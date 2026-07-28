"""마이그레이션 SQL 파일 하나를 적용한다.

트랜잭션으로 감싼다. 중간에 실패하면 아무것도 남지 않는다.
적용 전에 무엇이 실행될지 보여주고, --apply가 없으면 실행하지 않는다.

실행 예:
    python scripts/apply_migration.py 004_cb_demo_fields.sql
    python scripts/apply_migration.py 004_cb_demo_fields.sql --apply
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.cb import db  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("migrate")

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


async def run(name: str, apply: bool) -> None:
    path = MIGRATIONS_DIR / name
    if not path.exists():
        raise SystemExit(f"없는 마이그레이션: {path}")
    sql = path.read_text(encoding="utf-8")

    statements = [s for s in (x.strip() for x in sql.split(";")) if s and not s.startswith("--")]
    print(f"\n{path.name} — 실행할 문장 {len(statements)}개")
    for s in statements:
        head = " ".join(s.split())[:100]
        print(f"  {head}…")

    if not apply:
        logger.info("dry-run입니다. 적용하려면 --apply")
        return

    pool = await db.connect()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(sql)
    logger.warning("%s 적용 완료", path.name)

    async with pool.acquire() as conn:
        cols = await conn.fetch(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema='cb' AND table_name='cb_institutions' "
            "  AND column_name IN ('apply_deadline','apply_period_start',"
            "    'result_announcement_date','is_demo_deadline','required_documents_ai',"
            "    'required_documents_generated_at','required_documents_source') "
            "ORDER BY column_name"
        )
        print("\n적용 후 컬럼:")
        for c in cols:
            print(f"  {c['column_name']:<34} {c['data_type']:<26} null={c['is_nullable']}")


async def main_async(args) -> None:
    try:
        await run(args.name, args.apply)
    finally:
        await db.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="cb 마이그레이션 적용")
    parser.add_argument("name", help="scripts/migrations/ 안의 파일명")
    parser.add_argument("--apply", action="store_true", help="실제로 적용한다")
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
