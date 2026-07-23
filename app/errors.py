"""API_SPEC 8번 공통 에러 포맷."""
from fastapi import Request
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


class DatabaseUnavailable(ApiError):
    """DATABASE_URL 미설정/연결 실패. POLICY_NOT_FOUND로 오인되지 않게 분리한다."""

    status_code = 503
    code = "DATABASE_UNAVAILABLE"
    default_message = "제도 정보를 조회할 수 없습니다. 서버 DB 연결을 확인해주세요."


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.code, "message": exc.message},
    )
