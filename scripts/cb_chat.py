"""챗봇 검색엔진(cb) 대화 테스트 REPL — 서버 없이 그래프를 직접 호출한다.

사용법:
    .venv\\Scripts\\python.exe scripts\\cb_chat.py
    .venv\\Scripts\\python.exe scripts\\cb_chat.py --region 강남구
    .venv\\Scripts\\python.exe scripts\\cb_chat.py --debug     # 노드 로그까지 표시

대화 중 명령어:
    /help          명령어 목록
    /state         지금까지 누적된 필터와 지역
    /list          마지막 검색 결과 전체 (점수 포함)
    /detail N      N번 제도의 원문 보기 (검색이 왜 그걸 골랐는지 확인용)
    /why           마지막 검색의 벡터/키워드 순위 내역
    /region 은평구  거주 자치구 변경
    /new           새 대화 시작 (누적 필터 초기화)
    /quit          종료
"""
import argparse
import asyncio
import logging
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# graph를 import하는 시점에 Windows 이벤트 루프 정책이 잡힌다. 다른 것보다 먼저.
from app.cb import graph  # noqa: E402
from app.cb import constants, db, embedding, search  # noqa: E402

BANNER = """
┌──────────────────────────────────────────────────────────────┐
│  CareOn 챗봇 검색엔진 — 대화 테스트                          │
│  제도 856건 · 하이브리드 검색(벡터+키워드)                   │
│  /help 로 명령어 확인, /quit 로 종료                         │
└──────────────────────────────────────────────────────────────┘
"""

DIM = "\033[2m"
BOLD = "\033[1m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
RESET = "\033[0m"


def c(text: str, color: str) -> str:
    return f"{color}{text}{RESET}"


class Session:
    def __init__(self, region: Optional[str], user_id: int = 1):
        self.user_id = user_id
        self.region = region
        self.new_thread()

    def new_thread(self) -> None:
        self.thread_id = "cli-%s" % uuid.uuid4().hex[:10]
        self.last: Dict[str, Any] = {}
        self.turns = 0


def print_help() -> None:
    print(c("""
  /state         누적된 필터와 지역
  /list          마지막 검색 결과 전체 (점수 포함)
  /detail N      N번 제도 원문 보기
  /why           마지막 검색의 벡터/키워드 순위 내역
  /region 은평구  거주 자치구 변경
  /new           새 대화 시작
  /quit          종료
""", DIM))


def print_state(session: Session) -> None:
    filters = session.last.get("filters") or {}
    print(c("  thread   : %s (턴 %d)" % (session.thread_id, session.turns), DIM))
    print(c("  지역     : %s" % (session.region or "(미설정 — 전국+서울시 제도만)"), DIM))
    for label, key in (("생애주기", "life_cycle"), ("가구상황", "household"),
                       ("관심주제", "theme")):
        values = filters.get(key) or []
        print(c("  %s : %s" % (label, ", ".join(values) or "-"), DIM))
    if session.region:
        print(c("  검색 지역키: %s" % constants.region_keys_for_user(session.region), DIM))


def print_list(session: Session) -> None:
    rows = session.last.get("candidates") or []
    if not rows:
        print(c("  (마지막 검색 결과가 없습니다)", DIM))
        return
    for i, r in enumerate(rows, 1):
        where = "전국" if r.get("region_scope") == "national" else (
            r.get("sgg_nm") or r.get("ctpv_nm") or "-")
        print("  %d. %s %s" % (i, c(r["serv_nm"], BOLD), c("[%s]" % where, DIM)))
        print(c("     rrf=%.4f  벡터순위=%s  키워드순위=%s" % (
            r.get("rrf") or 0, r.get("vec_rank") or "-", r.get("lex_rank") or "-"), DIM))
        print(c("     %s" % (r.get("serv_dgst") or "")[:90], DIM))


def print_why(session: Session) -> None:
    rows = session.last.get("candidates") or []
    query = session.last.get("query_text") or ""
    if not rows:
        print(c("  (마지막 검색 결과가 없습니다)", DIM))
        return
    print(c("  검색 질의문: %r" % query, DIM))
    print(c("  후보 검색어: %s" % search.candidate_terms(query), DIM))
    print(c("  %-38s %8s %8s %8s" % ("제도", "rrf", "벡터", "키워드"), DIM))
    for r in rows:
        print(c("  %-38s %8.4f %8s %8s" % (
            r["serv_nm"][:36], r.get("rrf") or 0,
            r.get("vec_rank") or "-", r.get("lex_rank") or "-"), DIM))
    print(c("  ※ 벡터순위 '-' = 벡터 검색이 못 찾은 것을 키워드가 건진 경우", DIM))


