"""CareOn AI 서버 진입점."""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import db
from app.cb import graph as cb_graph
from app.config import settings
from app.errors import ApiError, api_error_handler
from app.routers import cb, chat, policies

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="CareOn AI Server", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    # "*"는 Authorization 을 포함해 프론트가 보내는 헤더를 프리플라이트에서 그대로 허용한다.
    allow_headers=["*"],
)

app.add_exception_handler(ApiError, api_error_handler)
app.include_router(chat.router)
app.include_router(policies.router)
app.include_router(cb.router)


@app.on_event("startup")
async def on_startup() -> None:
    await db.connect()
    try:
        await cb_graph.startup()
    except Exception:  # noqa: BLE001
        # cb는 아직 기존 챗봇과 독립적이다. cb 초기화 실패로 서버 전체가
        # 안 뜨면 /api/v1/chat 까지 같이 죽는다. cb 엔드포인트만 503으로 둔다.
        logging.getLogger(__name__).exception("[cb] 초기화 실패 — cb API는 비활성 상태입니다")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await db.disconnect()
    await cb_graph.shutdown()


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "model": settings.claude_model,
        "db": db.available(),
        "prompts_dir": str(settings.prompts_dir),
    }


if __name__ == "__main__":
    # Railway 같은 PaaS는 실행 포트를 PORT 환경변수로 넘겨준다.
    # 로컬에서는 PORT가 없으므로 8000을 쓴다.
    import os

    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
    )
