"""공공데이터 복지서비스 API → cb.cb_institutions 배치 수집.

대화 서버(app/main.py)와 완전히 별개로 도는 독립 스크립트다.
"대화할 때마다 API를 실시간 호출"하지 않기 위해, 수집과 매칭을 분리한다.

수집 대상 (실측):
    중앙부처복지서비스   461건 (전국)
    지자체복지서비스     395건 (ctpvNm=서울특별시 1회 조회)
                        = 856건

실행 예:
    # 소량 테스트 (각 10건, DB 기록 없음)
    python scripts/sync_institutions.py --limit 10 --dry-run

    # 소량 테스트 (각 10건, DB 저장)
    python scripts/sync_institutions.py --limit 10

    # 전량
    python scripts/sync_institutions.py

    # 임베딩만 다시 (본문이 바뀐 건만 자동으로 골라낸다)
    python scripts/sync_institutions.py --embed-only
"""
import argparse
import asyncio
import logging
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app.cb import constants, db, embedding, normalize  # noqa: E402
from app.cb.config import cb_settings  # noqa: E402
from app.cb.welfare_api import (  # noqa: E402
    WelfareApiClient,
    WelfareQuotaExceeded,
    build_pools,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("sync")

SOURCES = ("central", "local")


# --- 수집 --------------------------------------------------------------------
async def collect(
    api: WelfareApiClient, source: str, limit: Optional[int], resume: bool
) -> List[Dict[str, Any]]:
    """목록 + 상세를 받아 DB 행 형태로 정규화한다.

    상세를 못 받은 건은 행을 만들지 않는다. 목록 데이터만 저장하면
    service_content가 빈 반쪽짜리 행이 남아 검색 품질을 떨어뜨린다.
    """
    logger.info("[%s] 목록 조회 시작 (남은 호출 예산 %s)",
                source, api.pool(source).remaining_text)
    list_items = await api.fetch_list(source, limit=limit)
    logger.info("[%s] 목록 %d건", source, len(list_items))

    serv_ids = [i.get("servId") for i in list_items if i.get("servId")]
    if resume:
        done = await db.fetch_detail_completed_ids(source)
        skipped = [s for s in serv_ids if s in done]
        serv_ids = [s for s in serv_ids if s not in done]
        logger.info("[%s] --resume: 이미 상세 보유 %d건 건너뜀, 남은 %d건",
                    source, len(skipped), len(serv_ids))

    logger.info("[%s] 상세 조회 시작 (%d건, 동시성 %d, 최소간격 %.2fs)",
                source, len(serv_ids), cb_settings.api_concurrency,
                cb_settings.api_min_interval)
    details = await api.fetch_details(source, serv_ids)
    logger.info("[%s] 상세 %d건 수신 (미수신 %d건)",
                source, len(details), len(serv_ids) - len(details))

    rows = []
    for item in list_items:
        sid = item.get("servId")
        detail = details.get(sid) if sid else None
        if not sid or not detail:
            continue
        rows.append(normalize.to_row(source, item, detail))
    return rows


# --- 태그 보완 ----------------------------------------------------------------
_AXIS_COLUMN = {
    "life_cycle": "life_cycle_tags",
    "household": "household_tags",
    "theme": "theme_tags",
}


def empty_axes(row: Dict[str, Any]) -> List[str]:
    """비어 있는 축 목록. API 원본이 축별로 결측이라 축 단위로 본다.

    실측 결측률: 생애주기 33~35%, 가구상황 39~42%, 관심주제 2~27%.
    3종이 전부 빈 건은 856건 중 10건뿐이라, '전부 빔' 기준으로는 사실상
    보완이 안 된다. 축 단위로 채워야 한다.
    """
    return [axis for axis, col in _AXIS_COLUMN.items() if not row[col]]


def needs_tag_completion(row: Dict[str, Any], mode: str) -> bool:
    axes = empty_axes(row)
    if mode == "none":
        return False
    if mode == "all-empty":
        return len(axes) == 3
    return bool(axes)  # any-empty


async def complete_tags(rows: List[Dict[str, Any]], mode: str) -> int:
    """비어 있는 축만 LLM으로 채운다. API가 준 값은 절대 덮어쓰지 않는다."""
    if mode == "none":
        logger.info("태그 보완 건너뜀 (--tag-fill none)")
        return 0

    targets = [r for r in rows if needs_tag_completion(r, mode)]
    if not targets:
        logger.info("태그 보완 대상 없음")
        return 0

    logger.info("태그 보완 대상 %d건 (mode=%s) — LLM 추론 시작", len(targets), mode)
    inferred = await embedding.infer_tags_many(targets)

    filled = 0
    for row in targets:
        tags = inferred.get(row["serv_id"])
        if not tags:
            continue
        axes = empty_axes(row)
        if not axes:
            continue
        for axis in axes:
            row[_AXIS_COLUMN[axis]] = tags.get(axis) or []
        # tags_source='llm'은 "한 축이라도 LLM이 채웠다"는 뜻이다.
        row["tags_source"] = "llm"
        await db.update_tags(row["serv_id"], {
            axis: row[col] for axis, col in _AXIS_COLUMN.items()
        })
        filled += 1

    logger.info("태그 보완 완료 %d건", filled)
    return filled


# --- 임베딩 ------------------------------------------------------------------
async def embed_pending(serv_ids: Optional[List[str]] = None) -> int:
    """content_hash가 바뀌었거나 임베딩이 없는 행만 다시 임베딩한다."""
    candidates = await db.fetch_rows_needing_embedding(serv_ids)
    todo = []
    for row in candidates:
        text = normalize.embedding_text(row)
        if not text.strip():
            continue
        digest = normalize.content_hash(text, cb_settings.embedding_model)
        if row.get("content_hash") == digest and not row.get("no_embedding"):
            continue
        todo.append({"serv_id": row["serv_id"], "text": text, "content_hash": digest})

    if not todo:
        logger.info("임베딩 대상 없음 (전부 최신)")
        return 0

    logger.info("임베딩 대상 %d건 / 전체 %d건", len(todo), len(candidates))
    vectors = await embedding.embed_texts([t["text"] for t in todo])
    saved = await db.save_embeddings([
        {
            "serv_id": t["serv_id"],
            "embedding": vec,
            "content_hash": t["content_hash"],
            "model": cb_settings.embedding_model,
        }
        for t, vec in zip(todo, vectors)
    ])
    logger.info("임베딩 저장 %d건", saved)
    return saved


# --- 리포트 ------------------------------------------------------------------
def report(source: str, rows: List[Dict[str, Any]]) -> None:
    from collections import Counter

    scope = Counter(r["region_scope"] for r in rows)
    axis_gap = Counter(a for r in rows for a in empty_axes(r))
    all_empty = sum(1 for r in rows if len(empty_axes(r)) == 3)
    any_empty = sum(1 for r in rows if empty_axes(r))
    missing = {
        "target_detail": sum(1 for r in rows if not r["target_detail"]),
        "service_content": sum(1 for r in rows if not r["service_content"]),
        "apply_method": sum(1 for r in rows if not r["apply_method"]),
        "extra_info": sum(1 for r in rows if not r["extra_info"]),
    }
    print(f"\n===== [{source}] 수집 {len(rows)}건 =====")
    print("  region_scope:", dict(scope))
    if scope.get(constants.REGION_DISTRICT):
        gu = Counter(r["sgg_nm"] for r in rows if r["region_scope"] == constants.REGION_DISTRICT)
        print("  자치구 분포:", dict(gu.most_common(8)), "..." if len(gu) > 8 else "")
    print(f"  태그 결측: 축별 {dict(axis_gap)} / 하나라도 빔 {any_empty} / 전부 빔 {all_empty}")
    print("  주요 필드 결측:", missing)
    for kind, col in (("life_cycle", "life_cycle_tags"),
                      ("household", "household_tags"),
                      ("theme", "theme_tags")):
        c = Counter(t for r in rows for t in r[col])
        print(f"  [{kind}] {dict(c.most_common())}")


def print_sample(rows: List[Dict[str, Any]], n: int = 2) -> None:
    for row in rows[:n]:
        print(f"\n--- 샘플: [{row['serv_id']}] {row['serv_nm']} ---")
        for key in ("source", "region_scope", "ctpv_nm", "sgg_nm", "life_cycle_tags",
                    "household_tags", "theme_tags", "jur_org_nm", "contact",
                    "criteria_year", "enforce_begin_ymd", "origin_modified_ymd"):
            print(f"  {key:<20} {row.get(key)!r}")
        for key in ("serv_dgst", "target_detail", "service_content", "apply_method"):
            v = row.get(key)
            v = (v[:160] + "…") if v and len(v) > 160 else v
            print(f"  {key:<20} {v!r}")
        print(f"  extra_info 키        {list((row.get('extra_info') or {}).keys())}")
        print(f"  임베딩 텍스트 길이    {len(normalize.embedding_text(row))}자")


# --- 메인 --------------------------------------------------------------------
async def run_source(
    api: WelfareApiClient, source: str, args: argparse.Namespace
) -> Dict[str, int]:
    run_id = None
    stats = {"fetched": 0, "inserted": 0, "updated": 0, "embedded": 0}
    try:
        if not args.dry_run:
            run_id = await db.start_run(source)

        rows = await collect(api, source, args.limit, args.resume)
        stats["fetched"] = len(rows)
        if not rows:
            logger.warning("[%s] 저장할 행이 없습니다", source)
            if run_id is not None:
                status = "failed" if api.quota_exceeded(source) else "success"
                await db.finish_run(
                    run_id, status, **stats,
                    error_text="일일 호출 한도 소진" if api.quota_exceeded(source) else None,
                )
            return stats
        report(source, rows)
        if args.sample:
            print_sample(rows)

        if args.dry_run:
            logger.info("[%s] --dry-run: DB에 쓰지 않고 종료", source)
            return stats

        result = await db.upsert_institutions(rows)
        stats["inserted"], stats["updated"] = result["inserted"], result["updated"]
        logger.info("[%s] 저장 완료 신규 %d / 갱신 %d",
                    source, result["inserted"], result["updated"])

        await complete_tags(rows, args.tag_fill)

        if not args.skip_embedding:
            stats["embedded"] = await embed_pending([r["serv_id"] for r in rows])

        # --limit / --resume / 한도 소진으로 부분 수집한 경우에는 비활성화를 돌리면 안 된다.
        # 안 받아온 나머지가 전부 "사라진 것"으로 처리된다.
        partial = args.limit is not None or args.resume or api.quota_exceeded(source)
        if not partial and not args.no_deactivate:
            await db.deactivate_missing(source, [r["serv_id"] for r in rows])
        elif partial:
            logger.info("[%s] 부분 수집 — 미수집 건 비활성화를 건너뜁니다", source)

        if run_id is not None:
            await db.finish_run(run_id, "success", **stats)
        return stats

    except Exception as exc:  # noqa: BLE001
        logger.exception("[%s] 수집 실패", source)
        if run_id is not None:
            await db.finish_run(
                run_id, "failed", **stats,
                error_text=f"{exc!r}\n{traceback.format_exc()}"[:8000],
            )
        raise


async def main() -> int:
    parser = argparse.ArgumentParser(description="복지서비스 API 배치 수집")
    parser.add_argument("--source", choices=("central", "local", "all"), default="all")
    parser.add_argument("--limit", type=int, default=None,
                        help="소스별 최대 건수 (소량 테스트용)")
    parser.add_argument("--dry-run", action="store_true",
                        help="수집·정규화만 하고 DB에 쓰지 않는다")
    parser.add_argument("--sample", action="store_true", help="샘플 행을 출력한다")
    parser.add_argument("--skip-embedding", action="store_true")
    parser.add_argument(
        "--tag-fill", choices=("none", "all-empty", "any-empty"), default="any-empty",
        help="LLM 태그 보완 범위. any-empty=비어있는 축만 채움(기본), "
             "all-empty=3종이 모두 빈 건만, none=보완 안 함",
    )
    parser.add_argument("--no-deactivate", action="store_true",
                        help="원본에서 사라진 건의 비활성화를 건너뛴다")
    parser.add_argument("--resume", action="store_true",
                        help="이미 상세를 받아둔 건은 건너뛰고 남은 것만 수집한다 "
                             "(호출 한도 소진 후 이어받기)")
    parser.add_argument("--embed-only", action="store_true",
                        help="공공데이터 API 호출 없이 임베딩만 갱신한다")
    parser.add_argument("--tag-only", action="store_true",
                        help="공공데이터 API 호출 없이, 축이 빈 행의 태그만 LLM으로 "
                             "채우고 임베딩을 갱신한다 (TPM 제한으로 유실된 건 복구용)")
    args = parser.parse_args()

    if not args.dry_run:
        await db.ensure_schema()

    try:
        if args.embed_only:
            n = await embed_pending()
            print(f"\n임베딩 갱신 {n}건")
            return 0

        if args.tag_only:
            # 공공데이터 호출이 0건이라 일일 한도와 무관하게 언제든 재실행할 수 있다.
            rows = await db.fetch_rows_needing_tags()
            logger.info("[tag-only] 축이 하나라도 빈 행 %d건", len(rows))
            filled = await complete_tags(rows, "any-empty")
            # 태그는 embedding_text에 '분류'로 들어가므로 content_hash가 바뀐다.
            # embed_pending이 바뀐 행만 알아서 골라낸다.
            embedded = 0
            if not args.skip_embedding and filled:
                embedded = await embed_pending([r["serv_id"] for r in rows])
            print(f"\n태그 보완 {filled}건 / 임베딩 갱신 {embedded}건")
            return 0

        sources = SOURCES if args.source == "all" else (args.source,)
        totals = {"fetched": 0, "inserted": 0, "updated": 0, "embedded": 0}

        pools = build_pools()
        async with httpx.AsyncClient(follow_redirects=True) as http:
            api = WelfareApiClient(http, pools)
            for source in sources:
                try:
                    stats = await run_source(api, source, args)
                except WelfareQuotaExceeded as exc:
                    logger.error("[%s] %s", source, exc)
                    continue
                for k in totals:
                    totals[k] += stats[k]

        print("\n===== 전체 합계 =====")
        print(f"  수집 {totals['fetched']} / 신규 {totals['inserted']} / "
              f"갱신 {totals['updated']} / 임베딩 {totals['embedded']}")
        print("\n===== 키 사용량 =====")
        for source in sources:
            pool = pools[source]
            used = ", ".join(
                f"#{e['index']}={e['used']}{'(소진)' if e['exhausted'] else ''}"
                for e in pool.usage_report()
            )
            print(f"  [{source}] {used} | 남은 예산 {pool.remaining_text}")
        return 0
    finally:
        # 이벤트 루프가 닫히기 전에 정리해야 GC 시점에
        # 'RuntimeError: Event loop is closed'가 뜨지 않는다.
        await embedding.close()
        await db.disconnect()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
