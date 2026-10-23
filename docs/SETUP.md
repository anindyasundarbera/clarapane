# Setup and device migration

Everything here is deliberately **outside version control**, so it is the part a new
device does not get from `git clone`. This file exists so a migration does not depend
on remembering it.

Read this when: setting up a new machine, re-cloning after a wipe, or onboarding
someone else.

---

## 1. What git gives you, and what it does not

`git clone` restores every tracked file: source, tests, evals, docs, hooks, workflow.
It does **not** restore any of the following, because each one is either a secret, a
local convenience, or machine state.

| Lost on migration | Where it lives | Restore in |
|---|---|---|
| Provider credentials | `.env` (gitignored) | §3 |
| Hooks being active | `core.hooksPath` in local git config | §4 |
| Commit message template | `commit.template` in local git config | §4 |
| Python virtualenv | `.venv/` (gitignored) | §2 |
| GitHub Actions secrets | GitHub repository settings | §5 |
| Optional runtime identity | `CLARAPANE_APP_NAME`, `CLARAPANE_OPERATOR_ID` in `.env` | §3 |

**The hooks are the easy thing to forget.** Probed in a clean clone:

```
hooksPath in fresh clone: NOT SET
active hooks: 14 samples only
```

All three hooks (`commit-msg`, `pre-commit`, `pre-push`) are **inert** until §4 is
done. They are preventive only; `scripts/audit_defects.py` and the test suite are the
detective controls, and CI is the only control that travels with the repository.

---

## 2. Toolchain and dependencies

Verified on this machine:

```
Python 3.14.4      (project requires >= 3.11)
uv 0.12.3
```

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e .
uv pip install --python .venv/bin/python pytest
```

`pytest` is **not** in `pyproject.toml` dependencies — install it separately, or the
test suite cannot run. CI does the same (`pip install -e . pytest`).

The project runs from the tree via `PYTHONPATH=src`; it is not installed as a
distribution. A consequence worth knowing: `importlib.metadata.version("clarapane")`
raises `PackageNotFoundError` unless you ran `-e .`, which is why
`clarapane.identity.version()` reads `pyproject.toml` instead.

Verify:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests -q     # expect: all pass, 2 skipped
```

The 2 skips are the hook tests, and they are expected on a fresh clone (§4). A skip
there is correct: unconfigured is not the same as misconfigured.

---

## 3. Credentials: `.env`

```bash
cp .env.example .env
```

`.env` is gitignored and must never be committed. Only `.env.example` is tracked, and
it contains key names with **no values**. `scripts/audit_defects.py` fails
(`env-matches-template`) if `.env` is missing a key that `.env.example` declares.

| Key | Required | Notes |
|---|---|---|
| `PROVIDER_BASE_URL` | yes | Any OpenAI-compatible endpoint. Verified: `https://ollama.com/v1` |
| `PROVIDER_API_KEY` | **yes, secret** | **One** key, used for chat *and* model listing |
| `PROVIDER_MODEL` | yes | Must name a **tool-capable** model. Check with `make models` |
| `CLARAPANE_OPERATOR_ID` | recommended | Accountable human recorded on every approval. Defaults to the OS login |
| `CLARAPANE_APP_NAME` | optional | Cosmetic display name only; does not change behaviour |

The names are provider-agnostic on purpose. They were `OPENAI_*`, which named the
SDK rather than the service — an `OPENAI_API_KEY` holding an Ollama key is a
mislabelling that costs a confused minute on every new machine. Legacy names are still
read as a fallback, so an existing `.env` keeps working.

Two dead keys were also removed: `OLLAMA_HOST` and `OLLAMA_API_KEY` were **required by
the template and read by nothing**. The capability lookup derives its host from
`PROVIDER_BASE_URL` and authenticates with the same single key.

**On a new device, the secrets must be re-entered by hand.** They are not in git and
should not be transferred through it. Retrieve them from your provider's dashboard.

Verify:

```bash
PYTHONPATH=src .venv/bin/python scripts/audit_defects.py     # expect: env-matches-template ok
make models                                                   # must list a tool-capable model
```

---

## 4. Local git configuration (lost silently)

These two settings live in `.git/config`, which is not committed. Without them the
repository still works, but its guardrails quietly stop applying.

```bash
git config core.hooksPath .githooks    # enable commit-msg, pre-commit, pre-push
git config commit.template .gitmessage # commit message scaffold
```

Verify:

```bash
git config core.hooksPath      # expect: .githooks
git config commit.template     # expect: .gitmessage
ls -l .githooks/               # all three must be executable
```

If a hook is present but not executable, `chmod +x .githooks/*` — a non-executable hook
is a hook that silently does nothing.

---

