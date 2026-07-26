"""OpenAI 임베딩 + 3종 태그 LLM 보완.

cb의 모든 LLM 호출은 OpenAI를 쓴다 (기존 챗봇의 Anthropic과 무관).
"""
import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Sequence

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)

from app.cb import constants
from app.cb.config import cb_settings

logger = logging.getLogger(__name__)

_client: Optional[AsyncOpenAI] = None

# 재시도로 복구되는 오류들. RateLimitError(TPM 초과)가 대부분이다.
# 공공데이터 API의 429와 성격이 정반대라는 점이 중요하다:
# 그쪽은 자정까지 복구 불가라 재시도가 해악이지만, 이쪽은 '분당' 제한이라
# 기다리면 반드시 풀린다. 재시도를 안 하면 그 행의 태그가 영구히 빈다.
_RETRYABLE = (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError)


def _retry_after(exc: Exception) -> Optional[float]:
    """서버가 알려준 대기 시간(초). 없으면 None."""
    response = getattr(exc, "response", None)
    header = getattr(response, "headers", None)
    if not header:
        return None
    for key in ("retry-after", "x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
        raw = header.get(key)
        if not raw:
            continue
        try:
            return float(str(raw).rstrip("s"))
        except ValueError:
            continue
    return None


async def _with_retry(make_call, *, label: str, attempts: int = 5, base: float = 4.0):
    """TPM 제한을 백오프로 흡수한다. make_call은 매번 새 코루틴을 만들어야 한다."""
    for attempt in range(1, attempts + 1):
        try:
            return await make_call()
        except _RETRYABLE as exc:
            if attempt == attempts:
                raise
            wait = _retry_after(exc) or base * (2 ** (attempt - 1))
            wait = min(wait, 60.0)
            logger.warning(
                "OpenAI %s 재시도 %d/%d (%.1fs 대기) %s",
                label, attempt, attempts, wait, type(exc).__name__,
            )
            await asyncio.sleep(wait)

# 임베딩 입력 상한. text-embedding-3-small은 8191 토큰까지 받는다.
# 한글은 대략 1자 ≈ 1토큰이라 넉넉히 잘라둔다.
MAX_EMBEDDING_CHARS = 6000


def client() -> AsyncOpenAI:
    global _client
    if _client is None:
        if not cb_settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY가 설정되지 않았습니다 (.env 확인)")
        _client = AsyncOpenAI(api_key=cb_settings.openai_api_key)
    return _client


async def close() -> None:
    """내부 httpx 연결을 닫는다.

    닫지 않고 이벤트 루프가 먼저 종료되면 GC 시점에
    'RuntimeError: Event loop is closed'가 표준에러로 쏟아진다.
    """
    global _client
    if _client is not None:
        await _client.close()
        _client = None


async def embed_texts(texts: Sequence[str]) -> List[List[float]]:
    """텍스트 배치를 임베딩한다. 입력 순서를 그대로 유지해서 돌려준다."""
    if not texts:
        return []
    out: List[List[float]] = []
    batch_size = cb_settings.embedding_batch_size

    for start in range(0, len(texts), batch_size):
        chunk = [t[:MAX_EMBEDDING_CHARS] for t in texts[start:start + batch_size]]
        kwargs: Dict[str, Any] = {"model": cb_settings.embedding_model, "input": chunk}
        # 모델 기본 차원과 다를 때만 dimensions를 넘긴다.
        if cb_settings.embedding_dim != 1536:
            kwargs["dimensions"] = cb_settings.embedding_dim

        resp = await _with_retry(
            lambda: client().embeddings.create(**kwargs),
            label="embeddings",
        )
        # OpenAI는 index 순서를 보장하지만 방어적으로 정렬한다.
        for item in sorted(resp.data, key=lambda d: d.index):
            if len(item.embedding) != cb_settings.embedding_dim:
                raise RuntimeError(
                    "임베딩 차원 불일치: 기대 {} 실제 {}. "
                    "cb.cb_institutions.embedding 컬럼과 맞지 않습니다.".format(
                        cb_settings.embedding_dim, len(item.embedding)
                    )
                )
            out.append(item.embedding)
        logger.info("임베딩 %d/%d", min(start + batch_size, len(texts)), len(texts))

    return out


# --- 3종 태그 LLM 보완 --------------------------------------------------------
_TAG_SCHEMA = {
    "type": "object",
    "properties": {
        "life_cycle": {
            "type": "array",
            "items": {"type": "string", "enum": constants.LIFE_CYCLE_TAGS},
        },
        "household": {
            "type": "array",
            "items": {"type": "string", "enum": constants.HOUSEHOLD_TAGS},
        },
        "theme": {
            "type": "array",
            "items": {"type": "string", "enum": constants.THEME_TAGS},
        },
    },
    "required": ["life_cycle", "household", "theme"],
    "additionalProperties": False,
}

_TAG_SYSTEM = """당신은 한국 복지제도를 복지로(bokjiro)의 3종 분류 체계로 태깅하는 역할입니다.

주어진 제도의 원문을 읽고 아래 세 축의 값을 고릅니다.
- 생애주기(life_cycle): 이 제도의 지원대상 연령/생애 단계
- 가구상황(household): 지원대상이 특정 가구 유형으로 한정될 때만
- 관심주제(theme): 이 제도가 다루는 영역

원칙:
- 반드시 주어진 목록 안의 값만 사용합니다. 새 값을 만들지 않습니다.
- 원문에 근거가 없으면 비워 둡니다. 추측해서 채우지 않습니다.
- 특히 가구상황은 제도가 명시적으로 그 대상을 한정할 때만 고릅니다.
  (모든 시민 대상 제도에 '저소득'을 붙이지 않습니다.)
- 해당하는 값이 여러 개면 모두 고릅니다."""


async def infer_tags(row: Dict[str, Any]) -> Dict[str, List[str]]:
    """원본 태그가 비어 있는 제도의 3종 필터를 LLM으로 추론한다.

    API가 태그를 직접 내려주므로 이건 결측 보완용이다.
    반환값은 어휘로 한 번 더 필터링해서 지어낸 값을 차단한다.
    """
    parts = [
        ("제도명", row.get("serv_nm")),
        ("요약", row.get("serv_dgst")),
        ("지원대상", row.get("target_detail")),
        ("선정기준", row.get("select_criteria")),
        ("서비스내용", row.get("service_content")),
    ]
    body = "\n".join(f"{k}: {v}" for k, v in parts if v)[:MAX_EMBEDDING_CHARS]

    resp = await _with_retry(
        lambda: client().chat.completions.create(
            model=cb_settings.openai_model,
            messages=[
                {"role": "system", "content": _TAG_SYSTEM},
                {"role": "user", "content": body},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "welfare_tags", "strict": True, "schema": _TAG_SCHEMA,
                },
            },
            temperature=0,
        ),
        label="tags servId=%s" % row.get("serv_id"),
    )
    raw = json.loads(resp.choices[0].message.content or "{}")
    return {
        kind: constants.filter_to_vocabulary(raw.get(kind) or [], kind)
        for kind in ("life_cycle", "household", "theme")
    }


