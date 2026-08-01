# AGENTS.md

Reroll generates conda v3 repodata from a wheel's `METADATA` file.

## Workflow: tests before code

Every edge case gets a test *before* the code that handles it exists.

1. New edge case? Write a unit test for the specific sub-behavior (e.g. a
   single parsing rule, a single field mapping) *before* implementing it.
   Prefer small, targeted tests over broad end-to-end ones. (Integration
   tests against real published wheels will be added once the core
   conversion exists.)
2. New test starts failing (or `xfail` if it can't even run yet). Run
   `make test` to confirm it fails for the right reason.
3. Implement the minimum needed to make it pass. A test is never edited to
   match the implementation's output.

## Commands

- `make install` — sync deps (uv)
- `make test` — unit tests
- `make lint` / `make format` — ruff
- `make typecheck` — ty
- `make ci` — everything CI runs; run this before opening a PR

## Code conventions

- Package code lives in `src/reroll/`; tests in `tests/`.
- Dev-only deps go in `[dependency-groups.dev]`.
- Python 3.13+, fully typed. `ty` and `ruff` must pass with zero warnings.
- Prefer small, pure functions — conversion logic should be trivially
  drivable from a single unit test's `METADATA` fixture.
