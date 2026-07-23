"""매칭 LLM 원본 응답을 그대로 덤프한다 (스키마 검증 디버깅용)."""
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, llm  # noqa: E402
from app.config import settings  # noqa: E402
from app.services import matching, policies  # noqa: E402
from scripts.simulate_match import build_profile  # noqa: E402


async def main() -> None:
    await db.connect()
    catalog = await policies.get_catalog()
    profile = build_profile()

    resp = await llm.client().messages.create(
        model=settings.claude_model,
        max_tokens=settings.matching_max_tokens,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        system=matching.build_system(policies.catalog_to_text(catalog)),
        messages=[{
            "role": "user",
            "content": matching.build_user_message(profile, [], date.today().isoformat()),
        }],
        tools=[matching.MATCHING_TOOL],
        tool_choice={"type": "tool", "name": matching.MATCHING_TOOL["name"]},
    )

    print("stop_reason =", resp.stop_reason)
    print("usage =", resp.usage)
    print("block types =", [b.type for b in resp.content])
    for b in resp.content:
        if b.type == "tool_use":
            out = Path("raw_match.json")
            out.write_text(json.dumps(b.input, ensure_ascii=False, indent=2), encoding="utf-8")
            print("-> raw_match.json 저장")
            for key, value in (b.input or {}).items():
                kinds = {type(v).__name__ for v in value} if isinstance(value, list) else type(value).__name__
                count = len(value) if isinstance(value, list) else "-"
                print("  {}: n={} itemtypes={}".format(key, count, kinds))
                if isinstance(value, list) and value:
                    print("     first =", json.dumps(value[0], ensure_ascii=False)[:300])
    await db.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
