"""LangGraph 노드 구현.

노드는 State의 일부만 돌려준다. 병합은 LangGraph가 reducer로 처리한다
(3종 필터는 state.merge_tags로 합집합 누적된다).

대화 중에는 제도를 노출하지 않는다. 그래서 검색은 매 턴이 아니라 대화가
끝나는 시점에 한 번만 돌린다. gathering 턴에는 임베딩도 SQL도 없다.
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.cb import cards, constants, embedding, prompts, search
from app.cb.config import cb_settings
from app.cb.state import CbState, active_filters, has_any_filter, merge_tags

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
        # 초반에 확정할 2가지. 아직 모르면 null이다.
        "age": {"type": ["integer", "null"]},
        "target_for": {"type": ["string", "null"], "enum": constants.TARGET_FOR_VALUES + [None]},
    },
    "required": ["life_cycle", "household", "theme", "query_text", "ready",
                 "age", "target_for"],
    "additionalProperties": False,
}

# 사용자 발화가 이만큼 쌓이면 더 묻지 않고 검색으로 넘어간다.
# 되묻기가 길어지면 사용자는 답만 계속 하고 결과를 못 본다.
MAX_USER_TURNS = 6

# 나이/대상을 물어볼 수 있는 최대 횟수. 답을 피하는 사람을 붙잡아 두지 않는다.
# 두 항목이니 각 1회씩이면 충분하다.
MAX_INTAKE_QUESTIONS = 2

# 첫 인사. LLM을 부르지 않는다 — 아직 아무 정보가 없어서 LLM이 더 나은 문장을
# 만들 수 없고, 앱을 열자마자 2~3초를 기다리게 할 이유도 없다.
GREETING = (
    "안녕하세요! 필요한 지원을 함께 찾아드릴게요.\n"
    "요즘 어떤 부분이 가장 부담되세요? 월세나 집 문제, 병원비, 일자리처럼 "
    "떠오르는 대로 편하게 말씀해 주세요."
)

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
    target = {
        constants.TARGET_SELF: "본인",
        constants.TARGET_CAREE: "돌보는 분",
    }.get(state.get("target_for") or "", "(아직 모름)")
    lines = [
        "[이미 확보한 정보]",
        "본인 나이: %s" % (state.get("age") or "(아직 모름)"),
        "도움의 대상: %s" % target,
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

    # 나이와 대상은 '한 번 확정되면 덮어쓰지 않는다'. 뒤 턴에서 LLM이 null을
    # 돌려줘도(대화 주제가 옮겨가면 흔히 그렇다) 이미 알아낸 값이 지워지면
    # 같은 걸 또 묻게 된다.
    target_for = raw.get("target_for")
    if target_for in constants.TARGET_FOR_VALUES and state.get("target_for") is None:
        update["target_for"] = target_for
    # 이번 턴에 새로 나온 값이 없으면 이미 확정된 값을 본다. LLM은 화제가
    # 옮겨가면 target_for를 null로 돌려주는데, 그걸 '본인 것'으로 읽으면
    # 돌봄 대상을 찾는 중에도 본인 생애주기가 붙는다.
    effective_target = update.get("target_for") or state.get("target_for")

    age = _valid_age(raw.get("age"))
    if age is not None and state.get("age") is None:
        update["age"] = age
        # 나이를 알면 생애주기가 확정된다. 본인 것을 찾는 경우에만 붙인다 —
        # 돌보는 분을 찾는 중이라면 본인 나이는 검색 대상이 아니다.
        if effective_target != constants.TARGET_CAREE:
            band = constants.life_cycle_for_age(age)
            if band:
                update["life_cycle"] = merge_tags(update.get("life_cycle"), [band])

    if effective_target == constants.TARGET_CAREE:
        # 돌보는 분을 찾는 중인데 본인 생애주기가 새로 끼어드는 것을 막는다.
        # 프롬프트로도 막아두었지만, 나이를 밝힌 턴에서 LLM이 습관적으로
        # 본인 생애주기를 함께 넣는 일이 실제로 반복됐다.
        own_band = constants.life_cycle_for_age(update.get("age") or state.get("age"))
        if own_band and own_band in (update.get("life_cycle") or []):
            # 이번 턴에 새로 들어오는 것만 막는다. 앞 턴에서 이미 누적된 태그는
            # reducer가 합집합으로 들고 있고, 사용자가 말한 사실이라 지우지 않는다.
            update["life_cycle"] = [t for t in update["life_cycle"] if t != own_band]
            logger.info("[intent] 돌봄 대상 문맥이라 본인 생애주기(%s)는 넣지 않는다", own_band)

    logger.info("[intent] 신규태그=%s query=%r ready=%s age=%s 대상=%s",
                {k: v for k, v in update.items()
                 if k in ("life_cycle", "household", "theme") and v},
                update["query_text"][:40], update["ready"],
                update.get("age", state.get("age")),
                update.get("target_for", state.get("target_for")))
    return update


def _valid_age(value: Any) -> Optional[int]:
    """LLM이 돌려준 나이를 걸러낸다.

    "몇 살쯤 되셨을까요"에 답을 안 했는데 추측해서 채우는 경우가 있고,
    돌보는 분 나이를 본인 나이로 잘못 넣는 경우도 있다. 범위 밖 값은 버린다.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 < value <= 120 else None