async def infer_tags_many(
    rows: Sequence[Dict[str, Any]], concurrency: int = 2
) -> Dict[str, Dict[str, List[str]]]:
    """여러 건을 동시성 제한 하에 태깅한다. 실패한 건은 결과에서 빠진다.

    동시성 기본값이 2인 이유: gpt-4o는 분당 토큰(TPM) 제한이 있고, 이 프롬프트는
    제도 본문을 통째로 넣어 건당 토큰이 크다. 4로 돌렸을 때 450건 중 209건이
    RateLimitError로 유실됐다. 낮춰서 애초에 덜 부딪히고, 부딪힌 건은
    _with_retry가 흡수한다.
    """
    sem = asyncio.Semaphore(concurrency)
    results: Dict[str, Dict[str, List[str]]] = {}
    failures: List[str] = []

    async def one(row: Dict[str, Any]) -> None:
        async with sem:
            try:
                results[row["serv_id"]] = await infer_tags(row)
            except Exception as exc:  # noqa: BLE001 — 태깅 실패가 전체 수집을 막으면 안 된다
                # 재시도를 다 쓰고도 실패한 경우만 여기 온다.
                # 209건이 각각 스택트레이스를 찍으면 로그를 못 읽으므로 요약만 남긴다.
                failures.append("{}({})".format(row.get("serv_id"), type(exc).__name__))

    await asyncio.gather(*(one(r) for r in rows))

    if failures:
        logger.error(
            "태그 추론 최종 실패 %d/%d건: %s%s",
            len(failures), len(rows), ", ".join(failures[:10]),
            " …" if len(failures) > 10 else "",
        )
    return results
