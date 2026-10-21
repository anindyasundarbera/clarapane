# AGENTS.md — rules for anyone (human or AI) working in this repo

These rules are binding. They exist because this project's value is not only
*what* it does but whether its history explains *why*. Read this before
committing, before touching secrets, and before skipping a tool gate.

---

## 1. Secrets

- **Never commit `.env`.** It is gitignored; keep it that way.
- Only `.env.example` is committed, and it contains **key names only**, never values.
- If a secret is ever committed, deleting the file in a later commit is **not**
  sufficient — it stays in history. Treat it as a rotation incident: rotate the
  key, then rewrite history.

Verify before committing:

```bash
git status --short            # .env must never appear
git diff --cached --name-only | grep -E '(^|/)\.env$' && echo "STOP: secret staged"
```

## 2. Commit messages

Format: **Conventional Commits**, enforced by `.githooks/commit-msg`.

```
<type>(<scope>): <lowercase subject, max 72 chars, no trailing period>
```

Allowed types: `feat` `fix` `docs` `test` `refactor` `perf` `chore` `build` `ci`

The body must be self-sufficient — a reader should understand the change
**without opening the diff**. Required sections:

```
Why:
  The problem or risk that motivated this.
What:
  - Bullet list of concrete changes.
Decision:
  Alternatives considered and why rejected. Omit only if there were none.
Evidence:
  Tests, measurements, or files that prove it works.
Refs: backlog:<id> | phase:<n> | #issue
```

Why this matters: `git log` is the project's memory.

```bash
git log --grep="Decision:"          # why things are the way they are
git log --grep="Refs: backlog:"     # which deferred work each commit touched
```

## 3. Commit granularity

One commit per **decision or coherent capability**, not per file and not per
work session. A commit must be individually understandable and leave the tree
in a working state.

## 4. Validation before committing

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests -q
python scripts/check_commits.py
```

Do not commit with failing tests. If a test must be skipped, say so in the
commit body under `Evidence:`.

## 5. Deferred work and the backlog

Anything consciously **not** built goes into `backlog.json`, never only into a
conversation. Use the CLI:

```bash
PYTHONPATH=src .venv/bin/python -m glassbox.backlog due
PYTHONPATH=src .venv/bin/python -m glassbox.backlog add --title "..." --why "..." --area "..."
```

Every open item needs a `trigger` and `acceptance` criteria or `validate()` rejects it.
Mark the hook point in code with `TODO(<backlog-id>)`; a test enforces that every
such marker names a real backlog item.

Rejected ideas stay in the backlog with `status: "rejected"` and their evidence.
Do not re-propose a rejected item without new measurements.

## 6. Agent design invariants

These are not style preferences; tests depend on them.

1. **Propose before mutate.** Writes require a prior proposal; `apply_edit` is a
   separate, gated tool from `propose_edit`.
2. **Risk lives in the registry, not the prompt.** `ToolSpec.risk` and `.gate`
   decide permission. Never let the model negotiate its own access.
3. **One log writer.** All events go through `glassbox.events.EventLog`. Nothing
   else opens the trace file.
4. **Errors are data.** Tool failures return `{"error": ...}` as a tool message
   so the model can recover; they must not crash the loop.
5. **Capability profiles are fixed per run.** Never change the tool list between
   turns within a run — it invalidates the provider prompt cache.
6. **Least privilege is enforced by construction.** A profile's tool list is the
   only thing the model sees; `select()` raises on unknown tools.
7. **Path jail on every access.** All paths resolve through `Workspace.resolve()`.
   The agent may read its own source but never write it.

## 7. Environment

- Python: `uv venv .venv` then `uv pip install --python .venv/bin/python -e .`
- Run the agent: `PYTHONPATH=src .venv/bin/python -m glassbox.cli "<task>"`
- Tests: `PYTHONPATH=src .venv/bin/python -m pytest tests -q`
- Model pinning: `scripts/list_models.py --set <model>`

## 8. Things that will bite you

- `find_dotenv()` raises `AssertionError` when run from a `python -` heredoc.
  Pass an explicit path: `load_dotenv(".env", override=True)`.
- Ollama returns the reasoning trace out-of-band in `message.model_extra["reasoning"]`,
  not as content. It is logged; use it.
- Model aliases resolve both ways (`gpt-4o` style `:cloud` suffixes are optional).
- Tool count does **not** predict latency, and schema tokens are ~88% cached.
  Do not "optimise" by trimming tools per turn.
