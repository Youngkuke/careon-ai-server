"""CareOn AI 서버 진입점."""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import db
from app.config import settings
from app.errors import ApiError, api_error_handler
from app.routers import chat, policies

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="CareOn AI Server", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(ApiError, api_error_handler)
app.include_router(chat.router)
app.include_router(policies.router)


@app.on_event("startup")
async def on_startup() -> None:
    await db.connect()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await db.disconnect()


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
