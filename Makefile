# Short commands so routine work does not need long inline invocations.
# Usage: make test | make audit | make run TASK="..." | make clean

PY := .venv/bin/python
export PYTHONPATH := src

.PHONY: help test audit run models clean backlog commit eval divergences docs install setup setup-apply setup-check

help:
	@echo "make test       run the test suite (includes the defect audit)"
	@echo "make audit      run the defect audit only"
	@echo "make run TASK=  run the agent on a task"
	@echo "make models     list tool-capable cloud models"
	@echo "make backlog    show actionable deferred work"
	@echo "make eval       run the eval suite (live model calls, ~2-4 min)"
	@echo "make divergences report failed attempts and guesses from traces"
	@echo "make docs      verify documentation claims against the code"
	@echo "make install   create .venv and install the package + pytest"
	@echo "make setup     verify this machine from .env (changes nothing)"
	@echo "make setup-apply push provider values to CI secrets from .env"
	@echo "make setup-check verify only, never change anything"
	@echo "make clean      remove caches and bytecode"

test:
	$(PY) -m pytest tests -q

audit:
	$(PY) scripts/audit_defects.py

run:
	@test -n "$(TASK)" || { echo 'usage: make run TASK="where is add defined?"'; exit 2; }
	$(PY) -m clarapane.cli "$(TASK)"

models:
	$(PY) scripts/list_models.py

backlog:
	$(PY) -m clarapane.backlog due

# Commit with the trace included by default, so evidence is never left behind.
commit:
	@test -n "$(MSG)" || { echo 'usage: make commit MSG="feat(scope): subject"'; exit 2; }
	git add -A
	ALLOW_UNSTAGED=1 git commit -F - <<'MSG_EOF'
	$(MSG)
	MSG_EOF

# Live eval run. Not part of `make test`: it needs network and a model.
# Traces are kept per case so a failure cites its own evidence instead of a
# temp directory that disappears with the run.
eval:
	$(PY) -m evals.runner --json evals/latest.json --trace-dir eval-traces

clean:
	find . -name '__pycache__' -not -path './.venv/*' -type d -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache
	@echo "cleaned caches"

# How predictable is the agent? Every failure and every guess, counted by class.
# --strict fails on HARNESS divergence only: our own mispredictions are defects.
divergences:
	$(PY) -m clarapane.divergence --traces runs --strict

# Docs that disagree with code are a false claim. Runs in `make test` too, so a fresh
# clone without hooks still fails on drift.
docs:
	$(PY) scripts/check_docs.py

# One command to make a machine work. Reads .env; never prints a secret value.
install:
	uv venv .venv
	uv pip install --python .venv/bin/python -e .
	uv pip install --python .venv/bin/python pytest

setup:
	$(PY) scripts/setup_env.py

# Also pushes PROVIDER_* to GitHub Actions secrets, from the same .env, so CI and
# local cannot disagree about which key or model is in use.
setup-apply:
	$(PY) scripts/setup_env.py --apply

setup-check:
	$(PY) scripts/setup_env.py --check
