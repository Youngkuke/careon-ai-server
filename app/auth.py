"""Spring 백엔드가 발급한 access_token 검증.

토큰을 발급하는 쪽은 Spring이고 AI 서버는 검증만 한다.
따라서 여기서 맞춰야 하는 건 전부 Spring(jjwt) 쪽 규약이다:

  - 알고리즘: HS512 (대칭키)
  - 서명 키: JWT_SECRET을 **Base64 디코드한 바이트**.
    Spring이 Keys.hmacShaKeyFor(Decoders.BASE64.decode(secret))로 키를 만들기 때문에,
    파이썬에서도 문자열이 아니라 디코드한 바이트를 넘겨야 서명이 맞는다.
  - sub: carer_id가 문자열로 들어있다 ("3")
  - type: "access" / "refresh". refresh 토큰으로는 API를 못 쓰게 막는다.
"""
import base64
import binascii
import logging
from typing import Optional

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.errors import Unauthorized

logger = logging.getLogger(__name__)

# auto_error=False: 헤더가 없을 때 FastAPI 기본 403 대신 우리 401 포맷으로 응답한다.
_bearer = HTTPBearer(auto_error=False)

ACCESS_TOKEN_TYPE = "access"

_signing_key: Optional[bytes] = None


def signing_key() -> bytes:
    """JWT_SECRET(Base64 문자열) → 서명 키 바이트."""
    global _signing_key
    if _signing_key is None:
        if not settings.jwt_secret:
            raise RuntimeError("JWT_SECRET이 설정되지 않았습니다 (.env 확인)")
        try:
            _signing_key = base64.b64decode(settings.jwt_secret, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise RuntimeError(
                "JWT_SECRET이 올바른 Base64 문자열이 아닙니다: {}".format(exc)
            )
    return _signing_key


def decode_token(token: str) -> dict:
    """서명/만료를 검증하고 payload를 돌려준다. 실패하면 Unauthorized."""
    try:
        return jwt.decode(token, signing_key(), algorithms=[settings.jwt_alg])
    except jwt.ExpiredSignatureError:
        logger.info("만료된 토큰")
        raise Unauthorized()
    except jwt.InvalidTokenError as exc:
        # 서명 불일치, 알고리즘 불일치, 형식 오류 등
        logger.info("유효하지 않은 토큰: %s", exc)
        raise Unauthorized()


async def get_current_carer_id(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> int:
    """Authorization: Bearer {access_token} → carer_id.

    라우터에서 `carer_id: int = Depends(get_current_carer_id)`로 받아 쓴다.
    요청 바디의 carer_id 대신 이 값을 신원으로 삼는다.
    """
    if credentials is None or not credentials.credentials:
        logger.info("Authorization 헤더 없음")
        raise Unauthorized()

    payload = decode_token(credentials.credentials)

    token_type = payload.get("type")
    if token_type != ACCESS_TOKEN_TYPE:
        logger.info("access 토큰이 아님 (type=%r)", token_type)
        raise Unauthorized()

    sub = payload.get("sub")
    try:
        # Spring은 sub를 문자열로 넣는다 ("3"). int 변환 실패 = 우리가 모르는 토큰.
        return int(sub)
    except (TypeError, ValueError):
        logger.info("sub를 carer_id로 해석할 수 없음: %r", sub)
        raise Unauthorized()
