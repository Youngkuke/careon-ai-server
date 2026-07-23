"""user_conversation_state 저장/조회 (api.md '챗봇 진행 상태').

⚠️ 현재 DB 스키마로는 쓰기가 불가능하다. settings.conversation_state_enabled로 막아둔다.

  user_conversation_state.active_policy_id : integer NOT NULL, FK → policies(policy_id)

명세는 "대화 중인 제도 ID. 없으면 0"이라고 하는데,
  - 0을 넣으면  → ForeignKeyViolationError (policy_id 최솟값이 1이라 0은 없는 제도)
  - NULL을 넣으면 → NotNullViolationError
라서 phase1(제도 선택 전) 상태를 저장할 방법이 아예 없다.

아래 코드는 **active_policy_id의 NOT NULL이 풀린 스키마**를 전제로 한다.
DB에는 NULL로 저장하고, API 응답에서만 명세대로 0으로 바꿔 내려준다 (FK는 그대로 유지).
스키마가 바뀌면 .env에 CONVERSATION_STATE_ENABLED=true만 넣으면 된다.
"""
import logging
from typing import Any, Dict, Optional

from app import db
from app.config import settings
from app.session_store import phase_number

logger = logging.getLogger(__name__)

# 명세상 "제도 없음"을 뜻하는 응답값. DB에는 NULL로 저장한다.
NO_ACTIVE_POLICY = 0


def _to_response(row: Dict[str, Any]) -> Dict[str, Any]:
    """DB 행 → api.md '챗봇 진행 상태 조회' 응답. NULL인 제도 ID는 0으로 바꾼다."""
    return {
        "conversation_state_id": row["conversation_state_id"],
        "carer_id": row["carer_id"],
        "current_phase": row["current_phase"],
        "active_policy_id": row["active_policy_id"] or NO_ACTIVE_POLICY,
        "updated_at": row["updated_at"],
    }


async def upsert(
    carer_id: int, current_phase: Any, active_policy_id: Optional[int] = None
) -> Optional[int]:
    """진행 상태를 저장하고 conversation_state_id를 돌려준다. 꺼져 있으면 None.

    carer_id에 UNIQUE 제약이 없어서 ON CONFLICT를 못 쓴다. 조회 후 갱신/삽입한다.
    (제약이 생기면 upsert 한 방으로 줄일 수 있다.)
    """
    if not settings.conversation_state_enabled:
        return None

    phase = phase_number(current_phase)
    row = await db.fetchrow(
        "SELECT conversation_state_id FROM user_conversation_state WHERE carer_id = $1",
        carer_id,
    )
    if row is not None:
        await db.execute(
            "UPDATE user_conversation_state "
            "SET current_phase = $1, active_policy_id = $2, updated_at = now() "
            "WHERE conversation_state_id = $3",
            phase,
            active_policy_id,
            row["conversation_state_id"],
        )
        return row["conversation_state_id"]

    created = await db.fetchrow(
        "INSERT INTO user_conversation_state "
        "(carer_id, current_phase, active_policy_id, updated_at) "
        "VALUES ($1, $2, $3, now()) RETURNING conversation_state_id",
        carer_id,
        phase,
        active_policy_id,
    )
    return (created or {}).get("conversation_state_id")


async def get(carer_id: int) -> Optional[Dict[str, Any]]:
    """진행 상태 조회. 없거나 기능이 꺼져 있으면 None."""
    if not settings.conversation_state_enabled:
        return None
    row = await db.fetchrow(
        "SELECT conversation_state_id, carer_id, current_phase, active_policy_id, updated_at "
        "FROM user_conversation_state WHERE carer_id = $1 "
        "ORDER BY updated_at DESC NULLS LAST, conversation_state_id DESC",
        carer_id,
    )
    return _to_response(row) if row else None


async def clear(carer_id: int) -> None:
    """진행 상태 초기화 (api.md '챗봇 진행 상태 초기화')."""
    if not settings.conversation_state_enabled:
        return
    await db.execute("DELETE FROM user_conversation_state WHERE carer_id = $1", carer_id)
    logger.info("챗봇 진행 상태 초기화: carer_id=%s", carer_id)
