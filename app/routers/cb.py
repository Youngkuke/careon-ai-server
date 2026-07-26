"""챗봇 검색엔진(cb) API.

기존 /api/v1/chat 과 완전히 분리된 계약이다. 공유하는 것은 인증(JWT)뿐이다.

  POST   /api/v1/cb/threads                         대화 시작 (봇이 먼저 인사)
  POST   /api/v1/cb/messages                        턴 진행
  DELETE /api/v1/cb/threads/{thread_id}             다시 시작
  GET    /api/v1/cb/threads/latest                  마지막으로 마친 대화 (화면 복원)
  GET    /api/v1/cb/threads/{thread_id}/results     결과 카드 (③에서 추가)
  GET    /api/v1/cb/institutions/{serv_id}          제도 상세 (③)
  POST   /api/v1/cb/institutions/{serv_id}/translate 쉬운 말 설명 (③)

채팅 턴 응답에는 제도가 실리지 않는다. 카드는 결과 API에서만 나간다.
"""
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends

from app.auth import get_current_carer_id
from app.cb import cards, db as cb_db, explain
from app.cb import graph as cb_graph
from app.cb import profile as cb_profile
from app.cb import threads
from app.cb.schemas import (
    BannerSection,
    Filters,
    InstitutionCard,
    InstitutionDetail,
    Intake,
    LatestThreadResponse,
    MessageOnly,
    PHASE_GATHERING,
    PHASE_READY,
    ResultRegion,
    ResultSection,
    ResultsResponse,
    ResultSummary,
    ThreadStartResponse,
    TranslateResponse,
    TurnRequest,
    TurnResponse,
)
from app.errors import CbUnavailable, InstitutionNotFound, ResultsNotReady

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/cb", tags=["cb"])


def require_cb() -> None:
    """cb 초기화에 실패한 서버에서 500 대신 503을 내려준다.

    기존 /api/v1/chat 은 cb 없이도 동작해야 하므로 기동은 막지 않는다
    (app/main.py 참고). 그 대가로 여기서 상태를 확인한다.
    """
    if not cb_graph.ready():
        raise CbUnavailable()


@router.post("/threads", response_model=ThreadStartResponse, status_code=201,
             dependencies=[Depends(require_cb)])
async def start_thread(
    carer_id: int = Depends(get_current_carer_id),
) -> ThreadStartResponse:
    """대화 시작. 사용자가 첫 마디를 꺼내기 전에 봇이 먼저 인사한다.

    화면을 열자마자 부르면 된다. 고정 인사말이라 LLM을 타지 않는다.
    """
    thread_id = threads.new_thread_id()
    region_sgg = await cb_profile.region_sgg(carer_id)
    result = await cb_graph.start_thread(thread_id, carer_id, region_sgg)
    return ThreadStartResponse(thread_id=thread_id, message=result["answer"])


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
        # 대화 중에는 지역을 다시 읽지 않는다. 첫 턴에 State로 들어간 값이 끝까지 간다.
        region_sgg = state.get("region_sgg")
    else:
        thread_id = threads.new_thread_id()
        region_sgg = await cb_profile.region_sgg(carer_id)

    result = await cb_graph.run_turn(thread_id, carer_id, body.message, region_sgg)

    if result.get("phase") == PHASE_READY:
        # 새로고침·재로그인 뒤에 결과 화면으로 돌아올 수 있게 남긴다.
        # 기록에 실패해도 이번 응답은 정상이므로 대화를 끊지 않는다.
        try:
            await cb_profile.mark_ready(carer_id, thread_id)
        except Exception:  # noqa: BLE001
            logger.exception("[cb] 완료 대화 기록 실패 thread=%s user=%s", thread_id, carer_id)

    summary = result.get("result_summary")
    return TurnResponse(
        thread_id=thread_id,
        phase=result.get("phase") or PHASE_GATHERING,
        message=result.get("answer") or "",
        filters=Filters(**(result.get("filters") or {})),
        intake=Intake(
            target_for=result.get("target_for"),
            age=result.get("age"),
            caree_age=result.get("caree_age"),
        ),
        result_summary=ResultSummary(**summary) if summary else None,
    )


@router.get("/threads/latest", response_model=LatestThreadResponse,
            dependencies=[Depends(require_cb)])
async def get_latest_thread(
    carer_id: int = Depends(get_current_carer_id),
) -> LatestThreadResponse:
    """마지막으로 결과까지 마친 대화.

    화면을 다시 열었을 때 상담을 처음부터 시킬지, 지난 결과를 보여줄지
    프론트가 이걸로 정한다. 없으면 thread_id가 null인 200이다 — '아직 안 했다'는
    정상 상태라서 404가 아니다.

    라우트 순서 주의: /threads/{thread_id}보다 먼저 선언해야 'latest'가
    thread_id로 잡히지 않는다.
    """
    thread_id = await cb_profile.last_ready_thread(carer_id)
    if not thread_id:
        return LatestThreadResponse()

    state = await threads.get_state(thread_id)
    # 대화를 지웠는데 기록만 남은 경우. 없는 대화를 가리키느니 없다고 답한다.
    if state is None or int(state.get("user_id") or 0) != carer_id:
        logger.info("[cb] 기록된 완료 대화가 사라졌다 thread=%s user=%s", thread_id, carer_id)
        return LatestThreadResponse()

    results = state.get("results") or {}
    summary = state.get("result_summary") or {}
    return LatestThreadResponse(
        thread_id=thread_id,
        phase=state.get("phase"),
        generated_at=results.get("generated_at"),
        result_summary=ResultSummary(**summary) if summary else None,
    )


