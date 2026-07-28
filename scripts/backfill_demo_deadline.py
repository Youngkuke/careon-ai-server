"""데모용 신청 일정 백필 (apply_period_start / apply_deadline / result_announcement_date).

경고 — 여기서 넣는 날짜는 전부 지어낸 값이다.
    복지로 원본에는 신청 일정이 없다. 데모 화면의 D-day 배지와 일정 행을
    채우려고 무작위로 만든 날짜다. 실제 일정이 아니며, 사용자가 이 날짜를
    믿고 신청을 미루거나 포기하면 실제 수급 기회를 놓친다.

    그래서 값을 쓴 행은 전부 is_demo_deadline = TRUE로 표시한다. 데모가 아닌
    화면은 이 플래그가 TRUE인 행의 날짜를 노출하면 안 된다.

    실시간 챗봇 답변 경로는 이 컬럼을 읽지 않는다 (004 마이그레이션 주석).

두 갈래로 나뉜다:
    support_cycle = '수시'  → 날짜 3종 전부 NULL, is_demo_deadline FALSE
        상시 접수라 마감 개념이 없다. 화면에서 '상시 접수'로 렌더링한다.
        (문자열 '상시'를 넣지 않는 이유: 이 컬럼들은 'YYYY-MM-DD' 계약이고,
         여기에 '상시'가 섞이면 프론트가 파싱 전에 값을 먼저 검사해야 한다.
         마감이 없다는 사실은 support_cycle이 이미 말해준다.)
    그 외                   → 아래 규칙으로 3종을 만든다

# 날짜 생성 규칙

    apply_deadline           2026-08-01 ~ 2026-09-30 균등 랜덤
    apply_period_start       80%는 오늘 - 0~25일, 20%는 오늘 + 1~14일
                             그 뒤 '마감 7일 전'을 넘지 않도록 당긴다
    result_announcement_date 마감 + 7~21일 랜덤

    apply_period_end 는 컬럼이 없다. apply_deadline과 항상 같은 값이라
    두 벌로 저장하면 한쪽만 고쳤을 때 어긋난다. 응답에서 파생시킨다.

## 시작일을 '오늘' 기준으로 잡는 이유

    마감에서 7~30일만 역산하면(원래 안), 마감이 08-01~09-30 균등이라
    시작일 평균이 08-14가 되어 오늘(07-28) 기준 662건 중 508건(77%)이
    '아직 시작 안 함'으로 읽힌다. 데모 카드 4장 중 3장이 '신청 예정'이 된다.

    오늘 기준으로 잡으면 545건(82%)이 '진행 중'이 되고, 117건은 '곧 시작'으로
    남아 화면에 변화가 생긴다. 실측 비교는 대화 기록 참고.

## 불변식

    apply_period_start < apply_deadline < result_announcement_date

    시작일은 항상 마감 7일 전 이하로 당겨지므로 '시작 전인데 이미 마감'은
    산술적으로 발생할 수 없다. verify_invariants()가 매 실행마다 확인한다.

기본이 dry-run이다. 실제로 쓰려면 --apply를 붙여야 한다.
(scripts/backfill_grading.py와 반대다. 그쪽은 원문에서 뽑은 값이라 재현되지만,
 여기는 지어낸 값이라 한 번 쓰면 원래 상태를 복원할 수 없다.)

실행 예:
    # 무엇이 들어갈지만 본다 (DB에 쓰지 않는다)
    python scripts/backfill_demo_deadline.py

    # 표본 20건까지 같이 본다
    python scripts/backfill_demo_deadline.py --sample 20

    # 실제로 쓴다
    python scripts/backfill_demo_deadline.py --apply

    # 데모 값을 전부 되돌린다
    python scripts/backfill_demo_deadline.py --clear --apply

재현이 필요하면 --seed로 고정한다. 기본은 매번 다른 날짜가 나온다.
--today로 기준일을 바꿔 다른 시점의 화면을 미리 볼 수 있다.
"""
import argparse
import asyncio
import logging
import random
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.cb import db  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("demo-deadline")

# 마감일을 뿌릴 구간 (양끝 포함).
DEADLINE_START = date(2026, 8, 1)
DEADLINE_END = date(2026, 9, 30)

# 시작일: 이 비율만큼은 '이미 시작한' 상태로 만든다.
ALREADY_OPEN_RATIO = 0.8
STARTED_DAYS_AGO = (0, 25)      # 오늘로부터 며칠 전에 시작했나
UPCOMING_DAYS_AHEAD = (1, 14)   # 아직 시작 전인 건은 며칠 뒤에 시작하나
# 시작일과 마감일 사이 최소 간격. 이 값이 불변식을 보장한다.
MIN_PERIOD_DAYS = 7

