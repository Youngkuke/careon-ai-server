"""API 요청/응답 스키마. API_SPEC_v1.md 형태를 그대로 따른다."""
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field

Phase = Union[int, str]  # 1,2,3,4,5,"matching",6,"done"


# --- 1. 세션 시작 -------------------------------------------------------------
class CreateSessionRequest(BaseModel):
    # 신원은 access_token의 sub에서 온다. 바디의 carer_id는 하위호환용으로만 받고,
    # 토큰과 다르면 403으로 거른다 (프론트가 다른 유저 ID를 보내는 사고를 잡기 위해).
    carer_id: Optional[int] = None
    age: Optional[int] = None
    region_sigungu: Optional[str] = None
    selected_types: List[str] = Field(default_factory=list)
    case_number: Optional[int] = None


class CreateSessionResponse(BaseModel):
    session_id: str
    # user_conversation_state에 행을 만들어야 채워진다.
    # 지금은 그 테이블에 쓸 수 없어서 null이다 (app/services/conversation_state.py 참고).
    conversation_state_id: Optional[int] = None
    current_phase: int
    active_policy_id: int = 0
    message: str


# --- 2. 대화 턴 ---------------------------------------------------------------
class MessageRequest(BaseModel):
    message: str


class MessageResponse(BaseModel):
    message: str
    phase: str  # "info_gathering" | "ready_to_match" (프론트 화면 제어용)
    current_phase: int
    active_policy_id: int = 0
    # 아래 둘은 프론트 계약에는 없는 보조 필드 (전환 애니메이션/디버깅용)
    phase_advanced: bool = False
    extracted_fields: Dict[str, Any] = Field(default_factory=dict)


# --- 3. 매칭 결과 조회 --------------------------------------------------------
class MatchItem(BaseModel):
    policy_id: int
    match_group: str  # "적합" | "확인_불가"
    # matched_policy에 저장했을 때만 채워진다.
    # match_group 컬럼이 DB에 생기기 전(MATCH_PERSIST_ENABLED=false)에는 null.
    matched_policy_id: Optional[int] = None


class MatchResponse(BaseModel):
    """화면에 뿌릴 제도 목록만. 판단 근거(reason/caution)는 내려주지 않는다.

    사용자는 제도 상세 화면에서 조건을 직접 확인하는 구조라 근거 텍스트가 불필요하고,
    부적합(40건 내외)은 화면에 노출하지 않으므로 제외한다.
    (원본은 세션에 그대로 남아 있어서 followup/로깅에서 쓴다.)
    """

    matches: List[MatchItem] = Field(default_factory=list)


# --- 5. 제도 상세 조회 --------------------------------------------------------
class RequiredDocument(BaseModel):
    document_id: int
    document_name: str


class PolicyTypeItem(BaseModel):
    policy_type_id: int
    type_name: str


class PolicyDetail(BaseModel):
    """화면 표시용 필드만. 매칭 판단에만 쓰는 컬럼(qualification_text 등)은 뺀다."""

    policy_id: int
    policy_name: Optional[str] = None
    agency_id: Optional[int] = None
    agency_name: Optional[str] = None
    category: Optional[str] = None
    summary: Optional[str] = None
    # 제도 대부분이 유형을 2개 이상 가져서 배열이다.
    policy_types: List[PolicyTypeItem] = Field(default_factory=list)
    support_period: Optional[str] = None
    cost: Optional[str] = None
    age_min: Optional[int] = None
    age_max: Optional[int] = None
    exception_age: Optional[str] = None
    application_method: Optional[str] = None
    deadline_type: Optional[str] = None
    deadline_date_raw: Optional[str] = None
    application_deadline: Optional[datetime] = None
    result_note: Optional[str] = None
    result_date: Optional[datetime] = None
    link: Optional[str] = None
    contact: Optional[str] = None
    is_lifetime_limit_once: Optional[bool] = None
    required_documents: List[RequiredDocument] = Field(default_factory=list)


class PolicyListResponse(BaseModel):
    policies: List[PolicyDetail] = Field(default_factory=list)


# --- 6. 제도 번역 -------------------------------------------------------------
class PolicyTranslateResponse(BaseModel):
    """제도 풀이 1차 버전. 지금은 풀이 텍스트 하나만 내려준다."""

    policy_id: int
    explanation: str


# --- 챗봇 진행 상태 (api.md) ---------------------------------------------------
class ConversationStateResponse(BaseModel):
    conversation_state_id: int
    carer_id: int
    current_phase: int
    active_policy_id: int = 0  # 대화 중인 제도 없으면 0
    updated_at: Optional[datetime] = None


class MessageOnlyResponse(BaseModel):
    message: str


# --- 7. 세션 상태 조회 --------------------------------------------------------
class SessionStateResponse(BaseModel):
    session_id: str
    phase: Phase
    profile: Dict[str, Any]
    conversation_history: List[Dict[str, str]]


# --- 8. 에러 공통 포맷 --------------------------------------------------------
class ErrorResponse(BaseModel):
    error: str
    message: str