def intake_done(state: CbState) -> bool:
    """초반 2가지(나이·대상)를 확인했거나, 물어볼 만큼 물어봤는가."""
    if state.get("age") is not None and state.get("target_for") is not None:
        return True
    return int(state.get("intake_asked") or 0) >= MAX_INTAKE_QUESTIONS


async def greet(state: CbState) -> Dict[str, Any]:
    """대화를 열면서 봇이 먼저 건네는 인사.

    사용자가 첫 마디를 꺼내야 봇이 반응하면, 무엇을 말해야 하는 화면인지
    알기 어렵다. 인사와 함께 첫 질문을 같이 던진다.

    고정 문구라 LLM도 DB도 타지 않는다. 응답이 즉시 돌아간다.
    """
    from langchain_core.messages import AIMessage

    logger.info("[greet] 새 대화 시작 user=%s region=%s",
                state.get("user_id"), state.get("region_sgg"))
    # answer만 돌려주면 이 인사가 대화 이력에 남지 않아서, 다음 턴에 LLM이
    # 자기가 무엇을 물었는지 모른다. messages에도 넣는다.
    return {"answer": GREETING, "messages": [AIMessage(content=GREETING)],
            "phase": "gathering"}


async def ask_intake(state: CbState) -> Dict[str, Any]:
    """자유대화로 넘어가기 전에 나이와 대상을 확인한다.

    설문이 아니다. 한 턴에 하나만, 지금까지의 대화에 이어붙여서 묻는다.
    (딱딱한 순서로 물으면 기존 phase1~5 챗봇과 같아진다.)
    """
    missing = "age" if state.get("age") is None else "target_for"
    messages = [
        {"role": "system", "content": prompts.load("intake")},
        {"role": "system", "content": _known_block(state)},
        {"role": "system", "content": "[이번 턴에 확인할 것] %s" % (
            "본인 나이" if missing == "age" else
            "지금 찾는 도움이 본인을 위한 것인지, 돌보는 분을 위한 것인지")},
    ] + _history(state)

    try:
        resp = await embedding.with_retry(
            lambda: embedding.client().chat.completions.create(
                model=cb_settings.openai_model,
                messages=messages,
                temperature=0.4,
            ),
            label="intake",
        )
        text = (resp.choices[0].message.content or "").strip()
    except Exception:  # noqa: BLE001
        logger.exception("[intake] 생성 실패 — 고정 문구로 대체")
        text = ""

    if not text:
        text = ("실례지만 나이가 어떻게 되세요?" if missing == "age" else
                "지금 찾으시는 건 본인을 위한 건가요, 아니면 돌보시는 분을 위한 건가요?")

    asked = int(state.get("intake_asked") or 0) + 1
    logger.info("[intake] %s 확인 질문 (%d/%d)", missing, asked, MAX_INTAKE_QUESTIONS)
    return {"answer": text, "intake_asked": asked, "phase": "gathering"}


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
    # 나이·대상을 아직 확인 중이면 검색하지 않는다. 이 둘이 생애주기를
    # 좌우해서, 모른 채로 찾으면 엉뚱한 대상의 제도가 올라온다.
    if not intake_done(state):
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
    rows = _apply_target_signal(rows, state.get("target_for"))
    logger.info(
        "[search] q=%r filters=%s region=%s 대상=%s → %d건",
        query_text[:40], filters, state.get("region_sgg"),
        state.get("target_for") or "미상", len(rows),
    )
    return {"candidates": rows}


# 대상이 어긋나는 제도에 매길 감점 (rrf에 곱한다).
# 빼지 않고 낮추기만 한다. 확실히 틀렸다고 단정할 수 없는 신호라서,
# 사라지면 사용자가 찾을 방법이 없지만 내려가 있으면 아래에서 볼 수 있다.
TARGET_MISMATCH_FACTOR = 0.7

# 성인이 받을 수 없는 생애주기. 돌보는 분을 찾는 중일 때 이것'만' 달린 제도는
# 대상이 어긋난다.
_CHILD_ONLY = frozenset({"영유아", "아동", "청소년"})


def _apply_target_signal(rows: List[Dict[str, Any]],
                         target_for: Optional[str]) -> List[Dict[str, Any]]:
    """도움의 대상이 어긋나는 제도를 뒤로 민다.

    영케어러는 본인 것과 돌보는 분 것을 함께 필요로 하므로 '청년 제도'를
    돌봄 문맥이라고 죽이면 안 된다 (가족돌봄청년 지원이 그렇게 사라진다).
    그래서 확실한 경우만 건드린다:

      - 돌보는 분을 찾는 중인데 영유아·아동·청소년 전용 제도인 경우
      - 본인을 찾는 중인데 노년 전용 제도인 경우

    둘 다 '전용'일 때만이다. 태그가 여러 생애주기에 걸쳐 있으면 손대지 않는다.
    """
    if target_for not in constants.TARGET_FOR_VALUES:
        return rows

    for row in rows:
        tags = set(row.get("life_cycle_tags") or [])
        if not tags:
            continue
        mismatched = (
            tags <= _CHILD_ONLY if target_for == constants.TARGET_CAREE
            else tags == {"노년"}
        )
        if mismatched:
            row["rrf"] = float(row.get("rrf") or 0.0) * TARGET_MISMATCH_FACTOR
            row["target_mismatch"] = True

    # 감점했으면 순위가 달라진다. 다시 세운다.
    rows.sort(key=lambda r: (-(r.get("rrf") or 0.0),
                             r.get("dist") if r.get("dist") is not None else 9.0))
    return rows


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
