"""대화 스레드 식별/소유권/삭제.

스레드의 상태는 LangGraph checkpointer(cb 스키마)에 있다. 별도의 세션 테이블을
두지 않는다 — 두 곳에 나눠 저장하면 반드시 어긋난다.

소유권도 그 상태 안의 user_id로 판정한다. thread_id는 추측 가능한 문자열이므로
토큰이 유효하더라도 남의 대화를 이어가거나 결과를 열어볼 수 없어야 한다.
"""
import logging
import uuid
from typing import Any, Dict, Optional

from app.cb import graph as cb_graph
from app.errors import Forbidden, ThreadNotFound

logger = logging.getLogger(__name__)


def new_thread_id() -> str:
    return "cb-" + uuid.uuid4().hex[:12]


async def get_state(thread_id: str) -> Optional[Dict[str, Any]]:
    """checkpointer에 저장된 State. 스레드가 없으면 None.

    한 번도 실행되지 않은 thread_id도 snapshot 자체는 돌아온다. 그때는
    values가 비어 있어서 '없음'과 구분되지 않으므로 user_id 유무로 판정한다.
    """
    snapshot = await cb_graph.graph().aget_state(
        {"configurable": {"thread_id": thread_id}}
    )
    values = dict(snapshot.values or {}) if snapshot else {}
    return values if values.get("user_id") is not None else None


async def require_owned(thread_id: str, user_id: int) -> Dict[str, Any]:
    """내 스레드일 때만 State를 돌려준다."""
    state = await get_state(thread_id)
    if state is None:
        raise ThreadNotFound()
    if int(state.get("user_id") or 0) != user_id:
        logger.warning(
            "스레드 소유자 불일치: thread=%s owner=%s requester=%s",
            thread_id, state.get("user_id"), user_id,
        )
        raise Forbidden("본인의 대화가 아닙니다.")
    return state


async def delete(thread_id: str, user_id: int) -> None:
    """'다시 시작'. 체크포인트를 지워서 대화를 처음부터 되돌린다.

    지우기 전에 소유권을 확인한다. 확인 없이 지우면 thread_id만 알면
    남의 대화를 날릴 수 있다.
    """
    await require_owned(thread_id, user_id)
    await cb_graph.checkpointer().adelete_thread(thread_id)
    logger.info("[cb] 스레드 삭제 thread=%s user=%s", thread_id, user_id)
