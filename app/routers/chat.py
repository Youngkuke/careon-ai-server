"""챗봇 세션/대화 API."""
import logging

from fastapi import APIRouter, Depends

from app import db
from app.auth import get_current_carer_id
from app.config import settings
from app.errors import (
    ConversationStateNotFound,
    Forbidden,
    PhaseMismatch,
    SessionNotFound,
)
from app.profile import Profile
from app.schemas import (
    ConversationStateResponse,
    CreateSessionRequest,
    CreateSessionResponse,
    MatchResponse,
    MessageOnlyResponse,
    MessageRequest,
    MessageResponse,
    SessionStateResponse,
)
from app.services import chat, conversation_state, matching
from app.session_store import (
    PHASE_DONE,
    PHASE_MATCHING,
    phase_label,
    phase_number,
    store,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


def _owned(session, carer_id: int):
    """세션 주인이 아니면 403.

    session_id는 8자리 hex라 추측 가능성이 없진 않다. 토큰이 유효하더라도
    남의 세션을 열어보거나 이어서 대화할 수 없게 막는다.
    """
    if session.profile.carer_id != carer_id:
        logger.warning(
            "세션 소유자 불일치: session=%s owner=%s requester=%s",
            session.session_id,
            session.profile.carer_id,
            carer_id,
        )
        raise Forbidden("본인의 세션이 아닙니다.")
    return session


@router.post("/sessions", response_model=CreateSessionResponse, status_code=201)
async def create_session(
    body: CreateSessionRequest,
    carer_id: int = Depends(get_current_carer_id),
) -> CreateSessionResponse:
    """API_SPEC 1. 세션 시작 — 온보딩 완료 후 2단계 챗봇 시작."""
    if body.carer_id is not None and body.carer_id != carer_id:
        raise Forbidden("본인 계정으로만 세션을 만들 수 있습니다.")

    carer = await db.get_carer(carer_id)

    profile = Profile(
        carer_id=carer_id,
        age=body.age if body.age is not None else (carer or {}).get("age"),
        region_sigungu=body.region_sigungu or (carer or {}).get("region"),
        selected_types=body.selected_types,
        case_number=body.case_number,
        name=(carer or {}).get("name"),
    )

    # 온보딩(1단계)에서 이미 받은 값은 phase1에서 다시 묻지 않는다 (phase1 원칙 3)
    if carer:
        _seed_from_carer(profile, carer)
        for row in await db.get_cared(carer_id):
            profile.care_recipients.append(
                {
                    "relation_to_user": row.get("cared_relation"),
                    "condition_summary": row.get("condition_summary"),
                    "severity_level": row.get("severity_level"),
                    "status": "active",
                }
            )

    session = store.create(profile)
    reply = await chat.start_session(session)
    return CreateSessionResponse(
        session_id=session.session_id,
        conversation_state_id=await conversation_state.upsert(
            carer_id, session.current_phase
        ),
        current_phase=phase_number(session.current_phase),
        message=reply,
    )


def _seed_from_carer(profile: Profile, carer: dict) -> None:
    """carers 행에서 이미 채워진 컬럼을 프로필 필드로 옮긴다."""
    from app.db_mapping import CARER_COLUMN_TO_FIELD

    for column, field in CARER_COLUMN_TO_FIELD.items():
        value = carer.get(column)
        if value is not None:
            profile.fields[field] = value


@router.post("/sessions/{session_id}/messages", response_model=MessageResponse)
async def post_message(
    session_id: str,
    body: MessageRequest,
    carer_id: int = Depends(get_current_carer_id),
) -> MessageResponse:
    """API_SPEC 2. 대화 턴 진행 (phase1~5 loop)."""
    session = store.get(session_id)
    if session is None:
        raise SessionNotFound()
    _owned(session, carer_id)
    if session.current_phase in (PHASE_MATCHING, PHASE_DONE, 6):
        raise PhaseMismatch(
            "이미 정보 수집이 끝난 세션입니다 (phase={}).".format(session.current_phase)
        )

    reply, advanced, extracted = await chat.process_turn(session, body.message)
    if advanced:
        await conversation_state.upsert(carer_id, session.current_phase)
    return MessageResponse(
        message=reply,
        phase=phase_label(session.current_phase),
        current_phase=phase_number(session.current_phase),
        phase_advanced=advanced,
        extracted_fields=extracted,
    )


@router.post("/sessions/{session_id}/match", response_model=MatchResponse)
async def run_match(
    session_id: str,
    carer_id: int = Depends(get_current_carer_id),
) -> MatchResponse:
    """api.md '매칭 실행' — phase5 확인 완료 후 매칭을 돌리고 결과를 저장한다.

    matched_policy 저장과 carers.diagnosis_completed 갱신이라는 쓰기 부수효과가 있어서
    GET이 아니라 POST다.
    """
    session = store.get(session_id)
    if session is None:
        raise SessionNotFound()
    _owned(session, carer_id)
    if session.current_phase in (1, 2, 3, 4, 5):
        raise PhaseMismatch(
            "아직 정보 수집 중입니다 (phase={}). 대화가 끝난 뒤 호출하세요.".format(
                session.current_phase
            )
        )
    result = await chat.get_match_result(session)
    items = matching.to_match_items(result)

    # matched_policy.match_group 컬럼이 DB에 생긴 뒤에 켠다 (app/config.py 참고).
    # 켜기 전까지는 매칭 결과를 응답으로만 내려주고 저장하지 않는다.
    if settings.match_persist_enabled:
        saved = await matching.save_match_result(carer_id, items)
        for item in items:
            item["matched_policy_id"] = saved.get(item["policy_id"])
    else:
        logger.info(
            "MATCH_PERSIST_ENABLED=false — 매칭 결과 %d건을 저장하지 않고 응답만 반환합니다 "
            "(matched_policy.match_group 컬럼 배포 대기 중)",
            len(items),
        )

    return MatchResponse(matches=items)


@router.get("/state", response_model=ConversationStateResponse)
async def get_conversation_state(
    carer_id: int = Depends(get_current_carer_id),
) -> dict:
    """api.md '챗봇 진행 상태 조회'. 대화 본문은 메모리에만 있으므로 반환하지 않는다."""
    state = await conversation_state.get(carer_id)
    if state is None:
        raise ConversationStateNotFound()
    return state


@router.delete("/state", response_model=MessageOnlyResponse)
async def delete_conversation_state(
    carer_id: int = Depends(get_current_carer_id),
) -> dict:
    """api.md '챗봇 진행 상태 초기화'. 2단계 진단을 처음부터 다시 할 때 호출한다."""
    await conversation_state.clear(carer_id)
    return {"message": "챗봇 진행 상태가 초기화되었습니다."}


@router.get("/sessions/{session_id}", response_model=SessionStateResponse)
async def get_session(
    session_id: str,
    carer_id: int = Depends(get_current_carer_id),
) -> SessionStateResponse:
    """API_SPEC 7. 세션 상태 조회 (재접속/디버깅용)."""
    session = store.get(session_id)
    if session is None:
        raise SessionNotFound()
    _owned(session, carer_id)
    return SessionStateResponse(
        session_id=session.session_id,
        phase=session.current_phase,
        profile=session.profile.to_dict(),
        conversation_history=session.history,
    )
