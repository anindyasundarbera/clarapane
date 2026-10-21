# Short commands so routine work does not need long inline invocations.
# Usage: make test | make audit | make run TASK="..." | make clean

PY := .venv/bin/python
export PYTHONPATH := src

.PHONY: help test audit run models clean backlog

help:
	@echo "make test       run the test suite (includes the defect audit)"
	@echo "make audit      run the defect audit only"
	@echo "make run TASK=  run the agent on a task"
	@echo "make models     list tool-capable cloud models"
	@echo "make backlog    show actionable deferred work"
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

clean:
	find . -name '__pycache__' -not -path './.venv/*' -type d -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache
	@echo "cleaned caches"
