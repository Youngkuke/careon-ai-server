"""LangGraph 노드 구현.

노드는 State의 일부만 돌려준다. 병합은 LangGraph가 reducer로 처리한다
(3종 필터는 state.merge_tags로 합집합 누적된다).

대화 중에는 제도를 노출하지 않는다. 그래서 검색은 매 턴이 아니라 대화가
끝나는 시점에 한 번만 돌린다. gathering 턴에는 임베딩도 SQL도 없다.
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from app.cb import cards, constants, embedding, prompts, search
from app.cb.config import cb_settings
from app.cb.state import CbState, active_filters, has_any_filter

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

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
        "ready": {"type": "boolean"},
    },
    "required": ["life_cycle", "household", "theme", "query_text", "ready"],
    "additionalProperties": False,
}

# 사용자 발화가 이만큼 쌓이면 더 묻지 않고 검색으로 넘어간다.
# 되묻기가 길어지면 사용자는 답만 계속 하고 결과를 못 본다.
MAX_USER_TURNS = 6

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
        return {"query_text": _last_user_text(state), "ready": False}

    # enum으로 막아두긴 했지만 어휘 필터를 한 번 더 태운다.
    # strict 스키마가 지켜지지 않는 경우가 드물게 있고, 그때 CHECK 제약이
    # 아니라 검색 0건으로 조용히 나타나서 원인을 찾기 어렵다.
    update: Dict[str, Any] = {
        kind: constants.filter_to_vocabulary(raw.get(kind) or [], kind)
        for kind in ("life_cycle", "household", "theme")
    }
    query_text = (raw.get("query_text") or "").strip()
    update["query_text"] = query_text or _last_user_text(state)
    update["ready"] = bool(raw.get("ready"))

    logger.info("[intent] 신규태그=%s query=%r ready=%s",
                {k: v for k, v in update.items()
                 if k not in ("query_text", "ready") and v},
                update["query_text"][:40], update["ready"])
    return update


def user_turns(state: CbState) -> int:
    """사용자가 지금까지 말한 횟수."""
    return sum(
        1 for m in (state.get("messages") or [])
        if (getattr(m, "type", None) or getattr(m, "role", None)) in ("human", "user")
    )


def is_ready(state: CbState) -> bool:
    """대화를 끝내고 검색으로 넘어갈 때인가.

    LLM의 판단(ready)을 그대로 믿지 않고 두 가지를 덧댄다:
      - 필터가 하나도 없으면 검색할 게 없다. 856건 중 아무거나 20건이 나온다.
      - 턴이 길어지면 LLM이 계속 아니라고 해도 넘어간다. 되묻기만 반복하면
        사용자는 답만 하고 결과를 못 본다.
    """
    if not has_any_filter(state):
        return False
    return bool(state.get("ready")) or user_turns(state) >= MAX_USER_TURNS


async def converse(state: CbState) -> Dict[str, Any]:
    """아직 정보가 부족할 때의 대화 턴.

    제도를 한 건도 언급하지 않는다. 검색도 돌지 않는다 —
    이 경로에는 임베딩 호출도 SQL도 없어서 응답이 빠르다.
    """
    messages = [
        {"role": "system", "content": prompts.load("converse")},
        {"role": "system", "content": _known_block(state)},
    ] + _history(state)

    try:
        resp = await embedding.with_retry(
            lambda: embedding.client().chat.completions.create(
                model=cb_settings.openai_model,
                messages=messages,
                temperature=0.4,   # 되묻는 말은 매번 똑같으면 기계적으로 들린다
            ),
            label="converse",
        )
        text = (resp.choices[0].message.content or "").strip()
    except Exception:  # noqa: BLE001
        logger.exception("[converse] 생성 실패 — 고정 문구로 대체")
        text = ""

    if not text:
        text = ("어떤 부분이 가장 힘드신가요? "
                "주거비, 병원비, 일자리처럼 지금 가장 마음에 걸리는 걸 알려주시면 찾아볼게요.")

    return {"answer": text, "asked_followup": True, "phase": "gathering"}


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
        limit=cards.RESULT_LIMIT,
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


def region_label_for_user(region_sgg: Any) -> str:
    """마무리 멘트에 쓸 지역 표기.

    자치구를 모르면 전국+서울시로만 검색하므로 멘트도 그대로 말한다.
    모르는 걸 아는 척하면 사용자가 자기 동네 제도가 다 나온 줄 안다.
    """
    sgg = (region_sgg or "").strip()
    return sgg if sgg in constants.SEOUL_GU else "전국·서울시"


async def wrap_up(state: CbState) -> Dict[str, Any]:
    """대화를 마치고 결과를 확정한다.

    멘트는 고정 템플릿이다. LLM을 쓰면 2~3초가 더 붙는데, 화면 전환 직전
    1~2초만 보이는 건수 보고 문구라 손해다.

    카드는 여기서 만들어 State에 넣어두고, 결과 API가 그대로 읽어간다.
    화면 전환 때 검색을 다시 돌리지 않는다.
    """
    rows = state.get("candidates") or []
    label = region_label_for_user(state.get("region_sgg"))
    matched, maybe = cards.split_sections(rows)

    if not matched and not maybe:
        # 0건이면 결과 화면으로 보내지 않는다. 빈 화면을 띄우는 것보다
        # 대화를 이어가면서 조건을 더 받는 편이 낫다.
        logger.info("[wrap_up] 0건 — 대화를 유지한다 (region=%s)", label)
        return {
            "answer": "조건에 맞는 제도를 찾지 못했어요. "
                      "어떤 도움이 가장 필요하신지 조금만 더 알려주시겠어요?",
            "phase": "gathering",
            "results": {},
            "result_summary": {},
        }

    if not matched:
        # 전부 '혹시관심'으로 내려간 경우. 건수를 맞춤 기준으로 세면 "0건 찾았어요"가
        # 되는데, 화면에는 카드가 있어서 말과 화면이 어긋난다.
        message = (f"딱 맞는 제도는 못 찾았지만, {label} 기준으로 "
                   f"관심 있으실 만한 걸 {len(maybe)}건 모아봤어요.")
    elif state.get("relaxed_axes"):
        message = (f"딱 맞는 건 없어서 조건을 조금 넓혔어요. "
                   f"{label} 기준 {len(matched)}건이에요.")
    else:
        message = f"이제 다 확인했어요! {label} 기준으로 맞춤 제도 {len(matched)}건을 찾았어요."

    results = {
        "generated_at": datetime.now(KST).isoformat(),
        "region_sgg": state.get("region_sgg"),
        "filters": active_filters(state),
        "relaxed_axes": list(state.get("relaxed_axes") or []),
        "matched": matched,
        "maybe": maybe,
    }
    summary = {"matched": len(matched), "maybe": len(maybe), "region_label": label}

    logger.info("[wrap_up] 맞춤 %d건 / 혹시관심 %d건 (region=%s, 완화=%s)",
                len(matched), len(maybe), label, state.get("relaxed_axes") or "없음")
    return {
        "answer": message,
        "phase": "ready",
        "results": results,
        "result_summary": summary,
    }


def _last_user_text(state: CbState) -> str:
    """가장 최근 사용자 발화 원문."""
    for message in reversed(state.get("messages") or []):
        role = getattr(message, "type", None) or getattr(message, "role", None)
        if role in ("human", "user"):
            content = getattr(message, "content", None)
            if isinstance(content, str):
                return content
    return ""
