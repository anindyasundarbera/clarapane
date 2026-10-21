# Copilot instructions

The authoritative rules for this repository live in [`AGENTS.md`](../AGENTS.md).
Read it before making changes. Summary of the non-negotiables:

- **Never commit `.env`.** Only `.env.example` with key names, no values.
- **Commit format:** `<type>(<scope>): <lowercase subject>`, Conventional Commits,
  enforced by `.githooks/commit-msg`. Types: feat fix docs test refactor perf chore build ci.
- **Commit bodies must be self-sufficient** and end with a `Refs:` footer.
  Use `Why: / What: / Decision: / Evidence: / Refs:` sections.
- **One commit per decision or coherent capability**, not per file.
- **Deferred work goes in `backlog.json`**, managed via `python -m glassbox.backlog`.
  Mark the hook point with `TODO(<backlog-id>)`.
- **Run before committing:** `PYTHONPATH=src .venv/bin/python -m pytest tests -q`
  and `python scripts/check_commits.py`.

Design invariants (propose-before-mutate, risk-in-registry, single log writer,
errors-as-data, fixed profiles per run, path jail) are listed in section 6 of
`AGENTS.md` and are covered by tests.
