"""cb 스키마 전용 DB 접근.

기존 app/db.py와 별도의 연결을 쓴다. 이유:
  1) search_path를 cb로 고정해야 pgvector의 '<=>' 연산자가 해석된다.
     (연산자는 타입과 달리 cb.vector 같은 스키마 한정 표기로 해결되지 않는다.)
  2) search_path에 public을 넣지 않는 것이 기존 테이블 오접근을 막는 안전장치다.

기존 public 스키마 테이블은 참조하지 않는다. 유일한 예외는
carers.region 1회 복사이며, 그때만 public.carers로 명시 한정해서 읽는다.
"""
import json
import logging
from typing import Any, Dict, List, Optional, Sequence

import asyncpg

from app.cb.config import cb_settings

logger = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None

# INSERT/UPDATE 대상 컬럼. region_key는 생성 컬럼이라 제외한다.
_UPSERT_COLUMNS = [
    "serv_id", "source", "serv_nm", "serv_dgst",
    "life_cycle_tags", "household_tags", "theme_tags", "tags_source",
    "ctpv_nm", "sgg_nm", "region_scope",
    "target_detail", "select_criteria", "service_content", "apply_method",
    "extra_info", "jur_org_nm", "support_cycle", "provision_type",
    "apply_method_nm", "detail_link", "contact", "criteria_year",
    "enforce_begin_ymd", "enforce_end_ymd", "origin_modified_ymd",
    "raw_list", "raw_detail", "last_fetched_at", "detail_fetched_at",
]

# 재수집 시 갱신하지 않을 컬럼 (임베딩은 별도 단계에서 관리한다).
_UPSERT_SKIP_ON_CONFLICT = {"serv_id"}

_JSON_COLUMNS = {"extra_info", "raw_list", "raw_detail"}


async def connect() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        if not cb_settings.database_url:
            raise RuntimeError("DATABASE_URL이 설정되지 않았습니다 (.env 확인)")
        _pool = await asyncpg.create_pool(
            cb_settings.database_url,
            min_size=1,
            max_size=5,
            server_settings={"search_path": cb_settings.db_schema},
        )
        logger.info("cb 스키마 연결 완료 (search_path=%s)", cb_settings.db_schema)
    return _pool


async def disconnect() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def ensure_schema() -> None:
    """cb 스키마와 필수 객체가 존재하는지 확인한다.

    마이그레이션(scripts/migrations/001_cb_schema.sql)을 먼저 적용해야 한다.
    이 스크립트는 DDL을 실행하지 않는다 — 스키마 생성은 명시적 승인 아래에서만 한다.
    """
    pool = await connect()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema=$1 AND table_name='cb_institutions'",
            cb_settings.db_schema,
        )
        if not exists:
            raise RuntimeError(
                "cb.cb_institutions 테이블이 없습니다. "
                "scripts/migrations/001_cb_schema.sql을 먼저 적용하세요."
            )
        has_vector = await conn.fetchval(
            "SELECT count(*) FROM pg_extension WHERE extname='vector'"
        )
        if not has_vector:
            raise RuntimeError("pgvector 확장이 설치되지 않았습니다.")


def _bind(row: Dict[str, Any], column: str) -> Any:
    value = row.get(column)
    if column in _JSON_COLUMNS:
        return json.dumps(value or {}, ensure_ascii=False)
    return value


def _upsert_sql() -> str:
    cols = ", ".join(_UPSERT_COLUMNS)
    placeholders = []
    for i, col in enumerate(_UPSERT_COLUMNS, start=1):
        placeholders.append(f"${i}::jsonb" if col in _JSON_COLUMNS else f"${i}")
    updates = ", ".join(
        f"{c} = EXCLUDED.{c}"
        for c in _UPSERT_COLUMNS
        if c not in _UPSERT_SKIP_ON_CONFLICT
    )
    return (
        f"INSERT INTO cb.cb_institutions ({cols}) VALUES ({', '.join(placeholders)}) "
        f"ON CONFLICT (serv_id) DO UPDATE SET {updates}, is_active = TRUE"
    )


async def upsert_institutions(rows: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """제도 행들을 upsert한다. (신규, 갱신) 건수를 돌려준다."""
    if not rows:
        return {"inserted": 0, "updated": 0}
    pool = await connect()
    serv_ids = [r["serv_id"] for r in rows]
    async with pool.acquire() as conn:
        existing = set(
            r["serv_id"] for r in await conn.fetch(
                "SELECT serv_id FROM cb.cb_institutions WHERE serv_id = ANY($1::TEXT[])",
                serv_ids,
            )
        )
        await conn.executemany(
            _upsert_sql(),
            [[_bind(r, c) for c in _UPSERT_COLUMNS] for r in rows],
        )
    inserted = sum(1 for s in serv_ids if s not in existing)
    return {"inserted": inserted, "updated": len(serv_ids) - inserted}


async def fetch_institution(serv_id: str) -> Optional[Dict[str, Any]]:
    """제도 1건 전문. 상세 API와 쉬운 말 설명이 쓴다.

    내린 제도(is_active=false)도 돌려준다. 저장해 둔 유저가 열었을 때
    404가 뜨는 것보다, 내용을 보여주고 화면에서 상태를 알리는 편이 낫다.
    """
    pool = await connect()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT serv_id, source, serv_nm, serv_dgst, "
            "       life_cycle_tags, household_tags, theme_tags, "
            "       ctpv_nm, sgg_nm, region_scope, "
            "       target_detail, select_criteria, service_content, apply_method, "
            "       extra_info, jur_org_nm, support_cycle, provision_type, "
            "       apply_method_nm, detail_link, contact, criteria_year, is_active "
            "FROM cb.cb_institutions WHERE serv_id = $1",
            serv_id,
        )
    if row is None:
        return None
    out = dict(row)
    # extra_info는 JSONB다. asyncpg는 문자열로 돌려준다.
    if isinstance(out.get("extra_info"), str):
        out["extra_info"] = json.loads(out["extra_info"] or "{}")
    return out


