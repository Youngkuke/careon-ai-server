"""LangGraph 노드 구현.

노드는 State의 일부만 돌려준다. 병합은 LangGraph가 reducer로 처리한다
(3종 필터는 state.merge_tags로 합집합 누적된다).

대화 중에는 제도를 노출하지 않는다. 그래서 검색은 매 턴이 아니라 대화가
끝나는 시점에 한 번만 돌린다. gathering 턴에는 임베딩도 SQL도 없다.
"""
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.cb import cards, constants, eligibility, embedding, grading, prompts, search
from app.cb.config import cb_settings
from app.cb.state import CbState, active_filters, has_any_filter, merge_tags

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

# 대화 이력을 몇 **메시지**까지 프롬프트에 넣을지.
# 전부 넣으면 토큰이 계속 늘고, 오래된 화제가 query_text를 흐린다.
#
# 8 → 16 (2026-08-01). 봇의 발화도 messages에 남기기 시작하면서(_spoken) 한 턴이
# 메시지 두 개가 됐다. 그대로 두면 프롬프트가 보는 대화가 절반으로 줄어든다.
HISTORY_TURNS = 16


def _spoken(text: str) -> List[Any]:
    """봇이 한 말을 대화 이력에 남긴다.

    **지금까지 남기지 않고 있었다.** greet만 messages에 AIMessage를 넣고
    ask_intake·ask_narrow·converse·wrap_up은 answer만 돌려줬다. 그래서
    프롬프트에 들어가는 대화 이력(_history)에 사용자 발화만 있었고,
    _last_bot_text는 언제나 첫 인사만 돌려줬다 — '[직전에 네가 한 말]을 다시 쓰지
    마라'는 가드가 인사말과 비교하고 있었으니 아무것도 막지 못했다.

    이것이 같은 질문·같은 첫마디가 되풀이된 근본 원인이다. 봇은 자기가 방금
    무슨 말을 했는지 볼 수 없는 상태로 매 턴 문장을 새로 지어냈다.
    """
    from langchain_core.messages import AIMessage

    return [AIMessage(content=text)]

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
        # 같은 주제가 누구 몫으로 나온 것인지. theme의 부분집합이고, 어느 쪽인지
        # 불분명하면 양쪽 다 비운다 (그러면 지금까지와 똑같이 동작한다).
        "self_themes": {
            "type": "array",
            "items": {"type": "string", "enum": constants.THEME_TAGS},
        },
        "caree_themes": {
            "type": "array",
            "items": {"type": "string", "enum": constants.THEME_TAGS},
        },
        # 상태·등급. 해당한다고 밝힌 것과, 아니라고 밝힌 것을 나눠서 받는다.
        # 답하지 않은 것은 어느 쪽에도 넣지 않는다 (모름 ≠ 해당 없음).
        "conditions": {
            "type": "array",
            "items": {"type": "string", "enum": constants.CONDITION_TAGS},
        },
        "denied_conditions": {
            "type": "array",
            "items": {"type": "string", "enum": constants.CONDITION_TAGS},
        },
        "query_text": {"type": "string"},
        "ready": {"type": "boolean"},
        # 초반에 확정할 것들. 아직 모르면 null이다.
        "age": {"type": ["integer", "null"]},
        "caree_age": {"type": ["integer", "null"]},
        "target_for": {"type": ["string", "null"], "enum": constants.TARGET_FOR_VALUES + [None]},
        # 자격 축. conditions와 달리 '있다/없다'가 아니라 '정도'와 '구간'이다.
        # 사용자가 스스로 카테고리를 말하는 일은 드물고, 대개 우회 질문의 답에
        # 나온 앵커 제도명("자활근로 나가요")에서 역추론된다.
        "income_category": {
            "type": ["string", "null"],
            "enum": grading.INCOME_CATEGORIES + [None],
        },
        "income_category_evidence": {"type": "string"},
        "disability_severity_hint": {
            "type": ["string", "null"],
            "enum": grading.SEVERITY_HINTS + [None],
        },
        "disability_severity_evidence": {"type": "string"},
        # 사용자가 자발적으로 밝혔거나, 앵커 역추론(income_category)이 실패해
        # converse가 2단계로 대략의 액수를 물었을 때만 채워진다.
        "monthly_income": {"type": ["integer", "null"]},
        "household_size": {"type": ["integer", "null"]},
        # target_for의 근거가 된 사용자 발화 구절. 없으면 빈 문자열.
        #
        # 이 필드가 없을 때 LLM은 근거 없이 self를 채웠다. 실측(2026-07-27)에서
        # "25살이요"라는 나이 답변만으로 대상=self가 확정됐고, 바로 다음 턴에
        # "아버지가 거동이 불편하신데 돌봄이 고민이에요"가 와도 뒤집히지 않아
        # 아버지 돌봄 상담 내내 검색이 본인 기준으로 돌았다.
        # 근거를 함께 내놓게 하면 추측이 눈에 보이고, 코드가 걸러낼 수 있다.
        "target_for_evidence": {"type": "string"},
    },
    "required": ["life_cycle", "household", "theme",
                 "self_themes", "caree_themes",
                 "conditions", "denied_conditions", "query_text", "ready",
                 "age", "caree_age", "target_for", "target_for_evidence",
                 "income_category", "income_category_evidence",
                 "disability_severity_hint", "disability_severity_evidence",
                 "monthly_income", "household_size"],
    "additionalProperties": False,
}

# 사용자 발화가 이만큼 쌓이면 더 묻지 않고 검색으로 넘어간다.
#
# 6 → 8 (2026-07-31). 데모 목적이 '빠른 대화'가 아니라 '구체적인 상황을
# 최대한 파악해서 정확한 제도를 추천하는 것'으로 확정됐다. 6턴으로는
# 소득 1→2→3단계에 상태·등급 질문까지 넣을 예산이 안 나온다
# (nodes.needs_income_probe의 budget 계산 참고).
MAX_USER_TURNS = 8

# 대상/나이를 물어볼 수 있는 최대 횟수. 답을 피하는 사람을 붙잡아 두지 않는다.
# 본인만 찾을 때는 2가지(대상·본인 나이), 돌보는 분을 찾을 때는 3가지
# (+돌보는 분 연세)라 상한을 3으로 둔다. self로 끝나는 대화는 2회에서 멈춘다.
MAX_INTAKE_QUESTIONS = 3