# 결과 발표일: 마감으로부터 며칠 뒤.
ANNOUNCE_AFTER_DAYS = (7, 21)

# 마감 개념이 없는 지급 주기. 이 값의 제도는 NULL로 남긴다.
ALWAYS_OPEN_CYCLE = "수시"


def plan_row(row: Dict[str, Any], rng: random.Random, today: date) -> Dict[str, Any]:
    """행 1건이 받을 값을 정한다. DB에 쓰는 것과 리포트에 쓰는 것을 함께 담는다."""
    always_open = (row.get("support_cycle") or "").strip() == ALWAYS_OPEN_CYCLE

    if always_open:
        start = deadline = announce = None
    else:
        span = (DEADLINE_END - DEADLINE_START).days
        deadline_date = DEADLINE_START + timedelta(days=rng.randint(0, span))

        if rng.random() < ALREADY_OPEN_RATIO:
            start_date = today - timedelta(days=rng.randint(*STARTED_DAYS_AGO))
        else:
            start_date = today + timedelta(days=rng.randint(*UPCOMING_DAYS_AHEAD))
        # 마감 7일 전을 넘지 않게 당긴다. 이 한 줄이 start < deadline을 보장한다.
        start_date = min(start_date, deadline_date - timedelta(days=MIN_PERIOD_DAYS))

        announce_date = deadline_date + timedelta(days=rng.randint(*ANNOUNCE_AFTER_DAYS))

        start = start_date.isoformat()
        deadline = deadline_date.isoformat()
        announce = announce_date.isoformat()

    return {
        "serv_id": row["serv_id"],
        "serv_nm": row.get("serv_nm") or "",
        "support_cycle": row.get("support_cycle"),
        "apply_period_start": start,
        "apply_deadline": deadline,
        "result_announcement_date": announce,
        # 지어낸 날짜를 넣은 행만 TRUE다. 수시는 값을 안 넣었으므로 FALSE.
        "is_demo_deadline": deadline is not None,
    }


def verify_invariants(plans: List[Dict[str, Any]], today: date) -> List[str]:
    """날짜 3종의 불변식을 확인한다. 위반이 있으면 저장하지 않는다.

    '시작 전인데 이미 마감'은 규칙상 나올 수 없지만, 규칙을 나중에 손대는
    사람이 그 사실을 모를 수 있다. 검사를 코드로 남겨둔다.
    """
    problems: List[str] = []
    for p in plans:
        start, deadline = p["apply_period_start"], p["apply_deadline"]
        announce = p["result_announcement_date"]
        filled = [x for x in (start, deadline, announce) if x]
        if not filled:
            continue
        if len(filled) != 3:
            problems.append(f"{p['serv_id']}: 날짜 3종이 부분적으로만 찼다")
            continue
        if not start < deadline:
            problems.append(f"{p['serv_id']}: 시작 {start} >= 마감 {deadline}")
        if not deadline < announce:
            problems.append(f"{p['serv_id']}: 마감 {deadline} >= 발표 {announce}")
        if start > today.isoformat() and deadline < today.isoformat():
            problems.append(f"{p['serv_id']}: 시작 전({start})인데 마감됨({deadline})")
    return problems


def _state(plan: Dict[str, Any], today: str) -> str:
    """오늘 기준으로 이 제도가 화면에 어떻게 보이는가."""
    start, deadline = plan["apply_period_start"], plan["apply_deadline"]
    if not deadline:
        return "상시 접수"
    if today < start:
        return "신청 예정"
    if today <= deadline:
        return "신청 진행 중"
    if today < plan["result_announcement_date"]:
        return "마감·결과 대기"
    return "결과 발표 완료"


