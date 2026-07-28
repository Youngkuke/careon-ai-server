"""required_documents_ai 사후 보정 2종.

백필이 끝난 뒤 발견된 두 가지를 고친다. 배치를 856건 다시 돌리지 않고
해당 행만 손본다.

  1) 신청을 아예 받지 않는 제도인데 서류가 채워진 건 → 빈 배열로
  2) '통장 사본'(띄어쓰기) 표기를 '통장사본'(붙임)으로 통일

기본이 dry-run이다. 실제로 쓰려면 --apply를 붙여야 한다.

실행 예:
    python scripts/fix_required_documents.py                # 영향 범위만 출력
    python scripts/fix_required_documents.py --apply        # 둘 다 적용
    python scripts/fix_required_documents.py --only 1 --apply
"""
import argparse
import asyncio
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.cb import db  # noqa: E402
from backfill_required_documents import _NOT_SUBMISSION  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("fix-docs")


# --- 1번: 신청을 받지 않는 제도 -------------------------------------------------
# '이 제도는 신청 자체를 받지 않는다'가 문장 전체의 뜻인 표현만 잡는다.
# 실측(856건)에서 '직접 지원(별도 신청사항 없음)'이 압도적으로 흔하다.
_NO_APPLY = re.compile(
    r"별도\s*신청\s*사항\s*없|별도\s*신청\s*절차\s*없|직접\s*지원\s*\(\s*별도"
)

# 같은 본문에 이게 있으면 '신청이 아예 없다'가 아니다. 조건부이거나
# ('단, ~는 신청 불필요') 일부 사업에만 해당하는 경우다.
_STILL_APPLIES = re.compile(
    r"제출\s*서류|구비\s*서류|신청서\b|단,|누락\s*시|미신청|신청\s*방법\s*:"
    r"|방문\s*신청|온라인\s*신청|추가\s*신청"
)

# basfrm에 들어 있지만 제출 서류가 아닌 것. backfill의 _NOT_SUBMISSION이
# 잡지 못한 것들을 여기서 더 건다. 실측에서 이 제도들의 basfrm은 전부
# 신청서가 아니었다:
#   '첨부파일없음.hwp' / '(보건복지부공문)…협조요청.hwp' / '장애인복지법(법률).pdf'
#   '2023년 저소득 한부모가족 지원 추진계획.hwp' / '…기본계획(안)_비배포.pdf'
# 이걸 안 걸면 '첨부파일없음.hwp' 하나 때문에 신청서가 있는 제도로 오인된다.
#
# '계획'은 뒤에 '서'가 오면 제외하지 않는다 — '디딤씨앗통장 적립 및 사용계획서'
# 처럼 신청자가 직접 작성해 내는 진짜 서식이기 때문이다.
_EMPTY_FORM = re.compile(
    r"첨부\s*파일\s*없|해당\s*없음|파일\s*없음"
    r"|공문|협조\s*요청|법률|시행령|시행규칙|계획(?!서)|현행화"
)


def real_submission_forms(extra_info: Dict[str, Any]) -> List[str]:
    """진짜 제출 서식만. 조례·지침·계획·공문·'첨부파일없음'은 뺀다."""
    out = []
    for item in (extra_info or {}).get("basfrm") or []:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        if not name or _NOT_SUBMISSION.search(name) or _EMPTY_FORM.search(name):
            continue
        out.append(name)
    return out


def plan_no_apply(rows: List[Dict[str, Any]]) -> Tuple[List[Dict], List[Dict]]:
    """(보정 대상, 제외 대상). 제외에는 왜 뺐는지를 함께 담는다."""
    targets, skipped = [], []
    for row in rows:
        body = row.get("apply_method") or ""
        if not _NO_APPLY.search(body):
            continue
        docs = row.get("required_documents_ai")
        if isinstance(docs, str):
            docs = json.loads(docs or "[]")
        docs = docs or []

        reasons = []
        if _STILL_APPLIES.search(body):
            reasons.append("본문에 신청/서류 안내가 함께 있음")
        forms = real_submission_forms(row.get("extra_info") or {})
        if forms:
            reasons.append("실제 제출 서식 있음(%s)" % forms[0][:34])

        item = {
            "serv_id": row["serv_id"], "serv_nm": row.get("serv_nm") or "",
            "source": row.get("required_documents_source"), "ndocs": len(docs),
            "reasons": reasons,
        }
        (skipped if reasons else targets).append(item)
    return targets, skipped


