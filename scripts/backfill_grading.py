"""제도 원문 → disability_severity / income_pct_max 백필.

전부 regex + 사전 매핑이다. LLM을 부르지 않는다 — 법정 고정값이라 재현성이
있어야 하고, 같은 원문에 대해 매번 같은 값이 나와야 사람이 검증할 수 있다.

실행 예:
    # 값을 쓰지 않고 분포만 본다
    python scripts/backfill_grading.py --dry-run

    # 856건 백필 + 리포트
    python scripts/backfill_grading.py

    # 사람이 확인할 표본을 CSV로 (기본 경로: scripts/out/grading_review.csv)
    python scripts/backfill_grading.py --dry-run --csv scripts/out/grading_review.csv

판정에 쓰는 원문 범위:
    serv_nm + serv_dgst + target_detail + select_criteria
    service_content(서비스내용)은 넣지 않는다. '장애인 활동지원 인력을 파견'처럼
    제공 내용 쪽에 낱말이 스치기만 해도 잡혀서, 자격과 무관한 제도에 값이 붙는다.
"""
import argparse
import asyncio
import csv
import logging
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.cb import db, grading  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("backfill")

DEFAULT_CSV = Path(__file__).resolve().parent / "out" / "grading_review.csv"

# 자격 판정 근거로 볼 필드. 순서가 곧 원문 조립 순서다.
_SOURCE_FIELDS = ("serv_nm", "serv_dgst", "target_detail", "select_criteria")

# "소득 이야기는 하는데 값이 안 나온" 행을 찾기 위한 말들.
# 이 행들이 3-2의 3번(둘 다 안 걸림)이고, 사람이 확인할 표본이다.
_INCOME_MENTION = re.compile(
    r"소득|수급|급여|차상위|저소득|기초생활|재산|중위|기초연금|한부모"
)


def source_text(row: Dict[str, Any]) -> str:
    return "\n".join((row.get(f) or "") for f in _SOURCE_FIELDS)


def classify_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """행 1건을 분류한다. DB에 쓸 값과 리포트/CSV용 근거를 함께 담는다."""
    body = source_text(row)
    severity, severity_rule, severity_evidence = grading.classify_severity(body)
    income, income_rule, income_evidence = grading.classify_income(body)
    return {
        "serv_id": row["serv_id"],
        "serv_nm": row.get("serv_nm") or "",
        "disability_severity": severity,
        "income_pct_max": income,
        "severity_rule": severity_rule,
        "severity_evidence": severity_evidence,
        "income_rule": income_rule,
        "income_evidence": income_evidence,
        "has_disability_tag": "장애인" in (row.get("household_tags") or []),
        "mentions_income": bool(_INCOME_MENTION.search(body)),
    }


# --- 리포트 -------------------------------------------------------------------
def _pct(part: int, whole: int) -> str:
    return f"{part / whole * 100:5.1f}%" if whole else "    -"


def print_report(results: List[Dict[str, Any]]) -> None:
    total = len(results)
    print()
    print("=" * 72)
    print(f" 백필 분포 리포트 — 전체 {total}건")
    print("=" * 72)

    # --- 장애 중증도 ---------------------------------------------------------
    mentions = [r for r in results if r["disability_severity"] is not None]
    print()
    print(f"[장애 중증도] 장애 언급 {len(mentions)}건 / 미언급(NULL) "
          f"{total - len(mentions)}건 ({_pct(len(mentions), total)} 언급)")
    counts = Counter(r["disability_severity"] for r in mentions)
    for value in grading.SEVERITY_VALUES:
        n = counts.get(value, 0)
        print(f"    {value:<10} {n:>4}건  {_pct(n, len(mentions))} (언급 건 대비)")
    print("    규칙별:")
    for rule, n in Counter(r["severity_rule"] for r in mentions).most_common():
        print(f"      {rule:<14} {n:>4}건")

    # household_tags '장애인' 태그와의 대조. 태그는 72%가 LLM이 붙인 것이라
    # 원문 기반 판정과 어긋나는 건이 곧 태그 품질 문제다.
    tagged = {r["serv_id"] for r in results if r["has_disability_tag"]}
    mentioned = {r["serv_id"] for r in mentions}
    print(f"    household_tags '장애인' 태그 {len(tagged)}건 / "
          f"원문 장애 언급 {len(mentioned)}건")
    print(f"      태그 O · 원문 X: {len(tagged - mentioned):>4}건 (태그 오탐 의심)")
    print(f"      태그 X · 원문 O: {len(mentioned - tagged):>4}건 (태그 누락)")

    # --- 소득 구간 -----------------------------------------------------------
    filled = [r for r in results if r["income_pct_max"] is not None]
    null_rows = [r for r in results if r["income_pct_max"] is None]
    null_mentions = [r for r in null_rows if r["mentions_income"]]
    print()
    print(f"[소득 구간] 값 있음 {len(filled)}건 ({_pct(len(filled), total)}) / "
          f"NULL {len(null_rows)}건")
    print("    규칙별:")
    for rule, n in Counter(r["income_rule"] for r in filled).most_common():
        print(f"      {rule:<14} {n:>4}건  {_pct(n, len(filled))}")
    print(f"    NULL 중 소득 관련 낱말이 있는 행: {len(null_mentions)}건 "
          f"← 사람 확인 대상 (3-2의 3번)")
    print("    income_pct_max 값 분포:")
    for pct, n in sorted(Counter(r["income_pct_max"] for r in filled).items()):
        print(f"      {pct:>3}% {'#' * min(n, 50)} {n}건")

    # --- 사람 확인 대상 -------------------------------------------------------
    ambiguous = [r for r in results if r["severity_rule"] == grading.RULE_AMBIGUOUS]
    print()
    print(f"[사람 확인 대상] 중증도 애매 {len(ambiguous)}건 (2-2의 3번 예외) / "
          f"소득 근거 없음 {len(null_mentions)}건 (3-2의 3번)")
    print("=" * 72)
    print()


