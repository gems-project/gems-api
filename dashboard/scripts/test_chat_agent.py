from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = ROOT / "dashboard"
sys.path.insert(0, str(DASHBOARD))

load_dotenv(DASHBOARD / ".env", override=True)

from gems_chat import run_agent  # noqa: E402
from gems_data import GemsData  # noqa: E402


QUESTIONS = [
    "How many rows are in bodyweight?",
    "Average body weight per contributor, sorted descending.",
    "Which animals have the most body weight measurements?",
]


def main() -> int:
    data = GemsData()
    failed = False
    for question in QUESTIONS:
        print(f"\nQUESTION: {question}")
        result = run_agent(question, [], data)
        answer = result.get("answer", "")
        tool_calls = result.get("tool_calls", [])
        print(f"TOOL_CALL_COUNT: {len(tool_calls)}")
        print(f"ANSWER:\n{answer}\n")
        if (
            not answer
            or answer.lower().startswith("error:")
            or "maximum number of tool calls" in answer.lower()
            or "failed before it could query data" in answer.lower()
        ):
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())