"""대본을 자동으로 돌려서 대화 흐름과 결과를 한 번에 확인하는 도구.

scripts/cb_chat.py는 사람이 한 줄씩 치는 REPL이다. 이쪽은 정해진 발화 목록을
끝까지 재생하고, 매 턴 어떤 노드를 탔는지와 State가 어떻게 쌓였는지를 함께
찍는다. 같은 대본을 코드 수정 전후로 돌리면 무엇이 달라졌는지 눈으로 비교된다.

무엇을 보려고 만들었나:
  - 인테이크 순서(대상 → 본인 나이 → 돌보는 분 연세)가 지켜지는가
  - 소득 파악이 1 → 2 → 3단계로 넘어가는가, 회피하면 접는가
  - 의료·돌봄 대화에서 "어디가 어떻게 불편하신지"를 묻는가 (condition_asked)
  - 대화에 나온 질환이 결과 상위로 올라오는가 (질환일치 표시)
  - target_for와 반대쪽 축의 제도가 목록에서 사라지지 않는가 (생애주기 표시)
  - 본인 몫 주제로만 걸린 돌봄 대상 전용 제도가 내려가는가 (축불일치 표시)

LLM과 DB를 실제로 부른다. 한 시나리오에 6~8턴이면 30초 안팎 걸린다.
대화 스레드는 끝나고 지운다 (--keep으로 남길 수 있다).

사용법:
    .venv\\Scripts\\python.exe scripts\\cb_replay.py --list
    .venv\\Scripts\\python.exe scripts\\cb_replay.py                # 전부 재생
    .venv\\Scripts\\python.exe scripts\\cb_replay.py 치매돌봄 본인일자리
    .venv\\Scripts\\python.exe scripts\\cb_replay.py --file my.json
    .venv\\Scripts\\python.exe scripts\\cb_replay.py --region 강남구 --keep

대본 파일(JSON)은 두 가지 모양을 다 받는다:
    {"이름": ["첫 발화", "둘째 발화"]}
    {"이름": {"region": "은평구", "turns": ["첫 발화", "둘째 발화"]}}
"""
import argparse
import asyncio
import json
import logging
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# graph를 import하는 시점에 Windows 이벤트 루프 정책이 잡힌다. 다른 것보다 먼저.
from app.cb import graph  # noqa: E402
from app.cb import constants, db, embedding  # noqa: E402

DIM = "\033[2m"
BOLD = "\033[1m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
RESET = "\033[0m"


def c(text: str, color: str) -> str:
    return f"{color}{text}{RESET}"


