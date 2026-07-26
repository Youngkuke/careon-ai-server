"""cb 챗봇을 API 계약 그대로 터미널에서 써 보는 도구.

scripts/cb_chat.py와 다른 점: 이쪽은 그래프를 직접 부르지 않고 실제
엔드포인트(/api/v1/cb/...)를 그대로 호출한다. 서버를 따로 띄우지 않고
FastAPI 앱을 프로세스 안에서 돌리므로 uvicorn도 토큰도 필요 없다.
(인증만 --user-id 값으로 갈아끼운다. 그 외에는 프론트가 보게 될 것과 같다.)

프론트 계약을 그대로 따라간다:
  - 대화 중에는 제도가 하나도 안 나온다. phase=gathering
  - phase=ready를 받으면 입력이 잠기고 결과 화면으로 넘어간다
  - 결과 화면은 배너 / 맞춤 제도 / 혹시 관심 있으실 수도 3단

사용법:
    .venv\\Scripts\\python.exe scripts\\cb_repl.py
    .venv\\Scripts\\python.exe scripts\\cb_repl.py --user-id 2
    .venv\\Scripts\\python.exe scripts\\cb_repl.py --debug    # 서버 로그까지 표시

대화 중 명령어:
    /help          명령어 목록
    /results       결과 화면 다시 보기
    /detail N      N번 제도 상세 (원문)
    /easy N        N번 제도를 쉬운 말로 (LLM 호출, 2~4초)
    /new           다시 시작 (DELETE /threads/{id} 후 새 대화)
    /quit          종료
"""
import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# app.cb.graph를 import하는 시점에 Windows 이벤트 루프 정책이 잡힌다. 다른 것보다 먼저.
from app.cb import graph  # noqa: E402,F401
from app.auth import get_current_carer_id  # noqa: E402
from app.main import app  # noqa: E402

BANNER = """
┌──────────────────────────────────────────────────────────────┐
│  CareOn 챗봇 검색엔진 — API 그대로 대화하기                  │
│  대화 중에는 제도가 나오지 않습니다. 다 듣고 나서 보여드려요 │
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
    """지금 화면 상태. 결과를 받은 뒤에는 입력을 받지 않는다(프론트와 같게)."""

    def __init__(self) -> None:
        self.thread_id: Optional[str] = None
        self.phase = "gathering"
        self.cards: List[Dict[str, Any]] = []   # /detail N 이 가리키는 번호표
        # 종료 안내를 한 번만 띄우기 위한 표시.
        self.told_done = False


def print_help() -> None:
    print(c("""
  /results       결과 화면 다시 보기
  /detail N      N번 제도 상세 (원문)
  /easy N        N번 제도를 쉬운 말로 (LLM 호출)
  /new           다시 시작
  /quit          종료