# 첫 인사. LLM을 부르지 않는다 — 아직 아무 정보가 없어서 LLM이 더 나은 문장을
# 만들 수 없고, 앱을 열자마자 2~3초를 기다리게 할 이유도 없다.
#
# 주제를 예시로 들지 않는다. "월세·병원비·일자리처럼" 같은 예시는 답하기는
# 쉽게 만들지만, 사용자가 그 셋 중에서 고르게 만들어 정작 본인 고민이
# 그 밖에 있을 때 말을 꺼내지 못한다. 열린 질문 하나로만 연다.
GREETING = "안녕하세요, 여러분의 상황이 궁금해요! 요즘 어떤 게 고민이신가요?"

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
        "도움의 대상: %s" % target,
        "본인 나이: %s" % (state.get("age") or "(아직 모름)"),
        "돌보는 분 연세: %s" % (state.get("caree_age") or "(아직 모름)"),
        "생애주기: %s" % (", ".join(filters["life_cycle"]) or "(없음)"),
        "가구상황: %s" % (", ".join(filters["household"]) or "(없음)"),
        "관심주제: %s" % (", ".join(filters["theme"]) or "(없음)"),
        "  └ 본인 몫으로 나온 주제: %s" % (", ".join(state.get("self_themes") or []) or "(아직 모름)"),
        "  └ 돌보는 분 몫으로 나온 주제: %s" % (", ".join(state.get("caree_themes") or []) or "(아직 모름)"),
        # 어디가 어떻게 불편하신지. 대화에서 상태 표현이 잡혔으면 그 이름을 보여준다.
        # 안 보여주면 이미 말한 것을 또 묻는다.
        "확인된 몸 상태: %s" % (", ".join(sorted(
            eligibility.mentioned_disease_groups(" ".join(_user_texts(state)))))
            or "(아직 모름)"),
        "해당한다고 밝힌 자격: %s" % (", ".join(state.get("conditions") or []) or "(없음)"),
        "해당 없다고 밝힌 자격: %s" % (", ".join(state.get("denied_conditions") or []) or "(없음)"),
        "받고 있는 급여 구분: %s" % (state.get("income_category") or "(아직 모름)"),
        "장애 정도: %s" % (state.get("disability_severity_hint") or "(아직 모름)"),
        # 소득 파악이 어느 단계까지 왔는지 프롬프트가 보고 판단한다
        # (app/cb/prompts/converse.md의 '소득 파악은 단계적으로').
        # 안 보여주면 이미 답한 것을 또 묻는다.
        "가구 월소득: %s" % (state.get("monthly_income") or "(아직 모름)"),
        "가구원 수: %s" % (state.get("household_size") or "(아직 모름)"),
        "",
        "[고를 수 있는 값]",
        "생애주기: %s" % ", ".join(constants.LIFE_CYCLE_TAGS),
        "가구상황: %s" % ", ".join(constants.HOUSEHOLD_TAGS),
        "관심주제: %s" % ", ".join(constants.THEME_TAGS),
        "상태·등급: %s" % ", ".join(constants.CONDITION_TAGS),
        "급여 구분: %s" % ", ".join(grading.INCOME_CATEGORIES),
        "장애 정도: %s" % ", ".join(grading.SEVERITY_HINTS),
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
    # 주제의 축 귀속. 어휘는 theme과 같은 것을 쓴다.
    # 한 턴에 양쪽으로 동시에 온 값은 둘 다 버린다 — LLM이 가르지 못한 것이고,
    # 잘못 귀속시키면 멀쩡한 제도가 내려가는데 안 넣으면 종전과 같이 동작할 뿐이다.
    axis = {kind: constants.filter_to_vocabulary(raw.get(kind) or [], "theme")
            for kind in ("self_themes", "caree_themes")}
    ambiguous = set(axis["self_themes"]) & set(axis["caree_themes"])
    for kind, values in axis.items():
        update[kind] = [v for v in values if v not in ambiguous]
    for kind in ("conditions", "denied_conditions"):
        update[kind] = [v for v in (raw.get(kind) or [])
                        if v in constants.CONDITION_TAGS]
    # 같은 항목이 양쪽에 다 들어오면 '해당한다'를 믿는다. 해당하는데 아니라고
    # 읽으면 필요한 제도가 사라지지만, 반대는 목록이 조금 길어질 뿐이다.
    if update["conditions"]:
        update["denied_conditions"] = [v for v in update["denied_conditions"]
                                       if v not in update["conditions"]]
    query_text = (raw.get("query_text") or "").strip()
    update["query_text"] = query_text or _last_user_text(state)
    update["ready"] = bool(raw.get("ready"))

    target_for = _grounded_target(raw, state)
    if target_for is not None:
        update["target_for"] = target_for
    # 이번 턴에 새로 나온 값이 없으면 이미 확정된 값을 본다.
    effective_target = target_for or state.get("target_for")

    # 나이는 '한 번 확정되면 덮어쓰지 않는다'. 뒤 턴에서 LLM이 null을 돌려줘도
    # (대화 주제가 옮겨가면 흔히 그렇다) 이미 알아낸 값이 지워지면 또 묻게 된다.
    bands: List[str] = []
    age = _valid_age(raw.get("age"))
    if age is not None and state.get("age") is None:
        update["age"] = age
    caree_age = _valid_age(raw.get("caree_age"))
    if caree_age is not None and state.get("caree_age") is None:
        update["caree_age"] = caree_age

    grading_update = _grounded_grading(raw, state)
    update.update(grading_update)

    # 장애 정도가 확정됐다는 것은 장애 등록이 이미 전제됐다는 뜻이다.
    # '심한/심하지 않은 장애인'이라는 구분 자체가 등록장애인에게만 매겨진다.
    #
    # 두 필드가 따로 놀면 위험하다. 실측(2026-07-28)에서 "아버지가 장애인연금
    # 받고 계시고요"에 severity_hint='심한'은 잡혔는데 conditions는 비어 있어서,
    # 장애등록을 자격으로 거는 제도를 걸러낼 근거(eligibility.DENIABLE_GROUPS)가
    # 없는 상태로 검색이 돌았다.
    #
    # 근거 없는 hint는 _grounded_grading이 이미 버렸으므로, 여기까지 온 값은
    # 근거가 있는 것만이다 ('모름'은 severity_from_hint가 None을 돌려줘서 빠진다).
    if grading.severity_from_hint(grading_update.get("disability_severity_hint")):
        update["conditions"] = merge_tags(update.get("conditions"), ["장애등록"])
        # 앞에서 한 번 걸렀지만 여기서 conditions가 늘었으므로 다시 맞춘다.
        # "장애 등록은 안 했는데 장애인연금 받아요" 같은 모순은 위와 같은 규칙으로
        # '해당한다'를 믿는다 (아니라고 잘못 읽으면 필요한 제도가 사라진다).
        update["denied_conditions"] = [
            v for v in (update.get("denied_conditions") or [])
            if v not in update["conditions"]
        ]
        logger.info("[intent] 장애 정도(%s)가 확정되어 conditions에 장애등록을 함께 넣는다",
                    grading_update["disability_severity_hint"])

    # 나이를 알면 생애주기가 확정된다. 본인과 돌보는 분의 생애주기를 **둘 다** 넣는다.
    #
    # 전에는 돌봄 대상을 찾는 중이면 본인 생애주기를 일부러 뺐다. 그랬더니
    # 아버지 돌봄 상담에서 life_cycle=[노년]만 남아 결과 20건이 전부 노인
    # 의료 제도가 되고, 정작 '가족돌봄청년 자기돌봄비' 같은 본인용 제도가
    # 한 건도 나오지 않았다. 영케어러는 양쪽이 다 필요하다.
    for value in (update.get("age") or state.get("age"),
                  update.get("caree_age") or state.get("caree_age")):
        band = constants.life_cycle_for_age(value)
        if band:
            bands.append(band)
    if bands:
        update["life_cycle"] = merge_tags(update.get("life_cycle"), bands)

    logger.info("[intent] 신규태그=%s query=%r ready=%s age=%s 돌봄대상연세=%s 대상=%s 축=%s",
                {k: v for k, v in update.items()
                 if k in ("life_cycle", "household", "theme") and v},
                update["query_text"][:40], update["ready"],
                update.get("age", state.get("age")),
                update.get("caree_age", state.get("caree_age")),
                effective_target or "미상",
                {k: v for k, v in update.items()
                 if k in ("self_themes", "caree_themes") and v} or "미상")
    return update


def _grounded_target(raw: Dict[str, Any], state: CbState) -> Optional[str]:
    """근거가 있을 때만 도움의 대상을 확정하고, 근거가 있으면 갱신도 허용한다.

    두 가지를 동시에 막는다.

      1) 근거 없는 확정. LLM은 나이만 답한 턴에도 습관적으로 self를 채운다.
         근거 구절이 비어 있으면 아직 모르는 것으로 둔다 (그래야 ask_intake가 묻는다).
      2) 확정 뒤 못 바꾸는 문제. 대화 도중 "아버지 돌봄이 고민"으로 화제가
         옮겨가면 대상은 실제로 바뀐다. 근거가 새로 있으면 갱신한다.

    갱신에 근거를 요구하므로, 화제가 옮겨갈 때 LLM이 null을 돌려주는 것만으로는
    확정된 값이 지워지지 않는다.
    """
    target_for = raw.get("target_for")
    if target_for not in constants.TARGET_FOR_VALUES:
        return None

    evidence = (raw.get("target_for_evidence") or "").strip()
    current = state.get("target_for")
    if not evidence:
        if current is None:
            logger.info("[intent] 대상=%s 은 근거가 없어 확정하지 않는다", target_for)
        return None

    if current is not None and current != target_for:
        logger.info("[intent] 대상 갱신 %s → %s (근거=%r)", current, target_for, evidence[:30])
    return target_for