# 기본 대본. 각 항목이 확인하려는 것이 무엇인지 이름에 담았다.
# 새 시나리오는 여기 넣거나 --file로 따로 준다.
SCENARIOS: Dict[str, List[str]] = {
    # 인테이크 순서. 주제만 말했을 때 대상을 가장 먼저 묻는가.
    "인테이크순서": [
        "요즘 병원비가 너무 많이 나가서 힘들어요",
        "아버지 병원비예요",
        "저는 25살이요",
        "아버지는 여든둘이세요",
        "장기요양등급은 아직 안 받으셨어요",
        # 증상 확인 노드가 들어오면서 결과까지 한 턴이 더 걸린다.
        "받는 지원은 없어요",
        "그냥 찾아주세요",
    ],
    # 나이만 답한 턴에 대상을 넘겨짚지 않는가 (2026-07-27 회귀).
    # 확인 지점은 1턴째다 — age는 잡히고 target_for는 비어 있어야 한다.
    "나이만답함": [
        "25살이요",
        "월세가 좀 부담돼요",
        "제 월세요",
        "돌보는 사람은 없어요",
        "받는 지원은 따로 없어요",
        "네 찾아주세요",
    ],
    # 질환 가산 + 돌봄 주제 확장 + 소득 1·2·3단계가 모두 도는 대본.
    "치매돌봄": [
        "어머니가 치매신데 병원비가 너무 많이 나가요",
        "어머니 때문에 찾고 있어요",
        "저는 25살이요",
        "어머니는 일흔여덟이세요",
        "받고 계신 지원이 있는지 잘 모르겠어요",
        "소득은 얼마 안 돼요",
        "저 혼자 벌고 어머니랑 둘이 살아요",
        "네 그게 다예요",
    ],
    # target_for=self인데 돌보는 분이 있는 경우. 노년 제도가 사라지지 않아야 한다.
    "본인일자리": [
        "일자리를 알아보고 있는데 아버지 간병 때문에 시간이 안 나요",
        "제가 받을 수 있는 걸 찾고 있어요",
        "26살이요",
        "아버지는 여든이세요",
        "받는 지원은 없는 것 같아요",
        "네 찾아주세요",
    ],
    # 축 분리 회귀 (2026-07-31). 26세 사용자가 81세 할아버지 상담 중에
    # "저도 일자리가 빠듯하다"고 **본인** 이야기를 한 대본이다.
    #
    # 확인할 것:
    #   - 3~4턴 사이에 "어디가 어떻게 불편하신지"를 묻는가 (증상 확인 노드).
    #     "신체적 장애세요, 정신적 장애세요?" 같은 이분법이면 실패다.
    #   - 지체·거동 답변 뒤에 「장애인활동지원 구비추가」류가 질환일치로 올라오는가.
    #   - 마지막 턴 뒤 결과에 「장애인일자리지원」이 맞춤 구간에 없어야 한다.
    #     ('일자리'는 본인 몫 주제이고 장애는 할아버지 쪽이라 축이 어긋난다.
    #      감점 표시 axis_mismatch가 붙는다.)
    #   - 반대로 청년 일자리 제도는 그대로 남아 있어야 한다. 사라지면 과잉 감점이다.
    "축분리_할아버지": [
        "할아버지 관련해서 제도를 받고 싶어요",
        "저는 26살이고 할아버지는 81세세요",
        "돌봄이랑 병원비인 것 같아",
        "작년에 낙상하시고 나서 다리를 잘 못 쓰세요",
        "받고 계신 지원은 잘 모르겠어요",
        "저도 일자리가 빠듯해서 걱정이에요",
        "네 그게 다예요",
        # 마지막 발화가 일자리를 다시 얹는다. query_text가 일자리 쪽으로 서야
        # 장애인 일자리 제도가 후보에 들어오고, 그래야 축 감점이 실제로 시험된다.
        # (앞 턴에서만 말하고 끝나면 검색어가 돌봄 쪽으로 서서 후보에 아예 안 든다.)
        "장애 등록은 되어 있어요. 저도 일자리 구하는 게 빠듯해서 그것도 같이 봐주세요",
    ],
    # 소득 질문을 피하면 재차 캐묻지 않는가.
    "소득회피": [
        "몸이 안 좋아서 병원을 자주 다녀요",
        "제 얘기예요",
        "27살이요",
        "받는 지원은 잘 모르겠어요",
        "그건 좀 말씀드리기 어렵네요",
        "그냥 찾아주세요",
        "장애 등록은 안 되어 있어요",
    ],
}

# 매 턴 찍을 State 키. 값이 비어 있으면 줄에서 뺀다 (0/False/[]/None).
WATCH = (
    "target_for", "age", "caree_age",
    "income_category", "monthly_income", "household_size",
    "disability_severity_hint", "conditions", "denied_conditions",
    "theme", "self_themes", "caree_themes", "life_cycle", "household",
    "intake_asked", "income_probes", "narrow_asked", "condition_asked",
)


