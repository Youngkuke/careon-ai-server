"""챗봇 검색엔진(cb) API.

기존 /api/v1/chat 과 완전히 분리된 계약이다. 공유하는 것은 인증(JWT)뿐이다.

  POST   /api/v1/cb/messages                        턴 진행
  DELETE /api/v1/cb/threads/{thread_id}             다시 시작

채팅 턴 응답에는 제도가 실리지 않는다. 카드는 결과 API에서만 나간다.
"""
import logging

from fastapi import APIRouter, Depends

from app.auth import get_current_carer_id
from app.cb import graph as cb_graph
from app.cb import threads
from app.cb.schemas import (
    Filters,
    MessageOnly,
    PHASE_GATHERING,
    ResultSummary,
    TurnRequest,
    TurnResponse,
)
from app.errors import CbUnavailable

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/cb", tags=["cb"])


def require_cb() -> None:
    """cb 초기화에 실패한 서버에서 500 대신 503을 내려준다.

    기존 /api/v1/chat 은 cb 없이도 동작해야 하므로 기동은 막지 않는다
    (app/main.py 참고). 그 대가로 여기서 상태를 확인한다.
    """
    if not cb_graph.ready():
        raise CbUnavailable()


@router.post("/messages", response_model=TurnResponse, dependencies=[Depends(require_cb)])
async def post_message(
    body: TurnRequest,
    carer_id: int = Depends(get_current_carer_id),
) -> TurnResponse:
    """대화 한 턴.

    thread_id가 없으면 새 대화를 시작하고 발급한 id를 응답에 실어준다.
    모르는 thread_id는 새로 만들지 않고 404다 — 클라이언트가 id를 지어내면
    소유권 판정이 무의미해진다.
    """
    if body.thread_id:
        state = await threads.require_owned(body.thread_id, carer_id)
        thread_id = body.thread_id
        region_sgg = state.get("region_sgg")
    else:
        thread_id = threads.new_thread_id()
        # 지역은 cb_user_profile에서 읽어온다(별도 커밋). 그 전까지는 전국+서울시로 검색한다.
        region_sgg = None

    result = await cb_graph.run_turn(thread_id, carer_id, body.message, region_sgg)

    summary = result.get("result_summary")
    return TurnResponse(
        thread_id=thread_id,
        phase=result.get("phase") or PHASE_GATHERING,
        message=result.get("answer") or "",
        filters=Filters(**(result.get("filters") or {})),
        result_summary=ResultSummary(**summary) if summary else None,
    )


@router.delete("/threads/{thread_id}", response_model=MessageOnly,
               dependencies=[Depends(require_cb)])
async def delete_thread(
    thread_id: str,
    carer_id: int = Depends(get_current_carer_id),
) -> MessageOnly:
    """다시 시작. 체크포인트를 지워 대화를 처음 상태로 되돌린다."""
    await threads.delete(thread_id, carer_id)
    return MessageOnly(message="대화가 초기화되었습니다.")
