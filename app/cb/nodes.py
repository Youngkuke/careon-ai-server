"""LangGraph 노드 구현.

노드는 State의 일부만 돌려준다. 병합은 LangGraph가 reducer로 처리한다
(3종 필터는 state.merge_tags로 합집합 누적된다).
"""
import json
import logging
from typing import Any, Dict, List

from app.cb import constants, embedding, prompts, search
from app.cb.config import cb_settings
from app.cb.state import CbState, active_filters

logger = logging.getLogger(__name__)

# 대화 이력을 몇 턴까지 프롬프트에 넣을지.
# 전부 넣으면 토큰이 계속 늘고, 오래된 화제가 query_text를 흐린다.
HISTORY_TURNS = 8

_INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "life_cycle": {
            "type": "array",
            "items": {"type": "string", "enum": constants.LIFE_CYCLE_TAGS},
        },
        "household": {
            "type": "array",
            "items": {"type": "string", "enum": constants.HOUSEHOLD_TAGS},
        },
        "theme": {
            "type": "array",
            "items": {"type": "string", "enum": constants.THEME_TAGS},
        },
        "query_text": {"type": "string"},
    },
    "required": ["life_cycle", "household", "theme", "query_text"],
    "additionalProperties": False,
}

# 검색 결과를 몇 건까지 답변에 쓸지. 너무 많으면 답변이 목록 나열이 된다.
TOP_K = 5

# 0건일 때 푸는 순서. 가구상황을 먼저 푼다 —
# '저소득' 같은 값은 사용자가 스치듯 말해도 붙는데, 제도 쪽은 명시적으로
# 그 대상을 한정할 때만 태그가 달려 있어 교집합이 과하게 좁아진다.
# 관심주제는 사용자가 원하는 '분야' 자체라 가장 마지막에 푼다.
RELAX_ORDER = ("household", "life_cycle")


def _history(state: CbState, turns: int = HISTORY_TURNS) -> List[Dict[str, str]]:
    """대화 이력을 OpenAI 메시지 형식으로. 최근 것만."""
    out: List[Dict[str, str]] = []
    for message in (state.get("messages") or [])[-turns:]:
        role = getattr(message, "type", None) or getattr(message, "role", "")
        content = getattr(message, "content", "")
        if not isinstance(content, str) or not content.strip():
            continue
        out.append({
            "role": "user" if role in ("human", "user") else "assistant",
            "content": content,
        })
    return out


def _known_block(state: CbState) -> str:
    """이미 확보한 값을 알려줘서 같은 걸 또 묻거나 뒤집지 않게 한다."""
    filters = active_filters(state)
    lines = [
        "[이미 확보한 정보]",
        "생애주기: %s" % (", ".join(filters["life_cycle"]) or "(없음)"),
        "가구상황: %s" % (", ".join(filters["household"]) or "(없음)"),
        "관심주제: %s" % (", ".join(filters["theme"]) or "(없음)"),
        "",
        "[고를 수 있는 값]",
        "생애주기: %s" % ", ".join(constants.LIFE_CYCLE_TAGS),
        "가구상황: %s" % ", ".join(constants.HOUSEHOLD_TAGS),
        "관심주제: %s" % ", ".join(constants.THEME_TAGS),
    ]
    return "\n".join(lines)