def _grounded_grading(raw: Dict[str, Any], state: CbState) -> Dict[str, Any]:
    """자격 축(소득 구간·장애 정도)을 근거가 있을 때만 확정한다.

    target_for와 같은 패턴이다. 근거 구절이 비어 있으면 확정하지 않는다 —
    이 값들은 사용자가 직접 말하기보다 앵커 제도명("자활근로 나가요")에서
    역추론되는 것이라, 근거를 함께 받지 않으면 LLM이 분위기로 채운 것과
    실제 답을 구별할 수 없다.

    한 번 확정된 값은 근거가 새로 있을 때만 바뀐다. 대화 주제가 옮겨가면
    LLM이 null을 돌려주는데, 그것만으로 지워지면 다시 물어야 한다.

    '모름'과 '해당없음'도 답이므로 그대로 저장한다. 저장해 두지 않으면 이미
    답한 것을 프롬프트가 '(아직 모름)'으로 보여주고 또 묻게 된다.
    """
    update: Dict[str, Any] = {}

    pairs = (
        ("income_category", "income_category_evidence", grading.INCOME_CATEGORIES),
        ("disability_severity_hint", "disability_severity_evidence",
         grading.SEVERITY_HINTS),
    )
    for field, evidence_field, allowed in pairs:
        value = raw.get(field)
        if value not in allowed:
            continue
        evidence = (raw.get(evidence_field) or "").strip()
        if not evidence:
            if state.get(field) is None:
                logger.info("[intent] %s=%s 은 근거가 없어 확정하지 않는다", field, value)
            continue
        current = state.get(field)
        if current is not None and current != value:
            logger.info("[intent] %s 갱신 %s → %s (근거=%r)",
                        field, current, value, evidence[:30])
        update[field] = value

    # 소득 숫자는 자발적 발화이거나 converse 2단계 질문의 답이다. 어느 쪽이든
    # 사용자가 말한 숫자만 온다. 한 번 확정되면 덮어쓰지 않는다.
    income = raw.get("monthly_income")
    if isinstance(income, int) and not isinstance(income, bool) \
            and 0 < income <= 100_000_000 and state.get("monthly_income") is None:
        update["monthly_income"] = income
    size = raw.get("household_size")
    if isinstance(size, int) and not isinstance(size, bool) \
            and 0 < size <= 20 and state.get("household_size") is None:
        update["household_size"] = size

    return update


def user_income_pct(state: CbState) -> Optional[int]:
    """사용자의 기준중위소득 %. 모르면 None.

    카테고리를 숫자 계산보다 우선한다. '차상위계층'은 행정이 확인해 준
    사실이지만, 월소득은 세전/세후·상여 포함 여부·가구원수 착오가 섞여
    사용자가 잘못 말하기 쉽다. 둘 다 있으면 확인된 쪽을 믿는다.
    """
    from_category = grading.user_income_pct(state.get("income_category"))
    if from_category is not None:
        return from_category
    return grading.pct_from_income(
        state.get("monthly_income"), state.get("household_size"))