async def run_no_apply(apply: bool) -> None:
    pool = await db.connect()
    async with pool.acquire() as conn:
        rows = [dict(r) for r in await conn.fetch(
            "SELECT serv_id, serv_nm, apply_method, extra_info, "
            "       required_documents_ai, required_documents_source "
            "FROM cb.cb_institutions WHERE apply_method IS NOT NULL"
        )]
    for r in rows:
        if isinstance(r.get("extra_info"), str):
            r["extra_info"] = json.loads(r["extra_info"] or "{}")

    targets, skipped = plan_no_apply(rows)
    changing = [t for t in targets if t["ndocs"] > 0]

    print()
    print("=" * 74)
    print(" 1번: 신청을 받지 않는 제도 → required_documents_ai = []")
    print("=" * 74)
    print("  문구 일치        %d건" % (len(targets) + len(skipped)))
    print("  보정 대상        %d건 (이미 빈 배열 %d건 제외)"
          % (len(changing), len(targets) - len(changing)))
    print("  제외             %d건" % len(skipped))
    for t in changing:
        print("    %-14s %-40s 서류%d개 → []" % (t["serv_id"], t["serv_nm"][:40], t["ndocs"]))
    if skipped:
        print("\n  [제외 — 신청이 실제로 있는 것으로 보임]")
        for s in skipped:
            print("    %-14s %-32s ← %s" % (s["serv_id"], s["serv_nm"][:32],
                                            "; ".join(s["reasons"])))

    if not apply or not changing:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE cb.cb_institutions "
            "SET required_documents_ai = '[]'::jsonb, "
            "    required_documents_source = 'none' "
            "WHERE serv_id = ANY($1::text[])",
            [t["serv_id"] for t in changing],
        )
    logger.warning("1번 보정 %d건 적용", len(changing))


# --- 2번: '통장 사본' 표기 통일 -------------------------------------------------
OLD_NAME, NEW_NAME = "통장 사본", "통장사본"

# 이름 전체가 정확히 '통장 사본'인 항목만 바꾼다. '본인 명의 통장 사본'처럼
# 수식어가 붙은 것은 다른 서류명이라 건드리지 않는다 — 뭉뚱그리면 정보가 준다.
_NORMALIZE_SQL = """
UPDATE cb.cb_institutions
   SET required_documents_ai = (
        SELECT jsonb_agg(
                 CASE WHEN d->>'name' = $1
                      THEN jsonb_set(d, '{name}', to_jsonb($2::text))
                      ELSE d END
                 ORDER BY ord)
          FROM jsonb_array_elements(required_documents_ai)
               WITH ORDINALITY AS t(d, ord))
 WHERE required_documents_ai @> jsonb_build_array(jsonb_build_object('name', $1::text))
"""


async def run_normalize(apply: bool) -> None:
    pool = await db.connect()
    async with pool.acquire() as conn:
        affected = await conn.fetchval(
            "SELECT count(*) FROM cb.cb_institutions "
            "WHERE required_documents_ai @> jsonb_build_array("
            "        jsonb_build_object('name', $1::text))",
            OLD_NAME,
        )
        variants = await conn.fetch(
            "SELECT d->>'name' AS n, count(*) AS c "
            "FROM cb.cb_institutions, jsonb_array_elements(required_documents_ai) d "
            "WHERE d->>'name' LIKE '%통장%' AND d->>'name' <> $1 AND d->>'name' <> $2 "
            "GROUP BY 1 ORDER BY 2 DESC LIMIT 8",
            OLD_NAME, NEW_NAME,
        )

    print()
    print("=" * 74)
    print(" 2번: '%s' → '%s'" % (OLD_NAME, NEW_NAME))
    print("=" * 74)
    print("  영향받는 행(제도) 수: %d" % affected)
    print("  건드리지 않는 변형 (수식어가 붙어 다른 서류명):")
    for v in variants:
        print("    %-34s %d개" % (v["n"][:34], v["c"]))

    if not apply or not affected:
        return
    async with pool.acquire() as conn:
        await conn.execute(_NORMALIZE_SQL, OLD_NAME, NEW_NAME)
    logger.warning("2번 보정 %d행 적용", affected)


async def main_async(args: argparse.Namespace) -> None:
    try:
        if args.only in (None, 1):
            await run_no_apply(args.apply)
        if args.only in (None, 2):
            await run_normalize(args.apply)
        if not args.apply:
            print()
            logger.info("dry-run입니다. DB에 쓰지 않았습니다 — 실제로 쓰려면 --apply")
    finally:
        await db.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="required_documents_ai 사후 보정")
    parser.add_argument("--apply", action="store_true", help="실제로 DB에 쓴다")
    parser.add_argument("--only", type=int, choices=[1, 2], default=None,
                        help="한 가지만 실행 (1=신청불필요, 2=통장사본 표기)")
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