class NodeTap(logging.Handler):
    """어떤 노드가 돌았는지 로그 접두어([intent] 등)로 잡아낸다.

    그래프가 어느 경로를 탔는지는 응답만 봐서는 알 수 없다. ask_intake로
    물은 것인지 converse로 물은 것인지가 이 도구의 핵심 관심사라서
    로그를 가로챈다.
    """

    #  이 접두어로 시작하는 줄은 내용까지 그대로 보여준다. 단계 전환처럼
    #  한 줄 요약이 곧 확인하려던 사실인 경우다.
    VERBOSE = ("[converse]", "[disease]", "[grading]", "[relax]", "[axis]")

    def __init__(self) -> None:
        super().__init__()
        self.path: List[str] = []
        self.notes: List[str] = []

    def reset(self) -> None:
        self.path.clear()
        self.notes.clear()

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if not message.startswith("["):
            return
        self.path.append(message.split("]")[0][1:])
        if message.startswith(self.VERBOSE):
            self.notes.append(message)


def load_scenarios(path: Path) -> Dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("최상위가 {이름: 발화목록} 형태여야 합니다")
    return raw


def unpack(entry: Any, default_region: Optional[str]) -> Tuple[List[str], Optional[str]]:
    """대본 항목 → (발화 목록, 지역). 두 가지 표기를 모두 받는다."""
    if isinstance(entry, dict):
        return list(entry.get("turns") or []), entry.get("region") or default_region
    return list(entry or []), default_region


def state_line(state: Dict[str, Any]) -> str:
    parts = []
    for key in WATCH:
        value = state.get(key)
        if value in (None, [], 0, False, ""):
            continue
        parts.append("%s=%s" % (key, ",".join(value) if isinstance(value, list) else value))
    return "  ".join(parts)


def show_results(state: Dict[str, Any]) -> None:
    """결과 화면 두 섹션을 카드 순서대로. 왜 그 자리에 있는지 표시를 함께 단다.

    카드에는 없는 표시(질환일치·대상불일치)를 붙이려고 검색 원본을 함께 읽는다.
    응답에 그런 필드를 더하지 않기로 했기 때문에(연동 완료) 여기서만 꺼내 쓴다.
    """
    results = state.get("results") or {}
    rows = {row["serv_id"]: row for row in (state.get("candidates") or [])}

    for label, key in (("맞춤 제도", "matched"), ("혹시 관심 있으실 수도", "maybe")):
        cards = results.get(key) or []
        print(c("\n  [%s] %d건" % (label, len(cards)), BOLD))
        for index, card in enumerate(cards, 1):
            row = rows.get(card["serv_id"]) or {}
            marks = []
            if row.get("disease_match"):
                marks.append("질환일치:%s" % ",".join(row["disease_match"]))
            if row.get("target_mismatch"):
                marks.append("대상불일치")
            if row.get("axis_mismatch"):
                marks.append("축불일치:%s" % ",".join(row["axis_mismatch"]))
            if row.get("grading_notes"):
                marks.append("/".join(row["grading_notes"]))
            if row.get("eligibility_notes"):
                marks.append("감점:%s" % ",".join(row["eligibility_notes"]))
            life_cycle = ",".join(card["tags"]["life_cycle"]) or "-"
            print("    %2d. %-36s %s %s" % (
                index, card["name"][:36],
                c("[%s]" % life_cycle, DIM),
                c(" ".join(marks), YELLOW)))