def _valid_age(value: Any) -> Optional[int]:
    """LLM이 돌려준 나이를 걸러낸다.

    "몇 살쯤 되셨을까요"에 답을 안 했는데 추측해서 채우는 경우가 있고,
    돌보는 분 나이를 본인 나이로 잘못 넣는 경우도 있다. 범위 밖 값은 버린다.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 < value <= 120 else None


def missing_intake(state: CbState) -> Optional[str]:
    """이번에 확인할 차례인 항목. 다 확인했으면 None.

    대상을 가장 먼저 확인한다. 의료·돌봄 이야기가 나왔을 때 그게 본인 것인지
    돌보는 분 것인지에 따라 찾아야 할 제도가 통째로 갈리기 때문이다.
    나이를 먼저 물으면, 나이 답변에서 LLM이 대상을 넘겨짚고 넘어가버린다.
    """
    if state.get("target_for") is None:
        return "target_for"
    if state.get("age") is None:
        return "age"
    # 돌보는 분을 찾는 중이라면 그분의 연세가 생애주기를 정한다.
    if state.get("target_for") == constants.TARGET_CAREE and state.get("caree_age") is None:
        return "caree_age"
    return None


def intake_done(state: CbState) -> bool:
    """초반 확인이 끝났거나, 물어볼 만큼 물어봤는가."""
    if missing_intake(state) is None:
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


# 이번 턴에 무엇을 확인할지 LLM에게 알려주는 문구, 그리고 생성이 실패했을 때
# 대신 내보낼 고정 질문.
_INTAKE_ASK = {
    "target_for": "지금 찾는 도움이 본인을 위한 것인지, 돌보는 분을 위한 것인지",
    "age": "본인 나이",
    "caree_age": "돌보시는 분의 연세",
}
_INTAKE_FALLBACK = {
    "target_for": "지금 찾으시는 건 본인을 위한 건가요, 아니면 돌보시는 분을 위한 건가요?",
    "age": "실례지만 나이가 어떻게 되세요?",
    "caree_age": "돌보시는 분은 연세가 어떻게 되세요?",
}


async def ask_intake(state: CbState) -> Dict[str, Any]:
    """자유대화로 넘어가기 전에 대상과 나이를 확인한다.

    설문이 아니다. 한 턴에 하나만, 지금까지의 대화에 이어붙여서 묻는다.
    (딱딱한 순서로 물으면 기존 phase1~5 챗봇과 같아진다.)
    """
    missing = missing_intake(state) or "age"
    messages = [
        {"role": "system", "content": prompts.load("intake")},
        {"role": "system", "content": _known_block(state)},
        {"role": "system", "content": "[이번 턴에 확인할 것] %s" % _INTAKE_ASK[missing]},
    ]
    if state.get("intake_last_asked") == missing:
        # 사용자가 답하지 않고 다른 이야기를 했다. 같은 문장을 되풀이하면
        # 대화가 막힌 것처럼 보인다.
        messages.append({"role": "system", "content":
                         "[주의] 이미 한 번 물었다. 앞의 문장을 반복하지 말고 "
                         "훨씬 짧게 한 번만 더 권한 뒤, 몰라도 괜찮다고 덧붙여라."})
    # 인테이크도 매 턴 "방금 한 말을 받아준 뒤에 묻는다"고 지시받으므로
    # converse와 똑같이 같은 위로를 되풀이한다. 같은 장치를 건다.
    openings = _recent_bot_openings(state)
    opening_note = _opening_note(openings)
    if opening_note:
        messages.append(opening_note)
    messages += _history(state)

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

    text = _drop_repeated_opening(text, openings)
    if not text:
        text = _INTAKE_FALLBACK[missing]

    asked = int(state.get("intake_asked") or 0) + 1
    logger.info("[intake] %s 확인 질문 (%d/%d)", missing, asked, MAX_INTAKE_QUESTIONS)
    return {"answer": text, "messages": _spoken(text),
            "intake_asked": asked, "intake_last_asked": missing,
            "phase": "gathering"}


# 상태·등급을 물을 만한 주제. 이 주제의 제도는 등급·수급 여부로 자격이
# 갈리는 것이 많아서, 안 물으면 해당 없는 제도가 결과의 절반을 차지한다.
# 주거·일자리 같은 주제까지 넓히지 않는다 — 그쪽은 되묻는 값이 작다.
NARROW_THEMES = frozenset({"신체건강", "정신건강", "보호·돌봄"})


def needs_narrow(state: CbState) -> bool:
    """검색 직전에 상태·등급을 한 번 물어볼 차례인가.

    딱 한 번만 묻는다. 대화가 끝나갈 때 던지는 질문이라 여기서 되묻기를
    반복하면 결과를 못 보고 끝난다.
    """
    if state.get("narrow_asked"):
        return False
    # 이미 대화에서 나왔으면 물을 이유가 없다.
    if state.get("conditions") or state.get("denied_conditions"):
        return False
    return bool(NARROW_THEMES & set(state.get("theme") or []))


async def ask_narrow(state: CbState) -> Dict[str, Any]:
    """검색 직전, 자격을 가르는 상태·등급을 한 번 확인한다.

    "아프다"는 말 하나로 의료 제도 수백 건이 후보가 되는데, 그중 상당수는
    장기요양등급이나 장애등록이 신청 조건이다. 이걸 모르면 해당 없는 제도를
    함께 보여주게 되고, 사용자는 결국 목록을 직접 훑어야 한다.

    진단명은 묻지 않는다. 사용자가 먼저 말하면 그때 검색어에 실린다.
    """
    for_caree = state.get("target_for") == constants.TARGET_CAREE
    who = "돌보시는 분" if for_caree else "본인"

    # 장애가 이미 확인됐으면 등록 여부를 또 묻는 대신 '지금 받고 있는 지원'을
    # 묻는다. 자기 장애 정도나 급여 구분을 아는 사람은 드물지만, 받고 있는
    # 제도 이름은 대개 정확히 안다. 그 이름에서 정도와 소득 구간이 역추론된다
    # (app/cb/prompts/intent_extract.md의 앵커 제도 목록).
    disability_known = (
        "장애등록" in (state.get("conditions") or [])
        or "장애인" in (state.get("household") or [])
    )
    if disability_known:
        ask = ("%s이 지금 받고 계신 지원(활동지원서비스, 장애인연금, 장애수당 등)의 "
               "이름. 정도나 등급을 직접 묻지 말 것." % who)
    else:
        ask = "%s의 장기요양등급 또는 장애등록 여부" % who

    messages = [
        {"role": "system", "content": prompts.load("narrow")},
        {"role": "system", "content": _known_block(state)},
        {"role": "system", "content": "[이번 턴에 확인할 것] %s" % ask},
    ] + _history(state)

    try:
        resp = await embedding.with_retry(
            lambda: embedding.client().chat.completions.create(
                model=cb_settings.openai_model,
                messages=messages,
                temperature=0.4,
            ),
            label="narrow",
        )
        text = (resp.choices[0].message.content or "").strip()
    except Exception:  # noqa: BLE001
        logger.exception("[narrow] 생성 실패 — 고정 문구로 대체")
        text = ""

    if not text:
        if disability_known:
            text = ("찾기 전에 하나만 여쭤볼게요. 활동지원서비스나 장애인연금처럼 "
                    "%s이 지금 받고 계신 지원이 있으실까요? "
                    "이름이 정확하지 않아도 괜찮아요." % ("돌보시는 분" if for_caree else "본인"))
        elif for_caree:
            text = ("찾기 전에 하나만 여쭤볼게요. 돌보시는 분이 장기요양등급이나 "
                    "장애 등록을 받으셨을까요? 아직이시거나 모르시면 그렇게만 알려주셔도 돼요.")
        else:
            text = ("찾기 전에 하나만 여쭤볼게요. 혹시 장애 등록이나 기초생활수급에 "
                    "해당되실까요? 아니거나 모르시면 그렇게만 알려주셔도 돼요.")

    logger.info("[narrow] %s 확인 질문 (대상=%s)",
                "받는 지원(우회)" if disability_known else "상태·등급",
                "돌봄대상" if for_caree else "본인")
    return {"answer": text, "messages": _spoken(text),
            "narrow_asked": True, "phase": "gathering"}


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
    # 상태·등급까지 물어봤다면 그게 마지막 질문이었다. 사용자가 답한 턴에
    # LLM이 ready를 다시 false로 돌려도(질문에 답한 직후엔 흔히 그렇다)
    # 대화로 되돌아가지 않는다. 마지막이라고 해놓고 또 물으면 신뢰를 잃는다.
    if state.get("narrow_asked"):
        return True
    return bool(state.get("ready")) or user_turns(state) >= MAX_USER_TURNS


# 소득을 물어볼 수 있는 converse 턴의 상한. 1·2·3단계로 딱 한 번씩이다.
MAX_INCOME_PROBES = 3

# 각 단계에서 무엇을 물을지. converse.md의 '소득 파악은 단계적으로'와 짝이다.
_INCOME_STAGE_ASK = {
    1: "1단계 — 지금 받고 계신 지원의 이름 (앵커 제도명). 액수는 묻지 않는다.",
    2: "2단계 — 대략의 가구 소득. 1단계로 구간이 안 잡혔을 때만이다. "
       "정확한 액수를 요구하지 말고, 답하기 어려우면 넘어가도 된다고 반드시 덧붙인다.",
    3: "3단계 — 같이 사는 가족의 소득, 친척에게 받는 비정기적 도움(용돈 등), "
       "그 밖에 받고 있는 수급. 예/아니오로 닫히지 않게 열린 질문으로 부드럽게 묻는다.",
}

# 돌봄이 필요한 상태가 확인됐다는 표시. 이 중 하나라도 conditions에 있으면
# 장애 정도를 소득보다 먼저 확인한다 (2026-07-31 결정).
#
# 왜 순서를 바꾸는가: 중증도가 잡히면 eligibility.grading_adjust가 1.3배 가산과
# 0.7배 감점을 건다. 안 잡히면 그 배율이 **아예 작동하지 않는다.** 반면 소득은
# 못 잡아도 '모르면 배제하지 않는다'로 안전하게 넘어간다. 손실이 큰 쪽을 먼저 딴다.
CARE_CONDITIONS = frozenset({"장기요양등급", "장애등록"})


def _condition_ask(state: CbState) -> str:
    """어디가 어떻게 불편하신지. 검색 이전 단계에서 가장 값이 큰 한 턴이다.

    **이분법으로 묻지 않는다.** "신체적 장애세요, 정신적 장애세요?"는 답할 수
    있는 사람이 드물고, 답을 받아도 856건을 가르지 못한다 — 제도 원문은 그런
    상위 분류로 자격을 적지 않는다.

    대신 데이터에 실제로 있는 표현의 축으로 예를 든다. 856건 실측(2026-07-31):
      거동·보행  '휠체어' 12건 / '보행' 4건 / '지체장애' 4건 / '거동' 4건
      인지       '인지' 28건 / '치매' 10건
      정신       '정신' 40건 / '우울' 4건
      감각       '시각장애' 8건 / '청각장애' 6건 / '보청기' 5건
      치료 중인 병 '재활' 32건 / '희귀' 28건 / '난치' 25건 / '암' 18건
    이 축으로 답이 오면 eligibility.DISEASE_BOOST_GROUPS가 그대로 받아
    해당 제도를 맞춤 상단으로 올린다. 축을 벗어난 답도 손해는 없다 —
    query_text에 실려 검색어가 된다.
    """
    who = "돌보시는 분" if state.get("target_for") == constants.TARGET_CAREE else "본인"
    return (
        "어디가 어떻게 불편하신지 — %s의 몸 상태를 구체적으로 확인한다. "
        "일상에서 무엇이 어려운지를 묻고, 답하기 쉽게 예를 두세 개 곁들여라. "
        "예로 들 만한 것: 걷거나 움직이는 것이 힘드신지, 기억이나 판단이 예전 같지 "
        "않으신지, 눈이나 귀가 불편하신지, 계속 치료받고 계신 병이 있으신지. "
        "**'신체적 장애세요, 정신적 장애세요' 같은 이분법으로 묻지 마라** — "
        "답할 수 있는 사람이 드물고 그 답으로는 제도를 가를 수 없다. "
        "고르라고 하지 말고 열린 질문으로 묻고, 진단명을 모르셔도 괜찮다고 덧붙여라."
        % who
    )


def needs_condition_probe(state: CbState) -> bool:
    """어디가 어떻게 불편하신지를 한 번 물어볼 차례인가. 다른 어떤 질문보다 앞선다.

    실측(2026-07-31, 26세 사용자 / 81세 할아버지): "돌봄이랑 병원비인 것 같아"까지
    듣고 곧장 매칭으로 넘어갔다. 실제로는 지체장애·낙상 후유증이었는데 아무도
    묻지 않아서, 제도의 서비스 내용과 대조할 상태 정보가 슬롯에 없었다.
    그 결과 eligibility.disease_boost가 통째로 무동작이 됐고(대화에 질환어가
    하나도 없으면 가산도 감점도 하지 않는다), 노인 돌봄군이 주제 태그만으로
    뭉텅이로 올라왔다.

    이미 상태가 드러난 대화에서는 묻지 않는다. 판정은 순위에 실제로 쓰이는
    표('DISEASE_BOOST_GROUPS')로 한다 — 그 표에 걸리는 말이 나왔다면 순위가
    이미 움직인다는 뜻이고, 걸리지 않았다면 사용자가 무슨 말을 했든 검색에는
    아직 아무것도 실리지 않았다는 뜻이다.

    소득·등급보다 먼저다. 등급과 소득은 못 잡아도 '모르면 배제하지 않는다'로
    넘어가지만, 상태를 모르면 무엇을 찾아야 하는지 자체가 정해지지 않는다.
    """
    if state.get("condition_asked") or state.get("narrow_asked"):
        return False
    # 몸 상태를 물을 근거가 있는 주제인가. 상태·등급 질문과 같은 기준을 쓴다.
    if not (NARROW_THEMES & set(state.get("theme") or [])):
        return False
    if eligibility.mentioned_disease_groups(" ".join(_user_texts(state))):
        return False
    budget = MAX_USER_TURNS - (1 if needs_narrow(state) else 0)
    if user_turns(state) >= budget:
        return False
    return not _WANTS_RESULTS.search(_last_user_text(state))


def _severity_ask(state: CbState) -> str:
    """장애 정도를 알아내기 위한 앵커 질문. 무엇이 확인됐는지에 따라 다르게 묻는다.

    **장기요양등급만 확인된 경우에 '몇 급이세요'를 물으면 안 된다.**
    장애 정도('심한/심하지 않은')는 등록장애인에게만 매겨지는 구분이라,
    장기요양등급만 있는 어르신에게는 성립하지 않는 질문이다. 그때는 장애 등록
    자체가 되어 있는지를 지원 이름으로 함께 더듬는다.

    어느 쪽이든 등급·정도를 직접 묻지 않는다. 이용자 상당수가 자기 등급을
    정확히 모르고, 틀리게 답하면 그 답이 그대로 검색을 왜곡한다
    (app/cb/prompts/narrow.md의 우회 질문 원칙 그대로다).
    """
    who = "돌보시는 분" if state.get("target_for") == constants.TARGET_CAREE else "본인"
    if "장애등록" in (state.get("conditions") or []):
        return ("장애 정도 — %s이 지금 받고 계신 장애 관련 지원의 **이름** "
                "(활동지원서비스, 장애인연금, 장애수당 등). "
                "'중증이신가요', '몇 급이세요'라고 직접 묻지 마라. "
                "이름에서 시스템이 정도를 알아낸다." % who)
    return ("장애 정도 — %s이 장애 등록도 되어 있는지, 그리고 받고 계신 지원의 "
            "**이름**(장애인연금, 장애수당, 활동지원서비스 등). 등급을 직접 묻지 마라. "
            "장애 등록이 없으실 수도 있으니 단정하지 말고 부드럽게 확인한다." % who)


def needs_severity_probe(state: CbState) -> bool:
    """장애 정도를 한 번 확인할 차례인가. 소득 단계보다 앞선다.

    돌봄이 필요한 상태(장기요양등급·장애등록)가 이미 확인된 대화에서만 묻는다.
    그렇지 않은 대화에서는 장애 정도를 물을 근거 자체가 없다.

    실측(2026-07-31, 페르소나1): 사용자가 스스로 "장기요양등급은 받으셨어요"라고
    말해 needs_narrow가 꺼졌고, ask_narrow가 한 번도 돌지 않아 장애 정도를
    물을 자리가 사라졌다. 결과적으로 grading_adjust가 통째로 무동작이었다.

    딱 한 번만 묻는다(severity_asked). 등급·정도는 모르는 사람이 많아서
    되물어봐야 나올 것이 없다.
    """
    if state.get("severity_asked") or state.get("narrow_asked"):
        return False
    if state.get("disability_severity_hint") is not None:
        return False
    if not (CARE_CONDITIONS & set(state.get("conditions") or [])):
        return False
    budget = MAX_USER_TURNS - (1 if needs_narrow(state) else 0)
    if user_turns(state) >= budget:
        return False
    return not _WANTS_RESULTS.search(_last_user_text(state))


def _last_bot_text(state: CbState) -> str:
    """직전에 봇이 한 말. 같은 문장을 두 번 쓰지 않게 하려고 쓴다."""
    for message in reversed(state.get("messages") or []):
        role = getattr(message, "type", None) or getattr(message, "role", None)
        if role in ("ai", "assistant"):
            content = getattr(message, "content", None)
            if isinstance(content, str) and content.strip():
                return content.strip()
    return ""


# 봇이 이미 써먹은 첫마디를 몇 개까지 보여줄지.
_OPENING_HISTORY = 4
_SENTENCE_END = re.compile(r"[.!?…]|\n")


def _recent_bot_openings(state: CbState, limit: int = _OPENING_HISTORY) -> List[str]:
    """봇이 최근에 말문을 연 문장들.

    실측(2026-08-01, 배우자 간병 상담): 세 턴이 연달아
    "간병이 많이 힘드셨겠어요" / "간병이 많이 힘드시겠어요" / "간병이 많이
    힘드시겠어요"로 시작했다. 매 턴 같은 위로를 되풀이하면 듣는 쪽은 상담자가
    자기 말을 안 듣고 있다고 느낀다.

    [직전에 네가 한 말]로 전문을 이미 보여주고 있었는데도 막히지 않았다.
    그 블록은 '질문을 반복하지 마라'로 읽히고 첫마디는 그 지시의 사정권 밖에
    남는다. 첫 문장만 따로 떼어 목록으로 보여주면 그 표현을 피해 간다.

    프롬프트 규칙만으로는 안 되기 때문에 코드가 실제 문자열을 넣어준다
    (converse.md의 '한 문장으로만 받아준다'는 이미 있었지만 지켜지지 않았다).
    """
    out: List[str] = []
    for message in reversed(state.get("messages") or []):
        role = getattr(message, "type", None) or getattr(message, "role", None)
        if role not in ("ai", "assistant"):
            continue
        content = getattr(message, "content", None)
        if not isinstance(content, str) or not content.strip():
            continue
        opening = _SENTENCE_END.split(content.strip(), 1)[0].strip()
        if opening and opening not in out:
            out.append(opening)
        if len(out) >= limit:
            break
    return out


def _opening_note(openings: List[str]) -> Optional[Dict[str, str]]:
    """이미 써먹은 첫마디를 알려주고 되풀이를 막는 system 메시지.

    '받아주지 마라'가 아니다. 받아주되 **이번 턴에 새로 나온 말에만** 반응하라는
    것이다. 위로 자체를 금지하면 사용자가 무거운 이야기를 꺼낸 턴에도 질문만
    툭 나가서 더 나쁘다.
    """
    if not openings:
        return None
    return {"role": "system", "content":
            "[이미 써먹은 첫마디]\n%s\n"
            "이 문장들, 그리고 **같은 뜻을 다르게 쓴 말로도** 이번 턴을 시작하지 마라. "
            "'힘드시겠어요'를 '힘드실 것 같아요'로 바꾸는 것은 피한 것이 아니다.\n"
            "받아줄 말이 있으면 **사용자가 방금 새로 말한 내용에만** 반응한다. 다만:\n"
            "- 사용자가 나이·숫자·이름처럼 **사실만 답한 턴은 받아줄 거리가 아니다.** "
            "인사치레 없이 곧바로 질문으로 시작한다.\n"
            "- **이미 확보한 사실을 요약해서 되뇌지 마라.** "
            "'남편분께서 교통사고 후유증으로 지체장애 3급을 받으셨군요' 같은 복창은 "
            "새 정보가 아니라 앞 대화의 반복이다.\n"
            "대화 전체의 사정을 매 턴 다시 위로하면 상담자가 대사를 읽는 것처럼 들린다."
            % "\n".join("- %s" % o for o in openings)}


# 첫마디가 앞의 것과 '사실상 같은 말'인지 보는 문턱 (문자 bigram 자카드).
#
# 문자 단위가 아니라 **어간 단위**로 비교한다.
#
# 처음에는 문자 bigram을 썼는데 한국어에서 약하다. '힘드시겠어요'와 '힘드실
# 것 같아요'는 같은 말인데 어미가 달라 겹치는 bigram이 셋뿐이라 0.43으로 나왔다.
# 어간만 보면(힘드/간병/많이) 그대로 일치한다. search.py가 조사·어미를 떼려고
# 접두 부분문자열을 쓰는 것과 같은 발상이다.
#
# 실측(2026-08-01) — 어간 겹침 / 짧은 쪽 대비 비율:
#   "많이 힘드시겠어요"          / "남편분 간병이 많이 힘드실 것 같아요"  2/2  같은 말
#   "남편분 간병이 많이 힘드시군요" / "간병이 많이 힘드시겠어요"          3/3  같은 말
#   "지체장애 판정을 받으셨군요"   / "지체장애 3급 판정을 받으셨군요"      3/3  같은 말
#   "지체장애 판정을 받으셨군요"   / "간병이 많이 힘드시겠어요"           0/3  다른 말
#   "혹시 나이가 어떻게 되세요"    / "혹시 지금 받고 계신 지원이 있으신가요" 1/4  다른 말
_OPENING_CONTAINMENT = 0.6
# 상투어 하나가 겹친 것("혹시")을 같은 말로 보지 않기 위한 하한.
_OPENING_MIN_SHARED = 2
_OPENING_MIN_STEMS = 2
_OPENING_WORD = re.compile(r"[가-힣]{2,}")

# 이 문장이 무언가를 묻고 있는가.
#
# 물음표만으로는 안 된다. 이 봇의 존댓말 질문은 물음표 없이 끝나는 일이 많다
# ("...알려주시면 좀 더 정확하게 찾아드릴 수 있어요"). 실제로 물음표를 요구했더니
# 잘라야 할 첫마디가 그대로 남았다(2026-08-01 실측).
_ASKS = re.compile(r"[?？]|까요|세요|가요|나요|는지|은지|을지|주시겠|어떠|계실|있으실")


def _stems(text: str) -> set:
    """문장에서 어간 후보만 남긴다. 조사·어미는 앞 두 글자만 남기면 대개 떨어진다."""
    return {word[:2] for word in _OPENING_WORD.findall(text)}


def _drop_repeated_opening(text: str, openings: List[str]) -> str:
    """앞에서 쓴 것과 사실상 같은 첫마디를 잘라낸다.

    프롬프트로는 끝까지 막히지 않았다. [이미 써먹은 첫마디] 목록을 주고 '같은 뜻을
    다르게 쓴 말로도 시작하지 마라'고 명시한 뒤에도 "남편분 간병이 많이 힘드시군요"
    다음 턴이 "간병이 많이 힘드시겠어요"로 나왔다. 이 파일의 다른 규칙들과 같은
    처리를 한다 — 프롬프트로 부탁하고, 코드로 확인한다.

    자르지 않는 경우가 셋 있다. 셋 다 '반복을 한 번 허용하는 것'이 '질문을
    통째로 날리는 것'보다 낫다는 같은 판단이다.
      - 답변이 한 문장뿐이다 → 그 문장이 곧 질문이다.
      - 첫 문장이 무언가를 묻고 있다 → 인사치레가 아니라 이번 턴의 질문이다.
      - 잘라내고 남는 말에 묻는 것이 없다 → 질문을 잘라낸 것이다.
    """
    if not openings or not text:
        return text
    parts = _SENTENCE_END.split(text.strip(), 1)
    if len(parts) < 2:
        return text
    head, rest = parts[0].strip(), parts[1].strip()
    if not head or not rest:
        return text
    if _ASKS.search(head) or not _ASKS.search(rest):
        return text

    mine = _stems(head)
    if len(mine) < _OPENING_MIN_STEMS:
        return text
    for previous in openings:
        other = _stems(previous)
        if len(other) < _OPENING_MIN_STEMS:
            continue
        shared = len(mine & other)
        if shared < _OPENING_MIN_SHARED:
            continue
        if shared / min(len(mine), len(other)) >= _OPENING_CONTAINMENT:
            logger.info("[opening] 되풀이된 첫마디를 잘라낸다 (겹친 어간 %s): %r",
                        sorted(mine & other), head)
            return rest
    return text


# 사용자가 소득 이야기를 접겠다고 한 신호.
#
# '모르겠어요'는 여기 넣지 않는다. 그건 거절이 아니라 1단계의 정상적인 답이고
# (income_category='모름'), 오히려 2단계로 넘어가야 하는 경우다. 여기 담는 것은
# "말할 수는 있지만 말하지 않겠다"는 뜻의 표현뿐이다.
_DECLINES = re.compile(
    r"말(씀)?\s*(드리기|하기)?\s*(는|가|좀)?\s*(어렵|그렇|그래|곤란|싫)"
    r"|답(하기|변)\s*(는|가|이)?\s*(좀\s*)?어렵"
    r"|밝히고\s*싶지\s*않|알려드리기\s*(는\s*)?(좀\s*)?(어렵|그렇)"
    r"|그냥\s*넘어|넘어갈게|패스할|비밀")


def converse_focus(state: CbState) -> Tuple[str, Dict[str, Any]]:
    """이번 turn에 무엇을 물을지 **코드가** 정한다. (지시문, State 갱신분)

    전에는 converse만 초점 없이 돌았다. ask_intake와 ask_narrow는 '이번 턴에
    확인할 것'을 코드가 계산해서 넘겨주는데, converse는 프롬프트가 매 턴
    스스로 판단하게 두었다. 그 결과 이미 아는 것을 또 물었다.

    실측(2026-07-31, 페르소나1): 1턴에 "엄마가 치매에 뇌졸중 후유증까지 있어서
    돌봐드리는 게 힘들다"고 다 말했는데, 4턴과 5턴이 연달아 "어머님을
    돌보시면서 가장 부담되는 부분이 어떤가요?"로 나갔다. 관심주제가 이미
    두 개(보호·돌봄·생활지원) 잡혀 있어서 물어봐야 새로 얻을 것이 없는데도
    프롬프트의 예시 문장을 그대로 복사한 것이다.

    그래서 순서를 코드로 못 박는다. **이미 채워진 슬롯은 건너뛴다.**
      1. 관심주제가 비었다 → 무엇이 가장 부담되는지 (이때만 묻는다)
      2. 의료·돌봄 주제인데 몸 상태를 모른다 → 어디가 어떻게 불편하신지
      3. 돌봄 상태가 확인됐는데 장애 정도를 모른다 → 앵커 질문으로 정도 확인
      4. 소득 구간을 아직 못 잡았다 → 1·2·3단계 중 이번 차례
      5. 다 됐다 → 짧게 마무리

    2가 맨 앞인 이유는 나머지 전부가 그 답에 얹히기 때문이다. 상태를 모르면
    질환 가산(disease_boost)이 통째로 무동작이고, 등급 질문도 무엇을 물어야
    할지 정해지지 않는다.

    3이 4보다 앞이다. 중증도가 없으면 grading_adjust의 배율이 아예 작동하지
    않는데, 소득은 못 잡아도 '모르면 배제하지 않는다'로 넘어간다.
    """
    if not (state.get("theme") or []):
        return ("지금 가장 부담되는 것이 무엇인지. 분야를 일상어로 예를 들어라 "
                "('월세나 집 문제', '병원비'처럼)."), {}

    if needs_condition_probe(state):
        return _condition_ask(state), {"condition_asked": True}

    probes = int(state.get("income_probes") or 0)

    if needs_severity_probe(state):
        # 앵커 질문("지금 받고 계신 지원의 **이름**")은 소득 1단계와 사실상 같은
        # 질문이다. 소진 처리하지 않으면 바로 다음 턴에 "지금 받고 계신 지원이나
        # 수급이 있으신가요?"가 거의 같은 문장으로 또 나간다.
        #
        # 실측(2026-08-01, 배우자 간병 상담):
        #   봇 > 지금 받고 계신 장애 관련 지원이 있으신가요? 활동지원서비스나 장애인연금 같은…
        #   나 > 지체장애 3급을 받았어
        #   봇 > 혹시 지금 받고 계신 지원이나 수급 중인 혜택이 있으신가요? 이름만 알려주시면 돼요.
        # 2·3단계(대략의 액수, 가구 안팎의 소득)는 다른 질문이라 그대로 남는다.
        return _severity_ask(state), {"severity_asked": True,
                                      "income_probes": max(probes, 1)}

    wrap_up = ("그 외에 더 걸리는 것이 있는지 짧게 확인하고 마무리한다. "
               "이미 확보한 것은 다시 묻지 않는다.")

    # 이미 물었는데 사용자가 "말씀드리기 어렵다"고 접었다. 다음 단계로 넘어가지
    # 않는다 — 단계를 올리는 것도 캐묻는 것이다. 단계를 소진시켜 이후 턴에서도
    # 다시 열리지 않게 한다.
    #
    # 프롬프트에도 같은 규칙이 있었지만(converse.md '사용자가 답을 피하면 접는다')
    # 지켜지지 않았다. 실측(2026-07-31): "그건 좀 말씀드리기 어렵네요" 다음 턴에
    # 3단계 질문이 그대로 나갔다.
    if probes >= 1 and _DECLINES.search(_last_user_text(state)):
        logger.info("[converse] 사용자가 소득 이야기를 접었다 — 더 묻지 않는다")
        return ("소득 이야기는 접는다. 다시 묻지 말고, 짧게 받아준 뒤 %s" % wrap_up,
                {"income_probes": MAX_INCOME_PROBES})

    if income_unknown(state) and probes < MAX_INCOME_PROBES:
        stage = probes + 1
        return _INCOME_STAGE_ASK[stage], {"income_probes": stage}

    return wrap_up, {}


# 단계는 상한이지 지시가 아니다. 사용자가 답하지 않고 다른 이야기를 해도
# 단계는 올라가는데(income_unknown이 그대로라서), 그때 다음 단계 지시만 주면
# LLM은 앞 턴 문장을 그대로 되풀이한다.
_CONVERSE_NOTE = (
    "위는 '여기까지 물어도 된다'는 상한이지 반드시 그것을 물으라는 뜻이 아니다. "
    "**이미 대화에 나온 것은 절대 다시 묻지 마라.** 사용자가 앞에서 진단명이나 "
    "상황을 말했으면 그것을 다시 확인하는 질문을 만들지 마라. "
    "앞 턴에 물었는데 사용자가 답하지 않았다면 훨씬 짧게 한 번만 더 권하거나 접어라."
)


def income_unknown(state: CbState) -> bool:
    """소득 구간을 아직 못 잡았는가. 다음 단계로 넘어갈지 정한다.

    '모름'은 답이긴 하지만 구간을 잡아주지는 못한다 — user_income_pct도
    user_income_floor도 None을 돌려줘서 랭킹에 아무것도 싣지 못한다.
    그래서 '아직 못 잡은' 쪽으로 센다. 다음 단계는 같은 것을 되묻는 게 아니라
    액수라는 **다른 질문**으로 넘어가는 것이라 심문이 되지 않는다.

    '해당없음'은 다르다. 수급·차상위 어디에도 해당하지 않는다는 것은
    기준중위소득 50%를 넘는다는 뜻이라 그 자체가 쓸 수 있는 신호다.
    """
    if state.get("monthly_income") is not None:
        return False
    category = state.get("income_category")
    return category is None or category == grading.INCOME_CATEGORY_UNSURE


# 사용자가 "그만 묻고 찾아달라"고 한 신호. 이 말이 나오면 소득 질문을 접는다.
#
# LLM의 ready는 '충분히 들었다'와 '사용자가 그만 물으래'를 구분하지 못한다.
# 뒤쪽인데 한 턴 더 물으면 신뢰를 잃으므로, 좁은 표현만 골라서 원문으로 막는다.
# 넓게 잡을 이유가 없다 — 못 걸러도 질문 한 번을 더 하는 것뿐이고,
# 잘못 걸리면 물어볼 기회를 잃는다.
_WANTS_RESULTS = re.compile(
    r"찾아\s*(줘|주세요|주실|봐|봐요)|보여\s*(줘|주세요)|이제\s*(됐|그만)|그만\s*(물어|하고)")


def needs_income_probe(state: CbState) -> bool:
    """검색으로 넘어갈 때가 됐지만 소득을 한 번 더 물어볼 차례인가.

    소득 구간을 알면 결과 정확도가 크게 오른다. 그런데 1단계(앵커 우회질문)는
    자연스러운 대화 흐름에 얹혀 있어서, 사용자가 "잘 모르겠어요"라고 답하면
    그대로 검색으로 넘어가 버린다. 실측(2026-07-31)에서 2단계는 한 번도
    실행되지 않았다 — is_ready가 관심주제와 나이만으로 곧장 true가 된다.

    그래서 여기서만 검색을 미룬다. 미루는 데는 조건이 붙는다:

      - **1단계부터 보장한다.** 관심주제와 나이만으로 곧장 검색으로 가던
        대화도 소득 우회질문을 최소 한 번은 거친다 (2026-07-31 결정).
        그 전에는 "생활비가 빠듯해요 → 대상? → 나이? → 검색"처럼 소득을
        한 번도 안 묻고 끝나는 경로가 있었다.
      - **상태·등급 질문(ask_narrow)보다 먼저다.** 그쪽은 '마지막 질문'이라고
        말하고 나가므로, 그 뒤에 또 물으면 한 말을 뒤집는 것이 된다.
      - **턴 예산 안에서만.** MAX_USER_TURNS를 넘기면서까지 묻지 않는다.
        상태·등급 질문이 아직 남아 있으면 그 몫으로 한 턴을 비워 둔다.
      - 사용자가 그만 찾아달라고 했으면 묻지 않는다.
    """
    if state.get("narrow_asked") or not income_unknown(state):
        return False
    probes = int(state.get("income_probes") or 0)
    if probes >= MAX_INCOME_PROBES:
        return False
    budget = MAX_USER_TURNS - (1 if needs_narrow(state) else 0)
    if user_turns(state) >= budget:
        return False
    return not _WANTS_RESULTS.search(_last_user_text(state))


async def converse(state: CbState) -> Dict[str, Any]:
    """아직 정보가 부족할 때의 대화 턴.

    제도를 한 건도 언급하지 않는다. 검색도 돌지 않는다 —
    이 경로에는 임베딩 호출도 SQL도 없어서 응답이 빠르다.
    """
    messages = [
        {"role": "system", "content": prompts.load("converse")},
        {"role": "system", "content": _known_block(state)},
    ]

    # 이번 턴의 초점은 코드가 정한다 (ask_intake·ask_narrow와 같은 방식).
    focus, update = converse_focus(state)
    messages.append({"role": "system", "content":
                     "[이번 턴에 확인할 것] %s\n%s" % (focus, _CONVERSE_NOTE)})

    # 직전에 한 말을 그대로 보여주고 반복을 막는다. 대화 이력에 이미 들어 있지만
    # 이력 속의 한 줄로는 힘이 약하다 — 실측에서 자기가 방금 한 질문을 그대로
    # 다시 냈다. 따로 떼어 명시하면 그 문장을 피해 간다.
    previous = _last_bot_text(state)
    if previous:
        messages.append({"role": "system", "content":
                         "[직전에 네가 한 말] %s\n"
                         "이 문장을 다시 쓰지 마라. 같은 것을 또 묻지 마라." % previous})

    # 위 블록은 '질문을 반복하지 마라'로 읽혀서 첫마디는 그대로 남았다.
    # 말문 여는 문장만 따로 떼어 한 번 더 막는다.
    openings = _recent_bot_openings(state)
    opening_note = _opening_note(openings)
    if opening_note:
        messages.append(opening_note)

    logger.info("[converse] 초점=%s%s", focus[:34],
                " %s" % update if update else "")
    messages += _history(state)

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

    text = _drop_repeated_opening(text, openings)
    if not text:
        text = ("어떤 부분이 가장 힘드신가요? "
                "주거비, 병원비, 일자리처럼 지금 가장 마음에 걸리는 걸 알려주시면 찾아볼게요.")

    update.update({"answer": text, "messages": _spoken(text),
                   "asked_followup": True, "phase": "gathering"})
    return update


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
        limit=cards.SEARCH_LIMIT,
    )
    rows = _rerank(rows, state, query_text)
    logger.info(
        "[search] q=%r filters=%s region=%s 대상=%s → %d건",
        query_text[:40], filters, state.get("region_sgg"),
        state.get("target_for") or "미상", len(rows),
    )
    return {"candidates": rows}


def _rerank(rows: List[Dict[str, Any]], state: CbState,
            query_text: str) -> List[Dict[str, Any]]:
    """SQL이 매긴 rrf에 대화에서만 알 수 있는 신호를 얹고 다시 세운다.

    SQL은 태그와 유사도까지만 안다. '누구를 위해 찾는지'와 '사용자가 어떤
    자격을 밝혔는지'는 대화에만 있어서 여기서 반영한다.
    """
    user_text = " ".join([query_text] + _user_texts(state))
    _apply_target_signal(rows, state.get("target_for"),
                         knows_caree=state.get("caree_age") is not None)
    # 대화에 나온 질환·상황이 원문에 적힌 제도를 끌어올린다. 아래 eligibility.apply
    # (질환 감점)와 짝이고 방향만 반대다. 감점보다 먼저 얹어도 결과는 같다 —
    # 둘 다 곱셈이고, 한 제도가 양쪽에 동시에 걸리는 일은 없다
    # (disease_penalty는 '대화에 안 나온' 질환에만 붙는다).
    rows = eligibility.disease_boost(rows, user_text)
    # ask_narrow가 마지막 한 턴을 써서 받아낸 답이다. 추정이 아니라 명시적
    # 답변이라 가장 확실한 신호인데, 지금까지 순위에 쓰이지 않고 있었다.
    rows = eligibility.condition_boost(rows, state.get("conditions") or [])
    # 본인 몫 주제로만 걸린 등록장애인 전용 제도를 내린다. 돌보는 분을 위해
    # 찾는 대화에서만 돈다 — 본인이 곧 등록장애인인 대화에서는 그 제도들이
    # 정확히 사용자 것이다.
    if state.get("target_for") == constants.TARGET_CAREE:
        rows = eligibility.axis_adjust(
            rows, state.get("self_themes") or [], state.get("caree_themes") or [])
    # 자격이 어긋나는 건은 여기서 목록에서 빠진다. 그래서 검색은 최종 노출
    # 건수보다 넉넉히 가져온다 (cards.SEARCH_LIMIT).
    rows = eligibility.apply(
        rows,
        user_text=user_text,
        user_household=state.get("household") or [],
        denied=state.get("denied_conditions") or [],
        conditions=state.get("conditions") or [],
    )
    # 확인된 자격 축(장애 정도·소득 구간)으로 올리고 내린다. 목록에서 빼지는
    # 않는다 — 제도 쪽 값의 61%가 카테고리명 사전 매핑에서 온 것이라
    # 제외까지 맡길 만큼 단단하지 않다 (eligibility.INCOME_OVER_FACTOR 주석).
    rows = eligibility.grading_adjust(
        rows,
        severity=grading.severity_from_hint(state.get("disability_severity_hint")),
        income_pct=user_income_pct(state),
        income_floor=grading.user_income_floor(state.get("income_category")),
    )
    rows.sort(key=lambda r: (-(r.get("rrf") or 0.0),
                             r.get("dist") if r.get("dist") is not None else 9.0))
    return rows


def _user_texts(state: CbState, turns: int = HISTORY_TURNS) -> List[str]:
    """최근 사용자 발화 원문들. 자격 언급("저 한부모예요")을 찾는 데 쓴다."""
    out: List[str] = []
    for message in (state.get("messages") or [])[-turns:]:
        role = getattr(message, "type", None) or getattr(message, "role", None)
        content = getattr(message, "content", None)
        if role in ("human", "user") and isinstance(content, str):
            out.append(content)
    return out


# 대상이 어긋나는 제도에 매길 감점 (rrf에 곱한다).
# 빼지 않고 낮추기만 한다. 확실히 틀렸다고 단정할 수 없는 신호라서,
# 사라지면 사용자가 찾을 방법이 없지만 내려가 있으면 아래에서 볼 수 있다.
TARGET_MISMATCH_FACTOR = 0.7

# 성인이 받을 수 없는 생애주기. 돌보는 분을 찾는 중일 때 이것'만' 달린 제도는
# 대상이 어긋난다.
_CHILD_ONLY = frozenset({"영유아", "아동", "청소년"})


def _apply_target_signal(rows: List[Dict[str, Any]],
                         target_for: Optional[str],
                         knows_caree: bool = False) -> List[Dict[str, Any]]:
    """도움의 대상이 어긋나는 제도를 뒤로 민다.

    영케어러는 본인 것과 돌보는 분 것을 함께 필요로 하므로 '청년 제도'를
    돌봄 문맥이라고 죽이면 안 된다 (가족돌봄청년 지원이 그렇게 사라진다).
    그래서 확실한 경우만 건드린다:

      - 돌보는 분을 찾는 중인데 영유아·아동·청소년 전용 제도인 경우
      - 본인을 찾는 중인데 노년 전용 제도인 경우.
        단 돌보는 분의 연세를 알고 있으면(knows_caree) 건드리지 않는다.
        본인 것을 우선하더라도 돌볼 어르신이 있다는 사실은 이미 확인됐고,
        그때 노인 제도를 깎으면 정작 필요한 제도가 밀린다.

    둘 다 '전용'일 때만이다. 태그가 여러 생애주기에 걸쳐 있으면 손대지 않는다.
    """
    if target_for not in constants.TARGET_FOR_VALUES:
        return rows

    for row in rows:
        tags = set(row.get("life_cycle_tags") or [])
        if not tags:
            continue
        if target_for == constants.TARGET_CAREE:
            mismatched = tags <= _CHILD_ONLY
        else:
            mismatched = tags == {"노년"} and not knows_caree
        if mismatched:
            row["rrf"] = float(row.get("rrf") or 0.0) * TARGET_MISMATCH_FACTOR
            row["target_mismatch"] = True

    # 정렬은 _rerank가 모든 신호를 얹은 뒤 한 번만 한다.
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
