"""대화 흐름 검증용 시뮬레이터 (서버 안 띄우고 서비스 레이어를 직접 호출).

사용법:
    .venv\\Scripts\\python.exe scripts\\simulate_chat.py            # 대본 자동 진행
    .venv\\Scripts\\python.exe scripts\\simulate_chat.py --interactive   # 직접 입력
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.profile import Profile  # noqa: E402
from app.services import chat  # noqa: E402
from app.session_store import PHASE_MATCHING, store  # noqa: E402

# 페르소나: 22세, 관악구, 어머니(뇌병변장애) 돌봄, 휴학 중, 알바
SCRIPT = [
    "지금 엄마랑 둘이 살고 전세예요",
    "네 엄마랑 같이 살아요. 카페 알바 주 3일 하고 있어요",
    "군대는 안 갔어요, 여자예요",
    "시간이 제일 부족한 거 같아요. 알바랑 병원 왔다갔다 하면 하루가 다 가요",
    "엄마가 뇌병변장애 1급이라 거동을 잘 못 하세요. 3년째인데 점점 안 좋아지세요",
    "거의 저 혼자 해요. 하루에 한 6시간쯤 되는 거 같아요",
    "학교는 작년부터 휴학 중이에요",
    "생활비는 한 달에 한 80만원 정도 쓰는 거 같아요",
    "알바로 한 70만원 정도 벌어요. 보증금은 8천이고 월세는 없어요",
    "네 맞아요 그렇게 하면 될 거 같아요",
]

BAR = "=" * 72


def show(session, label, reply, advanced=None, extracted=None):
    print("\n" + BAR)
    print("[{}] current_phase = {}".format(label, session.current_phase), end="")
    if advanced is not None:
        print("   phase_advanced = {}".format(advanced))
    else:
        print()
    if extracted:
        print("extracted_fields = " + json.dumps(extracted, ensure_ascii=False))
    print("-" * 72)
    print("봇: " + reply)


def show_match(result: dict) -> None:
    print("\n" + BAR)
    print("매칭 결과  적합 {} / 확인_불가 {} / 부적합 {}   has_followup_available={}".format(
        len(result["적합"]), len(result["확인_불가"]), len(result["부적합"]),
        result["has_followup_available"],
    ))
    print(BAR)
    print("\n■ 적합")
    for item in result["적합"]:
        print("  #{} {}".format(item["policy_id"], item["policy_name"]))
        print("     이유: {}".format(item["reason"]))
        if item.get("caution"):
            print("     ⚠ {}".format(item["caution"]))
    print("\n■ 확인_불가")
    for item in result["확인_불가"]:
        print("  #{} {}  (부족: {})".format(
            item["policy_id"], item["policy_name"], item["missing_field"]))
        print("     {}".format(item["reason"]))
    print("\n■ 부적합 ({}건)".format(len(result["부적합"])))
    for item in result["부적합"]:
        print("  #{} {} — {}".format(item["policy_id"], item["policy_name"], item["reason"]))


async def main(interactive: bool) -> None:
    await db.connect()
    profile = Profile(
        carer_id=123,
        age=22,
        region_sigungu="관악구",
        selected_types=["돌봄가사", "생계주거"],
        case_number=1,
        name="지수",
    )
    session = store.create(profile)

    reply = await chat.start_session(session)
    show(session, "세션 시작", reply)

    turn = 0
    while True:
        if interactive:
            try:
                message = input("\n나: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not message or message in ("quit", "exit"):
                break
        else:
            if turn >= len(SCRIPT):
                break
            message = SCRIPT[turn]
            print("\n나: " + message)
        turn += 1

        if not isinstance(session.current_phase, int):
            print("\n>>> 정보 수집 종료 (current_phase={})".format(session.current_phase))
            break

        reply, advanced, extracted = await chat.process_turn(session, message)
        show(session, "턴 {}".format(turn), reply, advanced, extracted)

    print("\n" + BAR)
    print("최종 프로필:")
    print(json.dumps(session.profile.to_dict(), ensure_ascii=False, indent=2))
    print("최종 current_phase =", session.current_phase)

    if session.current_phase == PHASE_MATCHING:
        print("\n매칭 실행 중...")
        started = time.time()
        result = await chat.get_match_result(session)
        print("({:.1f}초)".format(time.time() - started))
        show_match(result)

    await db.disconnect()


if __name__ == "__main__":
    asyncio.run(main("--interactive" in sys.argv))
