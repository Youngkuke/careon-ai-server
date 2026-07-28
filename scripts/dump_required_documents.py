"""required_documents_ai 현황을 리뷰용 CSV로 뽑는다 (DB 현재 상태 기준).

배치 실행 중에 만들어지는 CSV는 그 실행분만 담고, 이후 보정을 반영하지
못한다. 이 스크립트는 DB를 단일 소스로 삼아 856건 전부를 다시 뽑는다.
배치를 돌리거나 보정한 뒤에 실행해서 파일을 갱신한다.

  python scripts/dump_required_documents.py

서류 1개 = 1행이다. 서류가 없는 제도(빈 배열)도 document 칸이 빈 1행으로
남는다 — 빠지면 '누락'과 '서류 없음'을 구분할 수 없다.

confidence와 note는 들어가지 않는다. 두 값은 배치 실행 중에만 존재하고
DB에 저장하지 않기 때문이다. 대신 판단 근거를 되짚을 수 있게
provision_type·basfrm 개수·apply_method 앞부분을 함께 담는다.
"""
import argparse
import asyncio
import csv
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.cb import db  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("dump-docs")

DEFAULT_CSV = Path(__file__).resolve().parent / "out" / "required_documents_review.csv"

COLUMNS = [
    "serv_id", "serv_nm", "source", "generated_at",
    "doc_no", "document", "url", "url_type",
    "n_docs", "provision_type", "n_basfrm", "apply_method_head",
]

QUERY = """
SELECT serv_id, serv_nm, required_documents_ai AS docs,
       required_documents_source AS source,
       required_documents_generated_at AS generated_at,
       provision_type,
       jsonb_array_length(coalesce(extra_info->'basfrm','[]'::jsonb)) AS n_basfrm,
       left(regexp_replace(coalesce(apply_method,''), '\\s+', ' ', 'g'), 120) AS apply_head
  FROM cb.cb_institutions
 ORDER BY required_documents_source, serv_id
"""


async def run(path: Path) -> None:
    pool = await db.connect()
    async with pool.acquire() as conn:
        rows = await conn.fetch(QUERY)

    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        # 헤더 앞 주석. 이 파일이 무엇의 스냅샷인지 파일 안에 남긴다.
        handle.write(
            "# cb.cb_institutions.required_documents_ai 스냅샷 "
            "(DB 현재 상태, 서류 1개 = 1행). "
            "confidence/note는 배치 실행 중에만 존재해 DB에 없다. "
            "재생성: python scripts/dump_required_documents.py\n"
        )
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()

        for r in rows:
            docs = r["docs"]
            if isinstance(docs, str):
                docs = json.loads(docs or "[]")
            docs = docs or []
            base = {
                "serv_id": r["serv_id"],
                "serv_nm": r["serv_nm"] or "",
                "source": r["source"] or "",
                "generated_at": r["generated_at"].isoformat() if r["generated_at"] else "",
                "n_docs": len(docs),
                "provision_type": r["provision_type"] or "",
                "n_basfrm": r["n_basfrm"],
                "apply_method_head": r["apply_head"] or "",
            }
            if not docs:
                writer.writerow({**base, "doc_no": 0, "document": "",
                                 "url": "", "url_type": ""})
                written += 1
                continue
            for i, d in enumerate(docs, 1):
                writer.writerow({
                    **base, "doc_no": i,
                    "document": d.get("name") or "",
                    "url": d.get("url") or "",
                    "url_type": d.get("url_type") or "",
                })
                written += 1

    logger.info("제도 %d건 → %d행 (%s)", len(rows), written, path)


async def main_async(args: argparse.Namespace) -> None:
    try:
        await run(Path(args.out))
    finally:
        await db.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="필요서류 현황 CSV 덤프")
    parser.add_argument("--out", default=str(DEFAULT_CSV), help="출력 경로")
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