@router.get("/threads/{thread_id}/results", response_model=ResultsResponse,
            dependencies=[Depends(require_cb)])
async def get_results(
    thread_id: str,
    carer_id: int = Depends(get_current_carer_id),
) -> ResultsResponse:
    """결과 카드 목록.

    검색을 다시 돌리지 않는다. 대화가 끝날 때 wrap_up이 만들어 State에 넣어둔
    카드를 그대로 읽는다 — 화면 전환이 빠르고, 대화 종료 시점의 결과와
    화면이 어긋나지 않는다.
    """
    state = await threads.require_owned(thread_id, carer_id)
    if state.get("phase") != PHASE_READY:
        raise ResultsNotReady()

    results = state.get("results") or {}
    region_sgg = results.get("region_sgg")
    return ResultsResponse(
        thread_id=thread_id,
        generated_at=results["generated_at"],
        region=ResultRegion(
            sgg=region_sgg,
            source="user_profile" if region_sgg else "unset",
        ),
        filters=Filters(**(results.get("filters") or {})),
        relaxed_axes=list(results.get("relaxed_axes") or []),
        # 배너는 검색이 아니라 큐레이션이다. 큐레이션 목록을 채우기 전까지는
        # 빈 배열로 나가고, 프론트는 이 섹션을 숨기거나 기존 더미를 유지한다.
        banner=BannerSection(title="이런 지원도 받을 수 있어요", count=0, institutions=[]),
        matched=_section("맞춤 제도", results.get("matched")),
        maybe=_section("혹시 관심 있으실 수도", results.get("maybe")),
    )


def _section(title: str, raw_cards: Optional[List[Dict[str, Any]]]) -> ResultSection:
    items = [InstitutionCard(**card) for card in (raw_cards or [])]
    return ResultSection(title=title, count=len(items), institutions=items)


@router.get("/institutions/{serv_id}", response_model=InstitutionDetail,
            dependencies=[Depends(require_cb)])
async def get_institution(
    serv_id: str,
    carer_id: int = Depends(get_current_carer_id),
) -> InstitutionDetail:
    """제도 상세. 카드에서 뺀 긴 본문은 여기서만 내려간다."""
    row = await cb_db.fetch_institution(serv_id)
    if row is None:
        raise InstitutionNotFound()

    return InstitutionDetail(
        **cards.to_card(row),
        target_detail=_text(row.get("target_detail")),
        select_criteria=_text(row.get("select_criteria")),
        service_content=_text(row.get("service_content")),
        apply_method=_text(row.get("apply_method")),
        criteria_year=row.get("criteria_year"),
        extra_info=row.get("extra_info") or {},
    )


def _text(value: Optional[str]) -> Optional[str]:
    """빈 문자열은 null로 내려서 프론트가 섹션을 통째로 숨길 수 있게 한다."""
    text = (value or "").strip()
    return text or None


@router.post("/institutions/{serv_id}/translate", response_model=TranslateResponse,
             dependencies=[Depends(require_cb)])
async def translate_institution(
    serv_id: str,
    carer_id: int = Depends(get_current_carer_id),
) -> TranslateResponse:
    """제도 원문을 쉬운 말로 풀어준다.

    LLM 호출이 있어서 2~4초 걸린다. 상세 화면에서 사용자가 눌렀을 때만
    부르고, 목록에서 미리 불러두지 않는다.
    """
    row = await cb_db.fetch_institution(serv_id)
    if row is None:
        raise InstitutionNotFound()

    text = await explain.easy_text(row)
    if not text:
        # 생성 실패를 500으로 올리지 않는다. 원문은 상세 API로 이미 볼 수 있다.
        text = "쉬운 말 설명을 준비하지 못했어요. 잠시 후 다시 시도해주세요."

    return TranslateResponse(serv_id=serv_id, name=row.get("serv_nm") or "", easy_text=text)


@router.delete("/threads/{thread_id}", response_model=MessageOnly,
               dependencies=[Depends(require_cb)])
async def delete_thread(
    thread_id: str,
    carer_id: int = Depends(get_current_carer_id),
) -> MessageOnly:
    """다시 시작. 체크포인트를 지워 대화를 처음 상태로 되돌린다."""
    await threads.delete(thread_id, carer_id)
    return MessageOnly(message="대화가 초기화되었습니다.")
