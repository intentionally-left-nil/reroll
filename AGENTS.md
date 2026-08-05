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

## Never modify `docs/*.md`

Files under `docs/` are human-authored decision records. Never create, edit,
or delete them.

- If the work *contradicts* a doc, stop and surface the conflict to the user.
  Wait for the user to resolve the doc before continuing.
- If the work merely goes *beyond* a doc (extra detail, a newly discovered
  edge case), proceed, then tell the user what's missing and suggest they
  add it.

`specs/` is the agent-writable counterpart: approved implementation specs,
describing the final intended state of the code. Write there instead, and
link to the relevant `docs/` file for background rather than restating it.

## Never suppress, always fix

Do not add `# pragma: no cover`, `# type: ignore`, `# ty: ignore`, `# noqa`,
or any other coverage/lint/type-checker suppression comment to silence a
failing check. These hide real problems instead of fixing them: an
uncovered branch usually means the code is unreachable (delete it or
restructure so the type checker/tests can prove it out) or untested (write
the missing test), and a type/lint error usually means the code — or the
API it's calling — needs to change. Treat a suppression as a signal to
redesign, not a way to get `make ci` green.

If you believe a suppression is genuinely the right call, stop and ask the
user for explicit permission before adding it, and say why you think no
fix exists.
