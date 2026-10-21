"""List available cloud models and their capabilities.

The 'research the API' task, already resolved:
  * GET https://ollama.com/api/tags   -> full model list + capabilities
  * GET https://ollama.com/v1/models  -> OpenAI-native shape, no capabilities
  * https://api.ollama.com/api/tags   -> 403 (wrong host)

Usage:
    .venv/bin/python scripts/list_models.py            # tool-capable only
    .venv/bin/python scripts/list_models.py --all
    .venv/bin/python scripts/list_models.py --set glm-5.3   # pin a model in .env
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from clarapane.provider import Provider

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def pin_model(model: str) -> None:
    """Rewrite DEFAULT_OLLAMA_MODEL in .env, preserving every other line."""
    text = ENV_PATH.read_text()
    pattern = re.compile(r"^DEFAULT_OLLAMA_MODEL=.*$", re.MULTILINE)
    if pattern.search(text):
        text = pattern.sub(f"DEFAULT_OLLAMA_MODEL={model}", text)
    else:
        text = text.rstrip("\n") + f"\nDEFAULT_OLLAMA_MODEL={model}\n"
    ENV_PATH.write_text(text)
    print(f"pinned DEFAULT_OLLAMA_MODEL={model}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="include models without tool support")
    ap.add_argument("--set", metavar="MODEL", help="pin a model as DEFAULT_OLLAMA_MODEL")
    args = ap.parse_args()

    if args.set:
        pin_model(args.set)

    provider = Provider(env_path=str(ENV_PATH))
    models = sorted(provider.list_models(), key=lambda m: m.name)

    tools = [m for m in models if m.supports_tools]
    shown = models if args.all else tools

    print(f"provider : {provider.base_url}")
    print(f"default  : {provider.default_model}")
    print(f"total    : {len(models)} models, {len(tools)} tool-capable\n")

    name_w = max((len(m.name) for m in shown), default=10) + 2
    for m in shown:
        marker = "*" if m.name == provider.default_model else " "
        caps = ",".join(m.capabilities) or "-"
        print(f" {marker} {m.name:<{name_w}} {caps}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
