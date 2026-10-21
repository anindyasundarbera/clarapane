"""Phase 1 probe: prove the OpenAI-compatible endpoint answers.

Deliberately tiny. It logs nothing and owns no state, so if this fails we know the
problem is credentials/network/model and not our agent code.

Run:  .venv/bin/python scripts/probe_connectivity.py
"""

from __future__ import annotations

import os
import sys
import time

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(override=True)


def main() -> int:
    base_url = os.environ.get("OPENAI_BASE_URL")
    model = os.environ.get("DEFAULT_OLLAMA_MODEL")
    api_key = os.environ.get("OPENAI_API_KEY")

    # Report shape only -- never echo secrets.
    print(f"base_url = {base_url}")
    print(f"model    = {model}")
    print(f"api_key  = {'set' if api_key else 'MISSING'} (len={len(api_key or '')})")

    if not (base_url and model and api_key):
        print("FAIL: missing OPENAI_BASE_URL / OPENAI_API_KEY / DEFAULT_OLLAMA_MODEL")
        return 2

    client = OpenAI(base_url=base_url, api_key=api_key)
    started = time.perf_counter()
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with exactly: PROBE_OK"}],
            temperature=0,
            max_tokens=20,
        )
    except Exception as exc:  # noqa: BLE001 - probe should surface any failure verbatim
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1

    elapsed = (time.perf_counter() - started) * 1000
    content = (resp.choices[0].message.content or "").strip()
    usage = resp.usage

    print(f"\nreply    = {content!r}")
    print(f"latency  = {elapsed:.0f} ms")
    print(f"usage    = prompt={getattr(usage, 'prompt_tokens', None)} "
          f"completion={getattr(usage, 'completion_tokens', None)}")
    print(f"finish   = {resp.choices[0].finish_reason}")
    print("\nPASS: endpoint reachable and returning completions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
