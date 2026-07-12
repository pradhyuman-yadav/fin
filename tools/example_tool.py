"""Example tool. Deterministic execution unit for the WAT framework.

Copy this as a starting point for new tools. Loads secrets from .env,
does one job, prints/returns structured output.
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()


def run(name: str) -> str:
    """Do the actual work. Replace with real logic."""
    api_key = os.getenv("OPENAI_API_KEY", "")
    status = "set" if api_key else "missing"
    return f"Hello, {name}. OPENAI_API_KEY is {status}."


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "world"
    print(run(arg))