""", DIM))


def print_results(client, session: Session) -> None:
    r = client.get("/api/v1/cb/threads/%s/results" % session.thread_id)
    if r.status_code != 200:
        print(c("  결과를 불러오지 못했습니다: %s %s" % (r.status_code, r.text[:200]), YELLOW))
        return
    data = r.json()

    print()
    print(c("═" * 64, DIM))
    region = data["region"]
    print(c("  기준 지역: %s (%s)" % (region["sgg"] or "전국·서울시", region["source"]), DIM))
    if data["relaxed_axes"]:
        print(c("  ※ 조건을 넓혀서 찾은 결과입니다: %s" % ", ".join(data["relaxed_axes"]), YELLOW))
    print(c("═" * 64, DIM))

    session.cards = []
    for key in ("banner", "matched", "maybe"):
        section = data[key]
        print("\n" + c("[%s] %d건" % (section["title"], section["count"]), BOLD))
        if not section["institutions"]:
            print(c("   (없음)", DIM))
            continue
        for card in section["institutions"]:
            session.cards.append(card)
            number = len(session.cards)
            match = card["match"] or {}
            print("  %2d. %s %s" % (number, c(card["name"], BOLD),
                                    c("[%s]" % card["region"]["label"], DIM)))
            if card["summary"]:
                print(c("      %s" % card["summary"][:88], DIM))
            print(c("      %s · %s%s" % (
                card["agency"] or "담당기관 미상",
                (card["support"]["cycle"] or "지원주기 미상"),
                ("  |  거리 %.3f / %s" % (match["distance"], match["matched_by"])
                 if match.get("distance") is not None else ""),
            ), DIM))
    print()
    print(c("  (/detail N 원문 · /easy N 쉬운 말 · /new 다시 시작)", DIM))
    print()


def print_detail(client, session: Session, index: int) -> None:
    if not 1 <= index <= len(session.cards):
        print(c("  1~%d 사이의 번호를 넣어주세요." % len(session.cards), YELLOW))
        return
    serv_id = session.cards[index - 1]["serv_id"]
    r = client.get("/api/v1/cb/institutions/%s" % serv_id)
    if r.status_code != 200:
        print(c("  불러오지 못했습니다: %s" % r.status_code, YELLOW))
        return
    d = r.json()

    print("\n" + c("═" * 64, DIM))
    print(c(d["name"], BOLD), c("[%s]" % d["region"]["label"], DIM))
    print(c("═" * 64, DIM))
    for label, key in (("요약", "summary"), ("지원대상", "target_detail"),
                       ("선정기준", "select_criteria"), ("서비스내용", "service_content"),
                       ("신청방법", "apply_method")):
        value = d.get(key)
        if value:
            print(c("[%s]" % label, CYAN), value[:600])
    print(c("[담당기관]", CYAN), d["agency"] or "-",
          c("[문의처]", CYAN), d["apply"]["contact"] or "-")
    if d["link"]:
        print(c("[링크]", CYAN), d["link"])
    print(c("═" * 64, DIM) + "\n")


def print_easy(client, session: Session, index: int) -> None:
    if not 1 <= index <= len(session.cards):
        print(c("  1~%d 사이의 번호를 넣어주세요." % len(session.cards), YELLOW))
        return
    card = session.cards[index - 1]
    print(c("  쉬운 말로 푸는 중...", DIM))
    started = time.perf_counter()
    r = client.post("/api/v1/cb/institutions/%s/translate" % card["serv_id"])
    elapsed = (time.perf_counter() - started) * 1000
    if r.status_code != 200:
        print(c("  실패: %s" % r.status_code, YELLOW))
        return
    print("\n" + c(card["name"], BOLD) + c("  (%.0fms)" % elapsed, DIM))
    print(r.json()["easy_text"] + "\n")


def start_over(client, session: Session) -> None:
    if session.thread_id:
        client.delete("/api/v1/cb/threads/%s" % session.thread_id)
    session.thread_id = None
    session.phase = "gathering"
    session.cards = []
    session.told_done = False
    open_thread(client, session)


def open_thread(client, session: Session) -> None:
    """대화를 열고 봇의 첫 인사를 받는다. 사용자가 먼저 말하지 않아도 된다."""
    r = client.post("/api/v1/cb/threads")
    if r.status_code != 201:
        print(c("  대화를 시작하지 못했습니다: %s %s" % (r.status_code, r.text[:200]), YELLOW))
        return
    data = r.json()
    session.thread_id = data["thread_id"]
    session.phase = data["phase"]
    print()
    print(c("봇 > ", GREEN) + data["message"])
    print()


def handle_command(client, session: Session, line: str) -> bool:
    """명령어면 처리하고 True. 아니면 False."""
    if not line.startswith("/"):
        return False
    parts = line.split(maxsplit=1)
    cmd, arg = parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")

    if cmd in ("/quit", "/exit", "/q"):
        raise KeyboardInterrupt
    if cmd == "/help":
        print_help()
    elif cmd == "/new":
        start_over(client, session)
    elif cmd == "/results":
        if session.phase != "ready":
            print(c("  아직 대화 중이에요. 결과는 대화를 마친 뒤에 나옵니다.", YELLOW))
        else:
            print_results(client, session)
    elif cmd in ("/detail", "/easy"):
        if not session.cards:
            print(c("  아직 결과가 없습니다.", YELLOW))
            return True
        try:
            index = int(arg)
        except ValueError:
            print(c("  사용법: %s 2" % cmd, YELLOW))
            return True
        (print_detail if cmd == "/detail" else print_easy)(client, session, index)
    else:
        print(c("  모르는 명령어입니다. /help 를 보세요.", YELLOW))
    return True


def send(client, session: Session, text: str) -> None:
    body: Dict[str, Any] = {"message": text}
    if session.thread_id:
        body["thread_id"] = session.thread_id

    started = time.perf_counter()
    r = client.post("/api/v1/cb/messages", json=body)
    elapsed = (time.perf_counter() - started) * 1000
    if r.status_code != 200:
        print(c("  실패: %s %s" % (r.status_code, r.text[:200]), YELLOW))
        return

    data = r.json()
    session.thread_id = data["thread_id"]
    session.phase = data["phase"]

    print()
    print(c("봇 > ", GREEN) + data["message"])
    filters = data["filters"]
    shown = " / ".join("%s=%s" % (k, ",".join(v)) for k, v in filters.items() if v)
    intake = data.get("intake") or {}
    known = "나이=%s 대상=%s" % (intake.get("age") or "?",
                               {"self": "본인", "caree": "돌봄대상"}.get(
                                   intake.get("target_for"), "?"))
    print(c("  %s | %s | %s | %.0fms" % (
        known, shown or "필터 없음", session.phase, elapsed), DIM))
    print()

    if session.phase == "ready":
        # 프론트는 여기서 입력창을 잠그고 결과 화면으로 넘어간다.
        print_results(client, session)


def main() -> int:
    parser = argparse.ArgumentParser(description="cb 챗봇 API 대화 도구")
    parser.add_argument("--user-id", type=int, default=1,
                        help="이 carer_id로 로그인한 것처럼 동작한다 (지역이 여기서 나온다)")
    parser.add_argument("--debug", action="store_true", help="서버 로그 표시")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.debug else logging.WARNING,
        format=c("  %(name)s: %(message)s", DIM),
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # 토큰 검증만 건너뛴다. 라우터·그래프·DB는 전부 실제 코드가 돈다.
    app.dependency_overrides[get_current_carer_id] = lambda: args.user_id

    from fastapi.testclient import TestClient

    print(BANNER)
    print(c("  서버를 준비하는 중...", DIM))
    with TestClient(app) as client:
        session = Session()
        print(c("  준비 완료. carer_id=%d 로 대화합니다." % args.user_id, DIM))
        open_thread(client, session)
        try:
            while True:
                if session.phase == "ready" and not session.told_done:
                    # 결과가 나온 뒤에는 대화를 이어가지 않는다(프론트와 같게).
                    print(c("  대화가 끝났습니다. /new 로 다시 시작하거나 /quit 로 나가세요.", DIM))
                    session.told_done = True
                try:
                    line = input(c("나 > ", CYAN)).strip()
                except EOFError:
                    break
                if not line:
                    continue
                if handle_command(client, session, line):
                    continue
                if session.phase == "ready":
                    print(c("  결과가 이미 나왔어요. /new 로 다시 시작해주세요.", YELLOW))
                    continue
                send(client, session, line)
        except KeyboardInterrupt:
            pass
        finally:
            print(c("\n  종료합니다.", DIM))
    return 0


if __name__ == "__main__":
    sys.exit(main())
