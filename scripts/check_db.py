"""DB 연결 + 스키마/데이터 존재 확인."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402


async def main() -> None:
    await db.connect()
    if not db.available():
        print("DATABASE_URL이 비어 있습니다.")
        return

    tables = await db.fetch(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' ORDER BY table_name"
    )
    print("테이블:", ", ".join(t["table_name"] for t in tables) or "(없음)")

    for table in ("policies", "policy_types", "agencies", "documents", "carers", "cared"):
        try:
            row = await db.fetchrow("SELECT count(*) AS c FROM {}".format(table))
            print("  {:<28} {} rows".format(table, row["c"]))
        except Exception as exc:  # noqa: BLE001
            print("  {:<28} ERROR: {}".format(table, exc))

    sample = await db.fetch(
        "SELECT policy_id, policy_name, age_min, age_max FROM policies ORDER BY policy_id LIMIT 3"
    )
    for s in sample:
        print("  #{} {} ({}~{})".format(s["policy_id"], s["policy_name"], s["age_min"], s["age_max"]))

    await db.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
