"""API_SPEC 8번 공통 에러 포맷."""
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    status_code = 400
    code = "BAD_REQUEST"
    default_message = "잘못된 요청입니다."

    def __init__(self, message: str = ""):
        super().__init__(message or self.default_message)
        self.message = message or self.default_message


class Unauthorized(ApiError):
    """토큰 없음/서명 불일치/만료/type 불일치 — 원인을 구분해서 알려주지 않는다.

    어떤 이유로 실패했는지 흘리면 토큰을 깎아볼 여지를 준다. 상세 원인은 서버 로그에만 남긴다.
    """

    status_code = 401
    code = "UNAUTHORIZED"
    default_message = "로그인이 필요합니다."


class Forbidden(ApiError):
    status_code = 403
    code = "FORBIDDEN"
    default_message = "접근 권한이 없습니다."


class SessionNotFound(ApiError):
    status_code = 404
    code = "SESSION_NOT_FOUND"
    default_message = "세션이 만료되었거나 존재하지 않습니다."


class ConversationStateNotFound(ApiError):
    status_code = 404
    code = "CONVERSATION_STATE_NOT_FOUND"
    default_message = "진행 중인 대화 상태가 없습니다."  # api.md 문구


class PhaseMismatch(ApiError):
    status_code = 409
    code = "PHASE_MISMATCH"
    default_message = "현재 단계에서는 호출할 수 없는 API입니다."


class PolicyNotFound(ApiError):
    status_code = 404
    code = "POLICY_NOT_FOUND"
    default_message = "해당 제도를 찾을 수 없습니다."  # api.md 문구


class CbUnavailable(ApiError):
    """cb 그래프 초기화 실패(DB/체크포인터). 기존 챗봇과 분리해서 알린다."""

    status_code = 503
    code = "CB_UNAVAILABLE"
    default_message = "챗봇 검색엔진을 사용할 수 없습니다. 잠시 후 다시 시도해주세요."


class ThreadNotFound(ApiError):
    """cb 대화 스레드가 checkpointer에 없다 (삭제됐거나 없는 id)."""

    status_code = 404
    code = "THREAD_NOT_FOUND"
    default_message = "대화를 찾을 수 없습니다. 새로 시작해주세요."


class ResultsNotReady(ApiError):
    """아직 정보 수집 중(phase=gathering)이라 결과가 없다."""

    status_code = 409
    code = "RESULTS_NOT_READY"
    default_message = "아직 대화가 진행 중입니다. 대화를 마친 뒤 결과를 볼 수 있습니다."


class InstitutionNotFound(ApiError):
    status_code = 404
    code = "INSTITUTION_NOT_FOUND"
    default_message = "해당 제도를 찾을 수 없습니다."


class DatabaseUnavailable(ApiError):
    """DATABASE_URL 미설정/연결 실패. POLICY_NOT_FOUND로 오인되지 않게 분리한다."""

    status_code = 503
    code = "DATABASE_UNAVAILABLE"
    default_message = "제도 정보를 조회할 수 없습니다. 서버 DB 연결을 확인해주세요."


class ValidationFailed(ApiError):
    """요청 본문/파라미터가 스키마에 안 맞는다.

    FastAPI 기본 응답은 {"detail": [...]} 라서 우리 오류 형식과 다르다.
    프론트 오류 파서가 이 한 가지 때문에 분기하지 않도록 형식만 맞춰준다.
    상태 코드는 422 그대로 둔다 — 400(우리가 직접 던지는 잘못된 요청)과
    구분되어야 서버 로그에서 '클라이언트가 계약을 어긴 요청'을 골라낼 수 있다.
    """

    status_code = 422
    code = "VALIDATION_ERROR"
    default_message = "요청 형식이 올바르지 않습니다."


# pydantic의 영문 msg를 그대로 내보내면 사용자 화면에 영어가 뜬다.
# 실제로 마주칠 만한 유형만 한국어로 옮기고 나머지는 기본 문구로 흡수한다.
_VALIDATION_REASONS = {
    "missing": "값이 필요합니다",
    "string_type": "문자열이어야 합니다",
    "int_type": "숫자여야 합니다",
    "int_parsing": "숫자여야 합니다",
}


def _validation_message(exc: RequestValidationError) -> str:
    """첫 번째 오류 하나만 문장으로. 전부 나열하면 말풍선이 길어진다."""
    errors = exc.errors() if hasattr(exc, "errors") else []
    if not errors:
        return ValidationFailed.default_message

    first = errors[0]
    if first.get("type") == "json_invalid":
        # 이때의 loc는 필드명이 아니라 깨진 위치의 문자 오프셋이라 쓸모가 없다.
        return "요청 본문이 올바른 JSON이 아닙니다."

    reason = _VALIDATION_REASONS.get(first.get("type", ""))
    # loc 앞머리('body', 'query', ...)는 사용자에게 의미가 없다.
    parts = [str(p) for p in (first.get("loc") or []) if p not in ("body", "query", "path")]
    field = ".".join(parts)

    if not field or not reason:
        return ValidationFailed.default_message
    return "{}: {}".format(field, reason)


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.code, "message": exc.message},
    )


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """FastAPI의 RequestValidationError → 공통 오류 형식.

    app/main.py에서 RequestValidationError 핸들러로 등록한다.
    """
    return await api_error_handler(request, ValidationFailed(_validation_message(exc)))