## 5. GitHub Actions secrets

CI has two jobs. The `offline` job needs **nothing** and always runs. The `eval` job
calls a live model and needs the same three provider values as `.env`.

It **fails loudly** when they are missing rather than skipping:

```
::error title=Eval cannot run::PROVIDER_API_KEY is not configured.
This job is red by design: a skipped eval would report success without measuring anything.
```

That is intentional. A skipped eval is a green build that tested nothing.

Set them:

```bash
make setup-apply
```

It reads `.env` and sets `PROVIDER_BASE_URL`, `PROVIDER_API_KEY` and
`PROVIDER_MODEL` for you — the same values CI will use, so local and remote cannot
disagree about which key or model is in play. Secret values are passed on stdin, never
as a process argument (which is world-readable via `/proc` on Linux).

Preview first if you prefer:

```bash
make setup-apply DRY=1     # or: python scripts/setup_env.py --apply --dry-run
```

By hand, if you would rather:

```bash
gh secret set PROVIDER_BASE_URL   --body "https://ollama.com/v1"
gh secret set PROVIDER_API_KEY    --body "<key>"
gh secret set PROVIDER_MODEL      --body "<tool-capable model>"
```

Verify:

```bash
gh secret list                       # expect three names, no values shown
gh run list --workflow=ci.yml        # then re-run and check both jobs
```

Until these exist, **only the `offline` job is meaningful** and the PR check list will
show `eval suite (live model)` as failed. That is an unset secret, not broken code.

**Never** put a key in a workflow file, a commit, an issue, or a PR body.

---

## 6. Verification checklist after migrating

Run in order; each step depends on the previous.

**The short path**, once `.env` exists:

```bash
git clone <repo> && cd clarapane
cp .env.example .env                       # fill in PROVIDER_* by hand
make install                               # venv + package + pytest
make setup                                 # verify everything, change nothing
make setup-apply                           # enable hooks + push CI secrets from .env
```

**The explicit path**, if you want each step visible — this is what `make setup` does:

```bash
cd agentic_training

uv venv .venv
uv pip install --python .venv/bin/python -e . pytest

cp .env.example .env                       # then fill in the secrets by hand

git config core.hooksPath .githooks
git config commit.template .gitmessage

PYTHONPATH=src .venv/bin/python -m pytest tests -q        # all pass
PYTHONPATH=src .venv/bin/python scripts/audit_defects.py  # no defects
PYTHONPATH=src .venv/bin/python scripts/check_docs.py     # 7 checks, no drift
PYTHONPATH=src .venv/bin/python scripts/check_commits.py  # commits conform
make run TASK="Where is add defined?"                     # proves the provider key works
```

Expected on a healthy setup:

| Command | Expected |
|---|---|
| `pytest tests -q` | all pass, 2 skipped (hook tests) |
| `audit_defects.py` | `no defects found` |
| `check_docs.py` | `7 check(s), no drift` |
| `check_commits.py` | `OK: N commit message(s) conform` |
| `make run` | a banner, a trace appended to `runs/runs.jsonl`, a result |

If `make run` fails with `PROVIDER_BASE_URL and PROVIDER_API_KEY must be set`, `.env` is
missing or not filled — not a code problem.

---

## 7. Things that bite on a fresh machine

Recorded because they cost time once already.

- **`find_dotenv()` raises `AssertionError` from a `python -` heredoc.** Pass an
  explicit path: `load_dotenv(".env", override=True)`.
- **A worktree has no `.env`.** It is gitignored, so `git worktree add` produces a
  checkout with no credentials. Resolve the main checkout via
  `git rev-parse --git-common-dir` if a tool needs to find it.
- **Two worktrees cannot share a branch.** Git rejects it:
  `fatal: '<branch>' is already used by worktree at ...`.
- **`pytest` is not a project dependency.** A venv built only from `pyproject.toml`
  cannot run the suite.
- **Hooks silently do nothing without `core.hooksPath`.** §4 exists for this reason.
- **`.pth`-style `PYTHONPATH=src` is required everywhere.** Forgetting it produces
  `ModuleNotFoundError: No module named 'clarapane'`, which looks like a broken
  install and is not.

---

## 8. What is *not* needed

Stated explicitly so a migration does not grow unnecessary steps:

- **No database.** The trace is append-only JSONL. Any `*.db` is derived and gitignored.
- **No Docker.** Nothing in the project requires a container.
- **No Node or npm.** The only JavaScript in the repo is the devcontainer/tooling
  outside this project's scope.
- **No tagging on migration.** Tags come from the remote with the clone.
- **No separate docs build.** Documentation is Markdown in the repo and is verified by
  `check_docs.py`, not generated by a static-site tool.