async def fetch_detail_completed_ids(source: str) -> set:
    """이미 상세까지 받아둔 serv_id 집합 (--resume용)."""
    pool = await connect()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT serv_id FROM cb.cb_institutions "
            "WHERE source = $1 AND detail_fetched_at IS NOT NULL",
            source,
        )
    return {r["serv_id"] for r in rows}


async def fetch_rows_needing_tags() -> List[Dict[str, Any]]:
    """축이 하나라도 빈 행을 돌려준다 (--tag-only용).

    상세를 받아둔 행만 대상으로 한다. 제도명·요약만으로 태그를 추론하면
    근거 없는 값이 붙는다. API 원본이 채워준 축은 손대지 않으므로,
    LLM이 이미 성공한 행도 남은 빈 축이 있으면 다시 대상이 된다.
    """
    pool = await connect()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT serv_id, serv_nm, serv_dgst, target_detail, select_criteria, "
            "       service_content, life_cycle_tags, household_tags, theme_tags "
            "FROM cb.cb_institutions "
            "WHERE is_active AND detail_fetched_at IS NOT NULL "
            "  AND (life_cycle_tags = '{}' OR household_tags = '{}' "
            "       OR theme_tags = '{}') "
            "ORDER BY serv_id"
        )
    return [dict(r) for r in rows]


async def fetch_rows_needing_embedding(
    serv_ids: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """임베딩이 없거나 content_hash가 바뀐 행을 돌려준다."""
    pool = await connect()
    query = (
        "SELECT serv_id, serv_nm, serv_dgst, target_detail, select_criteria, "
        "       service_content, life_cycle_tags, household_tags, theme_tags, "
        "       content_hash, embedding IS NULL AS no_embedding "
        "FROM cb.cb_institutions WHERE is_active "
        # 상세를 못 받은 행은 제도명·요약만 있어서 임베딩 품질이 떨어진다.
        # 상세가 채워진 뒤에 임베딩한다.
        "  AND (service_content IS NOT NULL OR target_detail IS NOT NULL)"
    )
    args: List[Any] = []
    if serv_ids is not None:
        query += " AND serv_id = ANY($1::TEXT[])"
        args.append(list(serv_ids))
    async with pool.acquire() as conn:
        return [dict(r) for r in await conn.fetch(query, *args)]


async def save_embeddings(items: Sequence[Dict[str, Any]]) -> int:
    """[{serv_id, embedding: list[float], content_hash, model}] 를 저장한다."""
    if not items:
        return 0
    pool = await connect()
    async with pool.acquire() as conn:
        await conn.executemany(
            "UPDATE cb.cb_institutions "
            "SET embedding = $2::vector, content_hash = $3, embedding_model = $4, "
            "    embedded_at = now() "
            "WHERE serv_id = $1",
            [
                (
                    it["serv_id"],
                    "[" + ",".join(f"{v:.7f}" for v in it["embedding"]) + "]",
                    it["content_hash"],
                    it["model"],
                )
                for it in items
            ],
        )
    return len(items)


async def update_tags(serv_id: str, tags: Dict[str, List[str]]) -> None:
    """LLM으로 보완한 3종 태그를 반영하고 tags_source를 llm으로 표시한다."""
    pool = await connect()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE cb.cb_institutions SET life_cycle_tags = $2::TEXT[], "
            "  household_tags = $3::TEXT[], theme_tags = $4::TEXT[], tags_source = 'llm' "
            "WHERE serv_id = $1",
            serv_id,
            tags.get("life_cycle") or [],
            tags.get("household") or [],
            tags.get("theme") or [],
        )


async def deactivate_missing(source: str, seen_serv_ids: Sequence[str]) -> int:
    """이번 수집에서 안 보인 건을 내린다 (삭제하지 않는다).

    저장한 유저의 참조가 깨지지 않도록 soft delete로만 처리한다.
    """
    if not seen_serv_ids:
        return 0
    pool = await connect()
    async with pool.acquire() as conn:
        result = await conn.fetch(
            "UPDATE cb.cb_institutions SET is_active = FALSE "
            "WHERE source = $1 AND is_active AND NOT (serv_id = ANY($2::TEXT[])) "
            "RETURNING serv_id",
            source, list(seen_serv_ids),
        )
    if result:
        logger.warning(
            "[%s] 원본에서 사라져 비활성화한 제도 %d건: %s",
            source, len(result), [r["serv_id"] for r in result][:20],
        )
    return len(result)


# --- cb_sync_runs -------------------------------------------------------------
async def start_run(source: str) -> int:
    pool = await connect()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO cb.cb_sync_runs (source) VALUES ($1) RETURNING run_id", source
        )


async def finish_run(
    run_id: int,
    status: str,
    *,
    fetched: int = 0,
    inserted: int = 0,
    updated: int = 0,
    embedded: int = 0,
    error_text: Optional[str] = None,
) -> None:
    pool = await connect()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE cb.cb_sync_runs SET status=$2, fetched_count=$3, inserted_count=$4, "
            "  updated_count=$5, embedded_count=$6, error_text=$7, finished_at=now() "
            "WHERE run_id=$1",
            run_id, status, fetched, inserted, updated, embedded, error_text,
        )