async def extract_intent(state: CbState) -> Dict[str, Any]:
    """대화에서 3종 필터와 검색 질의문을 뽑는다.

    반환한 태그는 LangGraph reducer(merge_tags)가 기존 State에 합집합으로
    누적한다. 이 노드는 '이번에 새로 알아낸 것'만 돌려주면 된다.

    query_text는 누적하지 않고 매 턴 새로 쓴다. 누적하면 화제가 바뀌어도
    과거 관심사가 계속 섞여 검색이 흐려진다.
    """
    messages = [
        {"role": "system", "content": prompts.load("intent_extract")},
        {"role": "system", "content": _known_block(state)},
    ] + _history(state)

    try:
        resp = await embedding.with_retry(
            lambda: embedding.client().chat.completions.create(
                model=cb_settings.openai_model,
                messages=messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "cb_intent", "strict": True, "schema": _INTENT_SCHEMA,
                    },
                },
                temperature=0,
            ),
            label="intent",
        )
        raw = json.loads(resp.choices[0].message.content or "{}")
    except Exception:  # noqa: BLE001 — 추출 실패가 대화를 끊으면 안 된다
        logger.exception("[intent] 추출 실패 — 발화 원문으로 검색한다")
        return {"query_text": _last_user_text(state)}

    # enum으로 막아두긴 했지만 어휘 필터를 한 번 더 태운다.
    # strict 스키마가 지켜지지 않는 경우가 드물게 있고, 그때 CHECK 제약이
    # 아니라 검색 0건으로 조용히 나타나서 원인을 찾기 어렵다.
    update: Dict[str, Any] = {
        kind: constants.filter_to_vocabulary(raw.get(kind) or [], kind)
        for kind in ("life_cycle", "household", "theme")
    }
    query_text = (raw.get("query_text") or "").strip()
    update["query_text"] = query_text or _last_user_text(state)

    logger.info("[intent] 신규태그=%s query=%r",
                {k: v for k, v in update.items() if k != "query_text" and v},
                update["query_text"][:40])
    return update


async def ask_followup(state: CbState) -> Dict[str, Any]:
    """무엇을 찾는지 아직 모를 때 한 번 되묻는다."""
    messages = [
        {"role": "system", "content": prompts.load("followup")},
        {"role": "system", "content": _known_block(state)},
    ] + _history(state)

    try:
        resp = await embedding.with_retry(
            lambda: embedding.client().chat.completions.create(
                model=cb_settings.openai_model,
                messages=messages,
                temperature=0.4,   # 되묻는 말은 매번 똑같으면 기계적으로 들린다
            ),
            label="followup",
        )
        text = (resp.choices[0].message.content or "").strip()
    except Exception:  # noqa: BLE001
        logger.exception("[followup] 생성 실패 — 고정 문구로 대체")
        text = ""

    if not text:
        text = ("어떤 부분이 가장 힘드신가요? "
                "주거비, 병원비, 일자리처럼 지금 가장 마음에 걸리는 걸 알려주시면 찾아볼게요.")

    return {"answer": text, "asked_followup": True}


async def search_institutions(state: CbState) -> Dict[str, Any]:
    """하이브리드 검색을 실행한다 (app/cb/search.py).

    벡터·키워드·필터가 SQL 한 번에 처리되므로 여기서는 인자만 맞춰준다.
    relaxed_axes에 적힌 축은 이번 검색에서만 빼고 조회한다.
    """
    filters = active_filters(state)
    for axis in state.get("relaxed_axes") or []:
        filters[axis] = []
    region_keys = constants.region_keys_for_user(state.get("region_sgg"))
    query_text = (state.get("query_text") or "").strip()

    if not query_text:
        # extract_intent가 질의문을 못 만든 경우. 마지막 사용자 발화로 대신한다.
        query_text = _last_user_text(state)

    rows = await search.search(
        query_text,
        life_cycle=filters["life_cycle"] or None,
        household=filters["household"] or None,
        theme=filters["theme"] or None,
        region_keys=region_keys,
        limit=TOP_K,
    )
    logger.info(
        "[search] q=%r filters=%s region=%s → %d건",
        query_text[:40], filters, state.get("region_sgg"), len(rows),
    )
    return {"candidates": rows}


async def relax_filters(state: CbState) -> Dict[str, Any]:
    """0건일 때 필터를 한 단계 푼다. 한 번만 실행된다.

    누적된 태그 자체는 지우지 않는다. 사용자가 말한 사실("저는 청년이에요")은
    검색이 0건이라고 해서 거짓이 되지 않는다. 지워버리면 다음 턴에 다시
    물어봐야 하고, 누적 설계 자체가 무너진다.
    대신 relaxed_axes에 '이번 검색에서 뺄 축'만 적어 두고 search가 참고한다.

    관심주제(theme)는 풀지 않는다. 사용자가 '주거'를 물었는데 주거를 빼고
    아무거나 보여주면 검색이 아니라 소음이다. 그래도 0건이면 0건으로 답한다.
    """
    dropped: List[str] = [axis for axis in RELAX_ORDER if state.get(axis)]
    logger.info("[relax] 이번 검색에서 제외할 축: %s", dropped or "(없음)")
    return {"relaxed": True, "relaxed_axes": dropped}