async def print_detail(session: Session, index: int) -> None:
    rows = session.last.get("candidates") or []
    if not 1 <= index <= len(rows):
        print(c("  1~%d 사이의 번호를 넣어주세요." % len(rows), YELLOW))
        return
    serv_id = rows[index - 1]["serv_id"]
    pool = await db.connect()
    async with pool.acquire() as con:
        row = await con.fetchrow(
            "SELECT serv_nm, serv_dgst, target_detail, select_criteria, service_content,"
            "       apply_method, contact, jur_org_nm, detail_link,"
            "       life_cycle_tags, household_tags, theme_tags"
            "  FROM cb_institutions WHERE serv_id = $1", serv_id)
    if not row:
        print(c("  제도를 찾을 수 없습니다.", YELLOW))
        return
    print("\n" + c("═" * 64, DIM))
    print(c(row["serv_nm"], BOLD))
    print(c("═" * 64, DIM))
    for label, key in (("요약", "serv_dgst"), ("지원대상", "target_detail"),
                       ("선정기준", "select_criteria"), ("서비스내용", "service_content"),
                       ("신청방법", "apply_method"), ("문의처", "contact"),
                       ("담당기관", "jur_org_nm"), ("링크", "detail_link")):
        value = (row[key] or "").strip()
        if value:
            print(c("[%s]" % label, CYAN), value[:600])
    print(c("[분류]", CYAN),
          "생애=%s 가구=%s 주제=%s" % (row["life_cycle_tags"],
                                    row["household_tags"], row["theme_tags"]))
    print(c("═" * 64, DIM) + "\n")


async def handle_command(session: Session, line: str) -> bool:
    """명령어면 처리하고 True. 아니면 False."""
    if not line.startswith("/"):
        return False
    parts = line.split(maxsplit=1)
    cmd, arg = parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")

    if cmd in ("/quit", "/exit", "/q"):
        raise KeyboardInterrupt
    if cmd == "/help":
        print_help()
    elif cmd == "/state":
        print_state(session)
    elif cmd == "/list":
        print_list(session)
    elif cmd == "/why":
        print_why(session)
    elif cmd == "/detail":
        try:
            await print_detail(session, int(arg))
        except ValueError:
            print(c("  사용법: /detail 2", YELLOW))
    elif cmd == "/region":
        if arg and arg not in constants.SEOUL_GU:
            print(c("  서울 25개 자치구 중에서 골라주세요. 예: /region 은평구", YELLOW))
        else:
            session.region = arg or None
            print(c("  지역을 '%s'(으)로 바꿨습니다." % (arg or "미설정"), GREEN))
    elif cmd == "/new":
        session.new_thread()
        print(c("  새 대화를 시작합니다 (thread=%s)" % session.thread_id, GREEN))
    else:
        print(c("  모르는 명령어입니다. /help 를 보세요.", YELLOW))
    return True


async def main() -> int:
    parser = argparse.ArgumentParser(description="cb 대화 테스트 REPL")
    parser.add_argument("--region", default=None, help="거주 자치구 (예: 은평구)")
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--debug", action="store_true", help="노드 로그 표시")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.debug else logging.WARNING,
        format=c("  %(name)s: %(message)s", DIM),
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.region and args.region not in constants.SEOUL_GU:
        print(c("서울 25개 자치구 중에서 골라주세요: %s" % args.region, YELLOW))
        return 1

    print(BANNER)
    print(c("  그래프를 준비하는 중...", DIM))
    await graph.startup()
    session = Session(args.region, args.user_id)
    print(c("  준비 완료. 지역=%s\n" % (args.region or "미설정"), DIM))

    try:
        while True:
            try:
                line = input(c("나 > ", CYAN)).strip()
            except EOFError:
                break
            if not line:
                continue
            if await handle_command(session, line):
                continue

            started = time.perf_counter()
            try:
                out = await graph.run_turn(
                    session.thread_id, session.user_id, line, session.region)
            except Exception as exc:  # noqa: BLE001 — REPL이 죽으면 안 된다
                print(c("  실행 실패: %r" % exc, YELLOW))
                continue
            elapsed = (time.perf_counter() - started) * 1000

            session.turns += 1
            session.last = out
            # run_turn은 API 응답 모양(카드 없음)만 돌려준다. REPL은 디버깅용이라
            # 검색 원본이 필요하므로 State에서 직접 꺼낸다.
            state = await graph.graph().aget_state(
                {"configurable": {"thread_id": session.thread_id}})
            values = state.values or {}
            session.last["query_text"] = values.get("query_text", "")
            session.last["candidates"] = values.get("candidates") or []

            print()
            print(c("봇 > ", GREEN) + out["answer"])
            print()

            filters = out["filters"]
            summary = " / ".join(
                "%s=%s" % (k, ",".join(v)) for k, v in filters.items() if v) or "필터 없음"
            note = ""
            if out["relaxed_axes"]:
                note = c("  [조건 완화: %s]" % ",".join(out["relaxed_axes"]), YELLOW)
            phase = out["phase"]
            counts = out.get("result_summary") or {}
            state_text = (
                "맞춤 %d건 / 혹시관심 %d건" % (counts.get("matched", 0), counts.get("maybe", 0))
                if phase == "ready" else "대화 중"
            )
            print(c("  %s | %s | %.0fms" % (summary, state_text, elapsed), DIM) + note)
            if phase == "ready":
                results = values.get("results") or {}
                for label, key in (("맞춤", "matched"), ("혹시관심", "maybe")):
                    names = [card["name"][:20] for card in (results.get(key) or [])[:3]]
                    if names:
                        print(c("  %s → %s" % (label, " · ".join(names)), DIM))
                print(c("  (/list 전체 · /why 순위근거 · /detail N 원문 · /new 다시 시작)", DIM))
            print()
    except KeyboardInterrupt:
        pass
    finally:
        print(c("\n  종료합니다.", DIM))
        await graph.shutdown()
        await embedding.close()
        await db.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
