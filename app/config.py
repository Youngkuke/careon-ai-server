"""환경 설정."""
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent

load_dotenv(ROOT_DIR / ".env")


class Settings:
    anthropic_api_key: Optional[str] = os.getenv("ANTHROPIC_API_KEY")
    claude_model: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")
    database_url: Optional[str] = os.getenv("DATABASE_URL") or None
    prompts_dir: Path = (ROOT_DIR / os.getenv("PROMPTS_DIR", ".")).resolve()
    session_ttl_seconds: int = int(os.getenv("SESSION_TTL_SECONDS", "86400"))

    # Spring 백엔드가 발급한 access_token 검증용.
    # JWT_SECRET은 Base64 문자열이고, 서명 키는 이걸 디코드한 '바이트'다 (app/auth.py 참고).
    jwt_secret: Optional[str] = os.getenv("JWT_SECRET") or None
    jwt_alg: str = os.getenv("JWT_ALG", "HS512")

    # 매칭 결과를 matched_policy에 저장할지 여부.
    # matched_policy.match_group 컬럼이 아직 DB에 없다. Spring이 JPA ddl-auto=update로
    # 다음 배포 때 만들어준다. 그 전에 켜면 UndefinedColumnError가 난다.
    # 배포 완료 통보를 받으면 .env에 MATCH_PERSIST_ENABLED=true만 넣으면 된다.
    match_persist_enabled: bool = (
        os.getenv("MATCH_PERSIST_ENABLED", "false").strip().lower() == "true"
    )

    # 챗봇 진행 상태를 user_conversation_state에 저장할지 여부.
    # active_policy_id가 NOT NULL + FK(policies)라 "제도 없음" 상태를 저장할 방법이 없다.
    # NOT NULL이 풀리면 켠다 (app/services/conversation_state.py 참고).
    conversation_state_enabled: bool = (
        os.getenv("CONVERSATION_STATE_ENABLED", "false").strip().lower() == "true"
    )

    # CORS 허용 출처. 쉼표로 구분해서 .env의 CORS_ALLOW_ORIGINS로 덮어쓸 수 있다.
    # credentials(쿠키/Authorization)를 쓰기 때문에 "*"는 쓸 수 없다.
    # 브라우저가 Access-Control-Allow-Origin: * 를 credentials 요청에서 거부한다.
    cors_allow_origins: list[str] = [
        o.strip()
        for o in os.getenv(
            "CORS_ALLOW_ORIGINS",
            "https://www.careon.site,https://careon.site,http://localhost:3000,http://localhost:5173,http://127.0.0.1:5173",
        ).split(",")
        if o.strip()
    ]

    # LLM 호출 파라미터
    extract_max_tokens: int = 1024
    reply_max_tokens: int = 1024
    matching_max_tokens: int = 16000


settings = Settings()
