"""챗봇 검색엔진(cb) 전용 설정.

기존 app/config.py를 건드리지 않으려고 별도 파일로 둔다.
기존 챗봇은 Anthropic을 쓰지만 cb는 OpenAI만 쓴다.
"""
import os
import re
from pathlib import Path
from typing import List, Optional
from urllib.parse import unquote

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent.parent

load_dotenv(ROOT_DIR / ".env")

# 퍼센트 인코딩 흔적(%2B, %3D ...). 디코딩된 키에는 절대 나타나지 않는다:
# 구형 키는 Base64(A-Za-z0-9+/=), 신형 키는 16진수라 둘 다 '%'를 안 쓴다.
# 따라서 '%XX'가 보이면 Encoding 키를 붙여넣은 것으로 단정해도 안전하다.
_PERCENT_ENCODED = re.compile(r"%[0-9A-Fa-f]{2}")


def _normalize_key(key: str) -> str:
    """Encoding 키를 붙여넣었으면 Decoding 형태로 되돌린다.

    httpx가 params로 넘길 때 한 번 더 인코딩하므로, Encoding 키를 그대로 두면
    '%'가 '%25'가 되어 인증이 깨진다. 증상이 '서비스키 인증 실패'로만 나와서
    원인을 찾기 어렵기 때문에 입력 단계에서 흡수한다.

    신형 64자 16진수 키는 인코딩/디코딩이 동일해서 이 함수를 그대로 통과한다.
    """
    return unquote(key) if _PERCENT_ENCODED.search(key) else key


def _key_list(plural_env: str, singular_env: str) -> List[str]:
    """콤마 구분 키 목록을 읽는다. 없으면 단일 키 환경변수로 대체한다.

    중복은 정규화 '후에' 제거한다. 같은 키의 Encoding/Decoding 두 형태를
    각각 등록해도 키 2개로 세지 않기 위해서다 (한도를 공유하므로 2개로 세면
    예산 추정이 두 배로 틀어진다).
    """
    raw = os.getenv(plural_env) or os.getenv(singular_env) or ""
    seen: List[str] = []
    for chunk in raw.split(","):
        key = _normalize_key(chunk.strip())
        if key and key not in seen:
            seen.append(key)
    return seen


class CbSettings:
    database_url: Optional[str] = os.getenv("DATABASE_URL") or None

    # cb 연결은 search_path를 cb로 고정한다.
    # pgvector를 cb 스키마에 설치했기 때문에 '<=>' 연산자도 cb 안에 있고,
    # 연산자는 타입과 달리 스키마 한정 표기로 해결되지 않는다 (search_path로만 해석).
    # public을 넣지 않는 것이 기존 테이블 오접근을 막는 안전장치이기도 하다.
    db_schema: str = "cb"

    # --- 공공데이터포털 ------------------------------------------------------
    # 개발계정은 API별로 일일 호출 한도가 있다. 운영계정 전환은 심사에 10~20일이
    # 걸려서, 팀원들이 각자 발급받은 키를 여러 개 모아 나눠 쓴다.
    #   WELFARE_CENTRAL_API_KEYS=key1,key2,key3   (콤마 구분, 권장)
    #   WELFARE_CENTRAL_API_KEY=key1              (단일 키, 하위호환)
    welfare_central_api_keys: List[str] = _key_list(
        "WELFARE_CENTRAL_API_KEYS", "WELFARE_CENTRAL_API_KEY"
    )
    welfare_local_api_keys: List[str] = _key_list(
        "WELFARE_LOCAL_API_KEYS", "WELFARE_LOCAL_API_KEY"
    )

    # 키 1개당 하루에 쓸 호출 수 상한. 실제 한도보다 약간 낮게 잡아 여유를 둔다.
    # 429를 맞고 나서 멈추는 것보다, 그 전에 다음 키로 넘어가는 편이 안전하다.
    key_daily_budget: int = int(os.getenv("WELFARE_KEY_DAILY_BUDGET", "95"))

    # data.go.kr은 동시 요청이 많으면 resultCode=99를 간헐적으로 뱉는다.
    # 낮게 잡고 재시도로 흡수한다.
    api_concurrency: int = int(os.getenv("WELFARE_API_CONCURRENCY", "3"))
    # 요청 시작 사이 최소 간격(초). 세마포어만으로는 응답이 빠를 때 초당 수십 건이
    # 나가서 일일 한도를 순식간에 태운다. 0.3초 ≈ 초당 3건.
    api_min_interval: float = float(os.getenv("WELFARE_API_MIN_INTERVAL", "0.3"))
    # 429는 재시도하지 않으므로 이 값은 일시적 오류(resultCode=99 등)에만 적용된다.
    api_max_retries: int = int(os.getenv("WELFARE_API_MAX_RETRIES", "3"))
    api_backoff_seconds: float = float(os.getenv("WELFARE_API_BACKOFF", "1.5"))
    api_timeout_seconds: float = float(os.getenv("WELFARE_API_TIMEOUT", "60"))
    api_page_size: int = int(os.getenv("WELFARE_API_PAGE_SIZE", "300"))

    # --- OpenAI --------------------------------------------------------------
    openai_api_key: Optional[str] = os.getenv("OPENAI_API_KEY")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o")
    embedding_model: str = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    # cb.cb_institutions.embedding 컬럼이 vector(1536)이다. 모델을 바꾸면 여기도 맞춰야 한다.
    embedding_dim: int = int(os.getenv("OPENAI_EMBEDDING_DIM", "1536"))
    embedding_batch_size: int = int(os.getenv("OPENAI_EMBEDDING_BATCH", "64"))


cb_settings = CbSettings()
