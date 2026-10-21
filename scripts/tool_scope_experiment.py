"""Repeat the tool-scoping experiment to check the stuck-loop finding is real.

Question: when an agent is asked to FIX something but is given no write
capability, what does it do on the turn after it has read the relevant code?

Condition A -- read+finish only:  expect it to re-read / loop
Condition B -- read+edit+finish:  expect it to propose_edit

Reports an outcome histogram per condition plus the tool-call sequence, so the
claim rests on a distribution rather than a single sample.
"""

from __future__ import annotations

import collections
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)
TRIALS = 5


def tool(name: str, props: dict, req: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"{name}.",
            "parameters": {"type": "object", "properties": props, "required": req},
        },
    }


READ = [
    tool("read_range", {"path": {"type": "string"}, "start": {"type": "integer"},
                        "end": {"type": "integer"}}, ["path"]),
    tool("finish", {"summary": {"type": "string"}, "evidence": {"type": "string"}},
         ["summary", "evidence"]),
]
EDIT = [tool("propose_edit", {"path": {"type": "string"}, "old_str": {"type": "string"},
                             "new_str": {"type": "string"}}, ["path", "old_str", "new_str"])]

TASK = "Fix the bug in sandbox/calculator/ops.py by removing the negative-number clamping."
OBS = json.dumps({"path": "calculator/ops.py", "lines": [
    {"line": 10, "text": "def add(a: int, b: int) -> int:"},
    {"line": 15, "text": "    if a < 0:"},
    {"line": 16, "text": "        a = 0"},
    {"line": 17, "text": "    if b < 0:"},
    {"line": 18, "text": "        b = 0"},
    {"line": 19, "text": "    return a + b"},
]})


def classify(name: str | None, prose: str | None) -> str:
    if name == "propose_edit":
        return "proposed edit (progress)"
    if name == "read_range":
        return "re-read (loop)"
    if name == "finish":
        return "finish"
    if name is None:
        return "prose stop"
    return f"other:{name}"


def run_once(client, model: str, tools: list[dict]) -> tuple[str, list[str]]:
    msgs = [
        {"role": "system", "content": "You are a coding agent. Call finish when the task is complete."},
        {"role": "user", "content": TASK},
    ]
    # Force the same starting point in both conditions: one read.
    first = client.chat.completions.create(
        model=model, messages=msgs, tools=tools, temperature=0, max_tokens=1200
    ).choices[0].message
    names = [c.function.name for c in (first.tool_calls or [])]

    msgs.append({"role": "assistant", "content": first.content or "", "tool_calls": [
        {"id": c.id, "type": "function",
         "function": {"name": c.function.name, "arguments": c.function.arguments}}
        for c in (first.tool_calls or [])]})
    for c in (first.tool_calls or []):
        msgs.append({"role": "tool", "tool_call_id": c.id, "content": OBS})

    second = client.chat.completions.create(
        model=model, messages=msgs, tools=tools, temperature=0, max_tokens=1200
    ).choices[0].message
    names += [c.function.name for c in (second.tool_calls or [])]
    pick = second.tool_calls[0].function.name if second.tool_calls else None
    return classify(pick, second.content), names


def main() -> int:
    client = OpenAI(base_url=os.environ["OPENAI_BASE_URL"], api_key=os.environ["OPENAI_API_KEY"])
    model = os.environ["DEFAULT_OLLAMA_MODEL"]
    results: dict[str, collections.Counter] = {}
    traces: dict[str, list[list[str]]] = {}

    for label, tools in [("read+finish", READ), ("read+edit+finish", READ + EDIT)]:
        counter: collections.Counter = collections.Counter()
        traces[label] = []
        for trial in range(TRIALS):
            outcome, names = run_once(client, model, tools)
            counter[outcome] += 1
            traces[label].append(names)
            print(f"  {label} #{trial + 1}: {outcome:<24} {names}", flush=True)
        results[label] = counter

    print("\n=== outcome distribution ===")
    for label, counter in results.items():
        print(f"\n{label} (n={TRIALS})")
        for outcome, count in counter.most_common():
            print(f"  {count}/{TRIALS}  {outcome}")

    out = Path(__file__).resolve().parent.parent / "runs" / "tool_scope_experiment.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(
        {"model": model, "trials": TRIALS,
         "results": {k: dict(v) for k, v in results.items()}, "traces": traces},
        indent=2,
    ))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
