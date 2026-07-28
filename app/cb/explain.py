"""제도 1건을 쉬운 말로 푸는 기능 + 개인화 설명(말풍선 A).

대화 그래프에서 떼어냈다. 대화는 제도를 노출하지 않고, 이 기능은 사용자가
결과 카드를 눌러 상세를 열었을 때만 호출된다. 그래서 그래프 노드가 아니라
독립 함수다 (기존 서비스의 제도번역기와 같은 위치).

# 말풍선 A는 왜 캐싱할 수 없나

apply_guide_easy(말풍선 B)나 required_documents_ai는 제도 원문만 보고 만들어서
856건을 미리 배치로 채워둘 수 있다. 말풍선 A는 대화 State를 함께 읽으므로
사용자마다 결과가 다르고, 같은 사용자도 대화가 진행되면 달라진다.
그래서 매 요청마다 새로 만든다 (2~4초). 프론트는 이 말풍선만 따로
비동기로 불러서 나머지 화면을 붙잡지 않아야 한다.

# 지어내지 않게 만드는 방식

LLM에게 State를 통째로 넘기지 않는다. 코드가 먼저 '이번 대화에서 실제로
확인된 것'만 골라 문장으로 만들고(confirmed_facts), 그 목록만 프롬프트에
넣는다. 모름·null·빈 값은 프롬프트에 아예 도달하지 않으므로 LLM이 그것을
언급할 방법이 없다. _source_block이 빈 필드를 빼는 것과 같은 원리다.
"""
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.cb import constants, embedding, grading, prompts
from app.cb.config import cb_settings

logger = logging.getLogger(__name__)

# 프롬프트에 넣을 필드와 자르는 길이.
# 원문을 통째로 넣으면 토큰이 커지고, 정작 중요한 지원대상이 뒤로 밀린다.
_SECTIONS = (
    ("제도명", "serv_nm", 200),
    ("한 줄 요약", "serv_dgst", 400),
    ("지원대상", "target_detail", 900),
    ("선정기준", "select_criteria", 900),
    ("서비스내용", "service_content", 900),
    ("신청방법", "apply_method", 700),
    ("담당기관", "jur_org_nm", 200),
    ("문의처", "contact", 200),
)


def _source_block(row: Dict[str, Any]) -> str:
    """비어 있는 필드는 아예 넣지 않는다.

    빈 문자열이나 "없음"을 넣으면 LLM이 "신청방법이 없습니다" 같은 잘못된
    단정을 만든다 (001_cb_schema.sql 주석의 원칙과 같다).
    """
    lines: List[str] = []
    for label, key, limit in _SECTIONS:
        value = (row.get(key) or "").strip()
        if value:
            lines.append(f"[{label}]\n{value[:limit]}")
    return "\n\n".join(lines)


# --- 말풍선 A: 개인화 설명 ------------------------------------------------------
# 개인화 설명이 읽는 제도 필드. 신청방법(apply_method)은 일부러 뺐다 —
# 신청 절차는 말풍선 B의 역할이고, 프롬프트에 넣으면 LLM이 결국 언급한다.
_PERSONAL_SECTIONS = (
    ("제도명", "serv_nm", 200),
    ("한 줄 요약", "serv_dgst", 400),
    ("서비스내용", "service_content", 1200),
    ("지원대상", "target_detail", 1200),
    ("선정기준", "select_criteria", 1200),
)

# '모름'류 값. 이 값들은 확인된 사실이 아니므로 프롬프트에 넣지 않는다.
_UNSURE = {
    grading.INCOME_CATEGORY_UNSURE,   # '모름'
    grading.SEVERITY_HINT_UNSURE,     # '모름'
    "", None,
}

_SEVERITY_LABEL = {
    grading.SEVERE: "장애의 정도가 심한 편",
    grading.MILD: "장애의 정도가 심하지 않은 편",
}


def _subject(state: Dict[str, Any]) -> str:
    """확인된 사실을 누구 이야기로 쓸지. target_for가 없으면 주어를 붙이지 않는다."""
    if state.get("target_for") == constants.TARGET_CAREE:
        return "돌보는 분"
    if state.get("target_for") == constants.TARGET_SELF:
        return "본인"
    return ""


