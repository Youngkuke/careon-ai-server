"""매칭만 단독 실행 (대화 10턴을 다시 돌리지 않고 매칭 품질만 본다).

사용법: .venv\\Scripts\\python.exe scripts\\simulate_match.py
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.profile import Profile  # noqa: E402
from app.services import matching  # noqa: E402

BAR = "=" * 72

# simulate_chat.py 대본을 끝까지 돌렸을 때 나온 프로필
PERSONA = {
    "age": 22,
    "region_sigungu": "관악구",
    "selected_types": ["돌봄가사", "생계주거"],
    "name": "지수",
    "fields": {
        "household_members": 2,
        "housing_type": "전세",
        "living_with_relation": "모(동거)",
        "dependents_count": 1,
        "has_income_activity": True,
        "military_service_status": "해당없음",
        "priority_burden_type": "시간부담",
        "priority_types": ["돌봄가사", "생계주거"],
        "priority_reason_summary": "알바와 병원 동행으로 하루 시간이 부족해 시간 부담이 가장 큼",
        "daily_care_hours_self": 6,
        "has_backup_caregiver": False,
        "employment_status": "휴학",
        "housing_deposit": 80000000,
        "housing_monthly_rent": 0,
    },
    "care_recipients": [
        {
            "relation_to_user": "모",
            "condition_summary": "뇌병변장애 1급, 거동 불편, 3년째 악화 중",
            "severity_level": "높음",
            "status": "active",
        }
    ],
    "income_signals": [
        {"signal_type": "월생활비", "raw_value": "한 80만원 정도", "parsed_value": 800000,
         "source": "indirect_question", "confidence": "medium"},
        {"signal_type": "아르바이트수입", "raw_value": "알바로 한 70만원 정도 벌어요",
         "parsed_value": 700000, "source": "self_declared", "confidence": "high"},
    ],
}


def build_profile() -> Profile:
    p = Profile(
        carer_id=123,
        age=PERSONA["age"],
        region_sigungu=PERSONA["region_sigungu"],
        selected_types=PERSONA["selected_types"],
        case_number=1,
        name=PERSONA["name"],
    )
    p.care_recipients = list(PERSONA["care_recipients"])
    p.income_signals = list(PERSONA["income_signals"])
    p.merge(dict(PERSONA["fields"]))
    return p


def show(result: dict) -> None:
    print(BAR)
    print("적합 {} / 확인_불가 {} / 부적합 {}   has_followup_available={}".format(
        len(result["적합"]), len(result["확인_불가"]), len(result["부적합"]),
        result["has_followup_available"]))
    print(BAR)

    print("\n■ 적합 ({}건)".format(len(result["적합"])))
    for it in result["적합"]:
        print("\n  #{} {}".format(it["policy_id"], it["policy_name"]))
        print("     이유: {}".format(it["reason"]))
        if it.get("caution"):
            print("     [주의] {}".format(it["caution"]))

    print("\n\n■ 확인_불가 ({}건)".format(len(result["확인_불가"])))
    for it in result["확인_불가"]:
        print("\n  #{} {}   << {}".format(it["policy_id"], it["policy_name"], it["missing_field"]))
        print("     {}".format(it["reason"]))

    print("\n\n■ 부적합 ({}건)".format(len(result["부적합"])))
    for it in result["부적합"]:
        print("  #{} {}".format(it["policy_id"], it["policy_name"]))
        print("      - {}".format(it["reason"]))


async def main() -> None:
    await db.connect()
    profile = build_profile()
    print("프로필:")
    print(json.dumps(profile.to_dict(), ensure_ascii=False, indent=2))
    print("\n매칭 실행 중...")
    started = time.time()
    result = await matching.run_matching(profile)
    print("({:.1f}초)\n".format(time.time() - started))
    show(result)
    await db.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