def print_report(plans: List[Dict[str, Any]], sample: int, today: date) -> None:
    total = len(plans)
    filled = [p for p in plans if p["apply_deadline"]]
    skipped = [p for p in plans if not p["apply_deadline"]]
    today_str = today.isoformat()

    print()
    print("=" * 72)
    print(f" 데모 신청 일정 백필 계획 — 전체 {total}건 (기준일 {today})")
    print("=" * 72)
    print(f"  날짜 채움 (is_demo_deadline=TRUE) : {len(filled):>4}건")
    print(f"  NULL 유지 (수시, FALSE)           : {len(skipped):>4}건")
    print(f"  마감 구간                         : {DEADLINE_START} ~ {DEADLINE_END}")

    print("\n  [support_cycle 별]")
    for cycle, n in Counter(p["support_cycle"] for p in plans).most_common():
        mark = "NULL 유지" if (cycle or "").strip() == ALWAYS_OPEN_CYCLE else "날짜 채움"
        print(f"    {str(cycle):<8} {n:>4}건  → {mark}")

    print(f"\n  [오늘({today}) 기준 화면 상태]")
    for state, n in Counter(_state(p, today_str) for p in plans).most_common():
        print(f"    {state:<16} {n:>4}건 ({n / total * 100:4.0f}%)")

    if filled:
        starts = [p["apply_period_start"] for p in filled]
        announces = [p["result_announcement_date"] for p in filled]
        print("\n  [범위]")
        print(f"    시작일   {min(starts)} ~ {max(starts)}")
        print(f"    마감일   {min(p['apply_deadline'] for p in filled)} ~ "
              f"{max(p['apply_deadline'] for p in filled)}")
        print(f"    발표일   {min(announces)} ~ {max(announces)}")

        print("\n  [마감 월별]")
        for month, n in sorted(Counter(p["apply_deadline"][:7] for p in filled).items()):
            print(f"    {month} {'#' * min(n // 8, 50)} {n}건")

    if sample and filled:
        print(f"\n  [표본 {min(sample, len(filled))}건]  시작 → 마감 → 발표")
        for p in filled[:sample]:
            print(f"    {p['apply_period_start']} → {p['apply_deadline']} → "
                  f"{p['result_announcement_date']}  {p['serv_nm'][:32]}")

    print("=" * 72)
    print()


async def run(args: argparse.Namespace) -> None:
    if args.clear:
        await run_clear(args.apply)
        return

    today = date.fromisoformat(args.today) if args.today else date.today()
    rows = await db.fetch_rows_for_deadline()
    logger.info("대상 %d건 조회", len(rows))

    rng = random.Random(args.seed)
    plans = [plan_row(row, rng, today) for row in rows]

    problems = verify_invariants(plans, today)
    print_report(plans, args.sample, today)

    if problems:
        # 불변식이 깨진 채로 쓰면 화면에 앞뒤가 안 맞는 일정이 뜬다.
        logger.error("불변식 위반 %d건 — 저장하지 않습니다:", len(problems))
        for item in problems[:10]:
            logger.error("  %s", item)
        return
    logger.info("불변식 확인: 시작 < 마감 < 발표, '시작 전 마감' 0건")

    if not args.apply:
        logger.info("dry-run입니다. DB에 쓰지 않았습니다 — 실제로 쓰려면 --apply")
        return

    saved = await db.save_demo_deadlines(plans)
    demo_count = sum(1 for p in plans if p["is_demo_deadline"])
    logger.warning(
        "데모 신청 일정 %d건 저장 (지어낸 날짜 %d건에 is_demo_deadline=TRUE)",
        saved, demo_count,
    )


async def run_clear(apply: bool) -> None:
    """데모로 넣은 값만 되돌린다. is_demo_deadline이 아닌 행은 건드리지 않는다."""
    pool = await db.connect()
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM cb.cb_institutions WHERE is_demo_deadline"
        )
        print(f"\n  되돌릴 대상: is_demo_deadline=TRUE {count}건\n")
        if not apply:
            logger.info("dry-run입니다. 지우지 않았습니다 — 실제로 지우려면 --apply")
            return
        await conn.execute(
            "UPDATE cb.cb_institutions "
            "SET apply_deadline = NULL, apply_period_start = NULL, "
            "    result_announcement_date = NULL, is_demo_deadline = FALSE "
            "WHERE is_demo_deadline"
        )
    logger.warning("데모 신청 일정 %d건 삭제", count)


async def main_async(args: argparse.Namespace) -> None:
    """풀을 연 루프 안에서 닫는다 (backfill_grading.py와 같은 이유)."""
    try:
        await run(args)
    finally:
        await db.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="데모용 신청 일정 백필 (지어낸 날짜를 넣는다)"
    )
    parser.add_argument("--apply", action="store_true",
                        help="실제로 DB에 쓴다. 없으면 dry-run이다")
    parser.add_argument("--clear", action="store_true",
                        help="is_demo_deadline=TRUE인 행의 데모 값을 되돌린다")
    parser.add_argument("--seed", type=int, default=None,
                        help="난수 시드. 같은 날짜를 재현하고 싶을 때만 쓴다")
    parser.add_argument("--today", default=None,
                        help="기준일 'YYYY-MM-DD' (기본: 실행일). "
                             "다른 시점의 화면을 미리 볼 때 쓴다")
    parser.add_argument("--sample", type=int, default=10,
                        help="리포트에 찍을 표본 건수 (기본 10)")
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
