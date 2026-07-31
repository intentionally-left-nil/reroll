# AGENTS.md

Reroll generates conda v3 repodata from a wheel's `METADATA` file.

## Workflow: tests before code

Every edge case gets a test *before* the code that handles it exists.

1. New edge case? Add a corpus case: `tests/corpus/cases/<name>/` with a
   `METADATA` file and the `repodata.json` you expect reroll to produce.
   Format details: `tests/corpus/README.md`. (Integration tests against
   real published wheels will be added once the core conversion exists.)
2. New case starts `xfail` (see `tests/test_corpus.py`). Run `make test` to
   confirm it fails for the right reason.
3. Implement the minimum needed to make it pass, then delete that case's
   `xfail`. A case is never edited to match the implementation's output.

## Commands

- `make install` — sync deps (uv)
- `make test` — corpus + unit tests
- `make lint` / `make format` — ruff
- `make typecheck` — ty
- `make ci` — everything CI runs; run this before opening a PR

## Code conventions

- Package code lives in `src/reroll/`; tests in `tests/`.
- Dev-only deps go in `[dependency-groups.dev]`.
- Python 3.13+, fully typed. `ty` and `ruff` must pass with zero warnings.
- Prefer small, pure functions — conversion logic should be trivially
  drivable from a single corpus case's `METADATA` file.