def _institution_block(rows: List[Dict[str, Any]]) -> str:
    """검색 결과를 프롬프트에 넣을 텍스트로.

    본문을 통째로 넣으면 5건만 해도 수천 자가 되고, 정작 중요한 지원대상이
    뒤로 밀린다. 필드별로 잘라서 넣는다.
    """
    parts: List[str] = []
    for index, row in enumerate(rows, 1):
        where = "전국" if row.get("region_scope") == "national" else (
            row.get("sgg_nm") or row.get("ctpv_nm") or "지역")
        lines = [
            f"--- {index}. {row.get('serv_nm')} ({where}) ---",
            f"요약: {(row.get('serv_dgst') or '').strip()[:300]}",
        ]
        for label, key, limit in (
            ("지원대상", "target_detail", 400),
            ("서비스내용", "service_content", 500),
        ):
            value = (row.get(key) or "").strip()
            if value:
                lines.append(f"{label}: {value[:limit]}")
        tags = (row.get("life_cycle_tags") or []) + (row.get("theme_tags") or [])
        if tags:
            lines.append("분류: " + ", ".join(tags))
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _situation_block(state: CbState) -> str:
    filters = active_filters(state)
    lines = ["[사용자 상황]"]
    for label, key in (("생애주기", "life_cycle"), ("가구상황", "household"),
                       ("관심주제", "theme")):
        if filters[key]:
            lines.append(f"{label}: {', '.join(filters[key])}")
    if state.get("region_sgg"):
        lines.append(f"거주지: 서울 {state['region_sgg']}")
    if state.get("relaxed_axes"):
        korean = {"household": "가구상황", "life_cycle": "생애주기"}
        dropped = ", ".join(korean.get(a, a) for a in state["relaxed_axes"])
        lines.append(
            f"※ 조건에 딱 맞는 제도가 없어 {dropped} 조건을 빼고 다시 찾은 결과다. "
            "답변에서 이 사실을 솔직히 알려라."
        )
    return "\n".join(lines)


async def translate(state: CbState) -> Dict[str, Any]:
    """검색 결과를 사용자 상황에 맞춘 대화체 답변 하나로 만든다.

    제도별 개별 번역이 아니라 종합 답변 1회다. LLM 호출이 한 번이라 빠르고,
    제도 간 우선순위와 비교를 말해줄 수 있다.
    """
    rows = state.get("candidates") or []
    user_content = _situation_block(state)
    if rows:
        user_content += "\n\n[찾은 제도]\n" + _institution_block(rows)
    else:
        user_content += "\n\n[찾은 제도]\n(없음 — 조건에 맞는 제도를 찾지 못했다)"

    messages = [
        {"role": "system", "content": prompts.load("answer")},
    ] + _history(state) + [
        {"role": "user", "content": user_content},
    ]

    try:
        resp = await embedding.with_retry(
            lambda: embedding.client().chat.completions.create(
                model=cb_settings.openai_model,
                messages=messages,
                temperature=0.3,
            ),
            label="answer",
        )
        text = (resp.choices[0].message.content or "").strip()
    except Exception:  # noqa: BLE001 — 답변 생성 실패가 500으로 나가면 안 된다
        logger.exception("[translate] 답변 생성 실패")
        text = ""

    if not text:
        # LLM이 죽어도 검색 결과는 살아 있다. 제도명만이라도 돌려준다.
        if rows:
            names = ", ".join(r["serv_nm"] for r in rows[:3])
            text = f"이런 제도를 찾았어요: {names}. 자세한 설명을 준비하는 데 문제가 있어 다시 시도해 주세요."
        else:
            text = "조건에 맞는 제도를 찾지 못했어요. 어떤 도움이 필요하신지 조금 더 알려주시겠어요?"

    logger.info("[translate] 후보 %d건 → 답변 %d자", len(rows), len(text))
    return {"answer": text}


def _last_user_text(state: CbState) -> str:
    """가장 최근 사용자 발화 원문."""
    for message in reversed(state.get("messages") or []):
        role = getattr(message, "type", None) or getattr(message, "role", None)
        if role in ("human", "user"):
            content = getattr(message, "content", None)
            if isinstance(content, str):
                return content
    return ""