async def replay(name: str, entry: Any, tap: NodeTap,
                 default_region: Optional[str], keep: bool) -> None:
    turns, region = unpack(entry, default_region)
    if not turns:
        print(c("  '%s'에 발화가 없습니다. 건너뜁니다." % name, YELLOW))
        return

    thread_id = "replay-%s" % uuid.uuid4().hex[:10]
    print("\n" + c("=" * 74, DIM))
    print(c("시나리오 %s" % name, BOLD) + c("   지역=%s  thread=%s"
                                         % (region or "미설정", thread_id), DIM))
    print(c("=" * 74, DIM))

    started_all = time.perf_counter()
    opening = await graph.start_thread(thread_id, 1, region)
    print(c("봇 > ", GREEN) + opening["answer"])

    spoken = 0
    for line in turns:
        tap.reset()
        spoken += 1
        print("\n" + c("나 > ", CYAN) + line)

        started = time.perf_counter()
        try:
            out = await graph.run_turn(thread_id, 1, line, region)
        except Exception as exc:  # noqa: BLE001 — 한 시나리오가 죽어도 다음은 돌린다
            print(c("  실행 실패: %r" % exc, YELLOW))
            return
        elapsed = (time.perf_counter() - started) * 1000

        snapshot = await graph.graph().aget_state(
            {"configurable": {"thread_id": thread_id}})
        state = snapshot.values or {}

        print(c("봇 > ", GREEN) + out["answer"])
        for note in tap.notes:
            print(c("      · %s" % note, YELLOW))
        print(c("    노드: %s  (%.0fms)" % (" → ".join(tap.path) or "-", elapsed), DIM))
        line_text = state_line(state)
        if line_text:
            print(c("    %s" % line_text, DIM))

        if out["phase"] == "ready":
            print(c("\n  === 결과 (사용자 발화 %d턴, 총 %.1fs) ==="
                    % (spoken, time.perf_counter() - started_all), BOLD))
            show_results(state)
            break
    else:
        print(c("\n  대본이 끝났지만 아직 대화 중입니다 (phase=gathering, %d턴)."
                % spoken, YELLOW))

    if keep:
        print(c("\n  스레드를 남겨둡니다: %s" % thread_id, DIM))
        return
    try:
        await graph.checkpointer().adelete_thread(thread_id)
    except Exception as exc:  # noqa: BLE001 — 정리 실패가 재생을 막을 이유는 없다
        print(c("\n  스레드 정리 실패 — 직접 지워주세요: %s (%r)" % (thread_id, exc), YELLOW))


async def main() -> int:
    parser = argparse.ArgumentParser(description="cb 대화 대본 재생")
    parser.add_argument("names", nargs="*", help="재생할 시나리오 이름 (없으면 전부)")
    parser.add_argument("--file", type=Path, help="대본 JSON 파일")
    parser.add_argument("--region", default="은평구", help="거주 자치구 (기본 은평구)")
    parser.add_argument("--list", action="store_true", help="시나리오 이름만 보여준다")
    parser.add_argument("--keep", action="store_true",
                        help="끝나고 대화 스레드를 지우지 않는다")
    parser.add_argument("--debug", action="store_true", help="노드 로그를 그대로 표시")
    args = parser.parse_args()

    try:
        book: Dict[str, Any] = load_scenarios(args.file) if args.file else dict(SCENARIOS)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(c("대본을 읽지 못했습니다: %r" % exc, YELLOW))
        return 1

    if args.list:
        for name, entry in book.items():
            turns, region = unpack(entry, args.region)
            print("  %-12s %d턴  지역=%s" % (name, len(turns), region or "미설정"))
        return 0

    unknown = [n for n in args.names if n not in book]
    if unknown:
        print(c("모르는 시나리오: %s" % ", ".join(unknown), YELLOW))
        print(c("고를 수 있는 것: %s" % ", ".join(book), DIM))
        return 1
    if args.region and args.region not in constants.SEOUL_GU:
        print(c("서울 25개 자치구 중에서 골라주세요: %s" % args.region, YELLOW))
        return 1

    # 노드 로그는 기본적으로 tap만 가져간다. --debug일 때만 화면에도 흘린다.
    logging.basicConfig(level=logging.WARNING,
                        format=c("  %(name)s: %(message)s", DIM))
    logging.getLogger("httpx").setLevel(logging.WARNING)
    tap = NodeTap()
    cb_logger = logging.getLogger("app.cb")
    cb_logger.addHandler(tap)
    cb_logger.setLevel(logging.INFO)
    cb_logger.propagate = bool(args.debug)

    print(c("  그래프를 준비하는 중...", DIM))
    await graph.startup()
    try:
        for name in (args.names or list(book)):
            await replay(name, book[name], tap, args.region, args.keep)
    finally:
        await graph.shutdown()
        await embedding.close()
        await db.disconnect()
    print()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