def confirmed_facts(state: Dict[str, Any]) -> List[str]:
    """이번 대화에서 '실제로 확인된 것'만 사람이 읽는 문장으로 만든다.

    여기 담기지 않은 것은 프롬프트에 도달하지 않는다. 그래서 모름·미확인 값을
    LLM이 사실처럼 말할 수 없다.

    3종 태그(life_cycle/household/theme)는 넣지 않는다. 나이에서 시스템이
    자동으로 붙이는 값이 섞여 있어(intent_extract.md) '사용자가 말한 사실'과
    '시스템이 유도한 값'을 구분할 수 없기 때문이다. 확인된 사실로 쓰기에는
    근거가 약하다.
    """
    facts: List[str] = []
    who = _subject(state)

    target_for = state.get("target_for")
    if target_for == constants.TARGET_CAREE:
        facts.append("지금 돌보는 분을 위한 지원을 찾고 있음")
    elif target_for == constants.TARGET_SELF:
        facts.append("지금 본인을 위한 지원을 찾고 있음")

    age = state.get("age")
    if isinstance(age, int) and age > 0:
        facts.append(f"본인 나이: 만 {age}세")

    caree_age = state.get("caree_age")
    if isinstance(caree_age, int) and caree_age > 0:
        facts.append(f"돌보는 분 연세: 만 {caree_age}세")

    # 사용자가 '있다'고 답한 것. 없다고 답한 것과 섞지 않는다.
    for tag in state.get("conditions") or []:
        facts.append(f"{who + ' ' if who else ''}{tag}에 해당한다고 확인됨".strip())

    # 명시적으로 '아니다'라고 답한 것도 확인된 사실이다. 자격을 좁히는 근거가 된다.
    for tag in state.get("denied_conditions") or []:
        facts.append(f"{who + ' ' if who else ''}{tag}에는 해당하지 않는다고 답함".strip())

    income = state.get("income_category")
    if income not in _UNSURE:
        if income == grading.INCOME_CATEGORY_NONE:
            facts.append("기초생활보장·차상위 등 수급 자격에는 해당하지 않는다고 답함")
        else:
            facts.append(f"소득 구분: {income} 대상으로 확인됨")

    severity = grading.severity_from_hint(state.get("disability_severity_hint"))
    if severity in _SEVERITY_LABEL:
        facts.append(f"{who + ': ' if who else ''}{_SEVERITY_LABEL[severity]}")

    return facts


def _personal_source_block(row: Dict[str, Any], facts: List[str], state: Dict[str, Any]) -> str:
    """프롬프트 user 블록. 비어 있는 항목은 통째로 뺀다."""
    lines: List[str] = []
    for label, key, limit in _PERSONAL_SECTIONS:
        value = (row.get(key) or "").strip()
        if value:
            lines.append(f"[{label}]\n{value[:limit]}")

    if facts:
        lines.append("[이번 대화에서 확인된 사실]\n" + "\n".join(f"- {f}" for f in facts))

    query = (state.get("query_text") or "").strip()
    if query:
        lines.append(f"[지금 찾고 있는 것]\n{query[:300]}")

    return "\n\n".join(lines)


_PERSONAL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary_easy", "target_general", "personal_fit"],
    "properties": {
        "summary_easy": {"type": "string"},
        "target_general": {"type": "string"},
        # 확인된 사실과 제도 조건이 이어지지 않으면 빈 문자열.
        "personal_fit": {"type": "string"},
    },
}


async def personal_explain(
    row: Dict[str, Any], state: Optional[Dict[str, Any]] = None
) -> Tuple[Dict[str, Optional[str]], List[str]]:
    """말풍선 A. (섹션 3종, 근거로 쓴 확인된 사실 목록)을 돌려준다.

    state가 없으면(스레드를 안 넘겼거나 대화 전) 개인화 없이 1·2번만 만든다.
    확인된 사실이 하나도 없을 때도 마찬가지다 — 근거 없는 3번을 쓰느니
    아예 내보내지 않는다.

    실패하면 세 값이 모두 None인 dict를 돌려준다. 호출부가 화면을 결정한다.
    """
    facts = confirmed_facts(state or {})
    messages = [
        {"role": "system", "content": prompts.load("explain_personal")},
        {"role": "user", "content": _personal_source_block(row, facts, state or {})},
    ]
    try:
        resp = await embedding.with_retry(
            lambda: embedding.client().chat.completions.create(
                model=cb_settings.openai_model,
                messages=messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "personal_explain", "strict": True,
                        "schema": _PERSONAL_SCHEMA,
                    },
                },
                temperature=0.3,
            ),
            label="explain_personal servId=%s" % row.get("serv_id"),
        )
        raw = json.loads(resp.choices[0].message.content or "{}")
    except Exception:  # noqa: BLE001
        logger.exception("[explain] 개인화 설명 생성 실패 serv_id=%s", row.get("serv_id"))
        return {"summary_easy": None, "target_general": None, "personal_fit": None}, facts

    sections = {
        "summary_easy": _clean(raw.get("summary_easy")),
        "target_general": _clean(raw.get("target_general")),
        # 근거가 없으면 LLM이 빈 문자열을 준다. 그때는 섹션 자체를 내리지 않는다.
        "personal_fit": _clean(raw.get("personal_fit")) if facts else None,
    }
    logger.info(
        "[explain] 개인화 설명 serv_id=%s 확인된사실=%d개 personal_fit=%s",
        row.get("serv_id"), len(facts), "있음" if sections["personal_fit"] else "없음",
    )
    return sections, facts


def _clean(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


async def easy_text(row: Dict[str, Any]) -> str:
    """제도 원문 → 쉬운 말 설명. 실패하면 빈 문자열."""
    messages = [
        {"role": "system", "content": prompts.load("explain")},
        {"role": "user", "content": _source_block(row)},
    ]
    try:
        resp = await embedding.with_retry(
            lambda: embedding.client().chat.completions.create(
                model=cb_settings.openai_model,
                messages=messages,
                temperature=0.3,
            ),
            label="explain",
        )
        text = (resp.choices[0].message.content or "").strip()
    except Exception:  # noqa: BLE001
        logger.exception("[explain] 생성 실패 serv_id=%s", row.get("serv_id"))
        return ""

    logger.info("[explain] serv_id=%s → %d자", row.get("serv_id"), len(text))
    return text
