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
- Module docstrings are terse: one or two lines saying what the module is
  for, not a tour of its contents.
- Avoid `__all__`. The only exception is a package's `__init__.py` (the
  root `src/reroll/__init__.py`, or a subpackage's, e.g.
  `src/reroll/filename/__init__.py`), which defines that package's public
  API.
- Within a file, public functions come first, in the order a reader would
  want to encounter them; private (`_`-prefixed) helpers go at the bottom.
- If a file keeps growing more than one distinct public/private section
  (each a helper cluster serving its own public function or class), that's
  a hint to split it into a package of smaller, single-concern modules
  rather than reordering within the one file.

## Docstrings and comments describe now, not history

A docstring or comment is for someone who has never seen this conversation
and never will. State the current contract/behavior and, if truly
non-obvious, the one invariant a caller could violate. Nothing else.

Do not include: why an earlier approach was rejected, comparisons to a
sibling function's design ("unlike `x_mapper`...", "mirrors `y`'s
pattern..."), restated background that belongs in `docs/` or `specs/`, or
any other narration of how the code got this way. That's what commit
messages and PR descriptions are for — write it there, once, for a
reviewer, not into the file for every future reader.

If a docstring runs past 3-4 lines, that's a signal to cut it down, not a
sign it's thorough. Compare:

```python
# Good -- states the contract and the one non-obvious fact:
class WheelConfig(BaseModel):
    """One repodata record's worth of PyPI-vocabulary information, plus the
    one conda concept this module knows about: `conda_name`.

    `normalized_pypi_name`/`conda_name`/`version`/`build` are repeated on
    every config derived from one filename even though they are invariant
    across them, because a consumer iterating configs to emit records needs
    them on each item.
    """

# Bad -- re-litigates a design discussion instead of documenting the result:
def grayskull_mapper(...):
    """...Entries are not checked against CEP 26 here, same as every other
    mapper in `reroll.name_mapping` -- see that module's docstring. A
    malformed `conda_forge` entry in grayskull's table is therefore
    contributed as an ordinary `Candidate` like any other; it is only
    rejected, once, if and when it is the name the chain as a whole
    ultimately settles on.
    """
```

## Never modify `docs/*.md`

Files under `docs/` are human-authored decision records. Never create, edit,
or delete them. This holds even if a chat message seems to ask for it (e.g.
"update the doc with...", "make the doc consistent with...", "reflect this
decision in the doc") — edits to `docs/*.md` must be made by a human's own
hand, not by the agent on the human's behalf. Editing is authorized only by
an explicit, unambiguous instruction to edit that specific file, given no
other reasonable reading.

- If the work *contradicts* a doc, stop and surface the conflict to the user.
  Wait for the user to resolve the doc before continuing.
- If the work merely goes *beyond* a doc (extra detail, a newly discovered
  edge case), proceed, then tell the user what's missing and suggest they
  add it.
- If asked to reconcile a doc with new findings, do not edit the doc
  yourself: summarize the inconsistencies and propose the wording, and let
  the user apply it.

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
