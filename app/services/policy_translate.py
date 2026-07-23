"""제도 번역기 — 제도 정보를 가족돌봄청년이 이해할 수 있는 말로 풀어준다.

1차 버전은 "DB에서 제도 하나 읽어 → 프롬프트에 그대로 넘겨 → 풀이 문장 하나 받기"가 전부다.
질문(question) 반영, 서류 이력 대조, 자부담금 자동 문구는 아직 없다.
붙일 자리는 build_user_content()의 확장 지점 주석을 참고.
"""
import logging
from typing import Any, Dict, Optional

from app import db, llm, prompts

logger = logging.getLogger(__name__)

PROMPT_FILE = "policy_translate.md"

# 풀이에 쓰지 않는 컬럼.
# original_notice는 공고 원문 전체라 토큰만 잡아먹고, 나머지는 내부 식별자/운영 메타다.
_SKIP_COLUMNS = frozenset(
    {"policy_id", "agency_id", "external_ref", "original_notice", "last_checked_at"}
)

# 컬럼명 그대로 넘기면 LLM이 의미를 잘못 잡는 게 있어서 한국어 라벨을 붙인다.
# 여기 없는 컬럼은 컬럼명을 그대로 쓴다 (DB에 컬럼이 추가돼도 자동으로 따라간다).
_LABELS = {
    "policy_name": "제도명",
    "agency_name": "운영기관",
    "summary": "지원내용",
    "support_period": "지원기간/금액",
    "cost": "자부담금",
    "duration": "소요기간",
    "application_method": "신청방법",
    "application_region": "신청가능지역",
    "schedule_type": "모집방식",
    "age_min": "최소연령",
    "age_max": "최대연령",
    "exception_age": "연령예외",
    "income_criteria": "소득기준",
    "qualification_text": "자격요건",
    "support_target": "지원대상",
    "duplication_restriction": "중복수혜제한",
    "deadline_type": "마감유형",
    "deadline_date_raw": "마감일(원문)",
    "application_deadline": "마감일",
    "result_note": "결과발표",
    "link": "링크",
    "contact": "문의처",
    "is_lifetime_limit_once": "생애1회한정",
    "info_reference_year": "정보기준연도",
    "notes": "비고",
}


async def fetch_policy(policy_id: int) -> Optional[Dict[str, Any]]:
    """policies 전체 컬럼 + 기관명. 없으면 None."""
    return await db.fetchrow(
        """
        SELECT p.*, a.agency_name
        FROM policies p
        JOIN agencies a ON a.agency_id = p.agency_id
        WHERE p.policy_id = $1
        """,
        policy_id,
    )


def build_user_content(row: Dict[str, Any]) -> str:
    """제도 행을 '라벨: 값' 텍스트 블록으로 만든다."""
    lines = []
    for key, value in row.items():
        if key in _SKIP_COLUMNS or value is None or value == "":
            continue
        lines.append("{}: {}".format(_LABELS.get(key, key), value))

    # --- 확장 지점 -----------------------------------------------------------
    # 나중에 여기에 덧붙일 것들:
    #   - 사용자 질문(question)      → "\n\n[사용자 질문]\n{question}"
    #   - 이미 제출한 서류 이력       → "\n\n[이미 가지고 있는 서류]\n..."
    #   - 자부담금 자동 안내 문구      → cost 값을 계산해서 별도 블록으로
    # 프롬프트 파일(policy_translate.md)에 대응 규칙을 추가하는 것도 잊지 말 것.
    return "[제도 정보]\n" + "\n".join(lines)


async def translate(row: Dict[str, Any]) -> str:
    """제도 행 하나를 풀이 문장으로 바꾼다."""
    system = prompts.load_app_prompt(PROMPT_FILE)
    messages = [{"role": "user", "content": build_user_content(row)}]
    explanation = await llm.complete_text(system, messages, effort="low")
    logger.info(
        "제도 번역 완료: policy_id=%s (%d자)", row.get("policy_id"), len(explanation)
    )
    return explanation
