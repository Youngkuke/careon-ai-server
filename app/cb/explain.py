"""제도 1건을 쉬운 말로 푸는 기능.

대화 그래프에서 떼어냈다. 대화는 제도를 노출하지 않고, 이 기능은 사용자가
결과 카드를 눌러 상세를 열었을 때만 호출된다. 그래서 그래프 노드가 아니라
독립 함수다 (기존 서비스의 제도번역기와 같은 위치).
"""
import logging
from typing import Any, Dict, List

from app.cb import embedding, prompts
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