# --- CSV ----------------------------------------------------------------------
_CSV_COLUMNS = [
    "kind", "serv_id", "serv_nm", "rule",
    "disability_severity", "income_pct_max", "evidence", "source_excerpt",
]


def write_csv(results: List[Dict[str, Any]], rows_by_id: Dict[str, Dict[str, Any]],
              path: Path, excerpt_chars: int = 400) -> int:
    """사람이 확인할 표본을 CSV로 뽑는다.

    두 종류를 한 파일에 담고 kind 열로 구분한다.
      severity_ambiguous : 1~3급과 4~6급이 함께 언급돼 정도로 가를 수 없는 건
      income_unresolved  : 소득 이야기는 하는데 %도 카테고리도 안 걸린 건

    Excel이 한글을 깨뜨리지 않도록 utf-8-sig로 쓴다.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    picked: List[Dict[str, Any]] = []

    for result in results:
        if result["severity_rule"] == grading.RULE_AMBIGUOUS:
            picked.append({**result, "kind": "severity_ambiguous",
                           "rule": result["severity_rule"],
                           "evidence": result["severity_evidence"]})
        elif result["income_pct_max"] is None and result["mentions_income"]:
            picked.append({**result, "kind": "income_unresolved",
                           "rule": result["income_rule"],
                           "evidence": result["income_evidence"]})

    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_CSV_COLUMNS,
                                extrasaction="ignore")
        writer.writeheader()
        for item in picked:
            raw = rows_by_id.get(item["serv_id"]) or {}
            excerpt = " ".join(source_text(raw).split())[:excerpt_chars]
            writer.writerow({**item, "source_excerpt": excerpt})

    logger.info("사람 확인용 CSV %d행 → %s", len(picked), path)
    return len(picked)


# --- 실행 ---------------------------------------------------------------------
async def run(dry_run: bool, csv_path: Optional[Path]) -> None:
    rows = await db.fetch_rows_for_grading()
    logger.info("대상 %d건 조회", len(rows))

    results = [classify_row(row) for row in rows]
    print_report(results)

    if csv_path is not None:
        write_csv(results, {r["serv_id"]: r for r in rows}, csv_path)

    if dry_run:
        logger.info("--dry-run: DB에 쓰지 않았습니다")
        return

    saved = await db.save_grading([
        {
            "serv_id": r["serv_id"],
            "disability_severity": r["disability_severity"],
            "income_pct_max": r["income_pct_max"],
        }
        for r in results
    ])
    logger.info("백필 저장 %d건", saved)


async def main_async(args: argparse.Namespace) -> None:
    """풀을 연 루프 안에서 닫는다.

    asyncio.run을 두 번 부르면 두 번째는 새 이벤트 루프라, 첫 루프에 묶인
    asyncpg 풀을 닫을 때 'Event loop is closed'가 쏟아진다.
    """
    try:
        await run(args.dry_run, Path(args.csv) if args.csv else None)
    finally:
        await db.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="장애 중증도/소득 구간 백필")
    parser.add_argument("--dry-run", action="store_true",
                        help="DB에 쓰지 않고 분포만 출력한다")
    parser.add_argument("--csv", nargs="?", const=str(DEFAULT_CSV), default=None,
                        help="사람 확인용 표본 CSV 경로 (기본 scripts/out/grading_review.csv)")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
