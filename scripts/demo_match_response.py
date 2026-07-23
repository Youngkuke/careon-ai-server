"""POST /api/v1/chat/sessions/{session_id}/match 가 실제로 반환하는 응답 형태를 그대로 찍는다.

- run_matching(실제 LLM 호출) → to_match_items(엔드포인트가 쓰는 변환) → MatchResponse
- 확인 포인트: (1) 라우트가 POST인지 (2) match_group이 한글인지 (3) 적합이 먼저 오는지

사용법: .venv\\Scripts\\python.exe scripts\\demo_match_response.py
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.config import settings  # noqa: E402
from app.schemas import MatchResponse  # noqa: E402
from app.services import matching  # noqa: E402
from scripts.simulate_match import build_profile  # noqa: E402

BAR = "=" * 72


def show_route_method() -> None:
    """실제 앱에 등록된 /match 라우트의 HTTP 메서드를 확인한다."""
    from app.main import app

    for route in app.routes:
        if getattr(route, "path", "").endswith("/sessions/{session_id}/match"):
            print("라우트: {}  메서드: {}".format(route.path, sorted(route.methods)))


async def main() -> None:
    print(BAR)
    print("1) 엔드포인트 메서드 확인")
    print(BAR)
    show_route_method()
    print("MATCH_PERSIST_ENABLED =", settings.match_persist_enabled,
          "(false면 저장 안 하고 matched_policy_id=null)")

    await db.connect()
    profile = build_profile()
    print("\n" + BAR)
    print("2) 실제 매칭 실행 (LLM 호출)")
    print(BAR)
    result = await matching.run_matching(profile)

    # 엔드포인트(run_match)가 쓰는 바로 그 변환
    items = matching.to_match_items(result)
    if settings.match_persist_enabled:
        saved = await matching.save_match_result(profile.carer_id, items)
        for item in items:
            item["matched_policy_id"] = saved.get(item["policy_id"])

    response = MatchResponse(matches=items)

    print("\n" + BAR)
    print("3) POST /match 응답 JSON (matches 배열 순서 그대로)")
    print(BAR)
    print(json.dumps(response.model_dump(), ensure_ascii=False, indent=2))

    print("\n" + BAR)
    print("4) 검증 요약")
    print(BAR)
    groups = [m["match_group"] for m in response.model_dump()["matches"]]
    print("match_group 값 종류:", sorted(set(groups)))
    print("등장 순서       :", groups)
    fit_idx = [i for i, g in enumerate(groups) if g == "적합"]
    unk_idx = [i for i, g in enumerate(groups) if g == "확인_불가"]
    ok = (not fit_idx or not unk_idx) or max(fit_idx) < min(unk_idx)
    print("적합이 확인_불가보다 먼저? ->", ok)

    await db.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
