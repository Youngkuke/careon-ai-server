"""제도 카탈로그 크기(토큰) 확인."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, llm  # noqa: E402
from app.config import settings  # noqa: E402
from app.services import policies  # noqa: E402


async def main() -> None:
    await db.connect()
    rows = await policies.get_catalog()
    text = policies.catalog_to_text(rows)
    print("제도 {}건, {:,} chars".format(len(rows), len(text)))
    print("=" * 72)
    print(policies.policy_to_block(rows[1]))
    print("=" * 72)

    resp = await llm.client().messages.count_tokens(
        model=settings.claude_model,
        messages=[{"role": "user", "content": text}],
    )
    print("input_tokens = {:,}".format(resp.input_tokens))
    await db.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
