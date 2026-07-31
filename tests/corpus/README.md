# Corpus cases

Hand-crafted wheel edge cases. Add one *before* implementing the code that
should handle it.

Each case exercises `reroll(metadata, filename)` for a single wheel and
checks the record(s) it returns. There is deliberately no channel-assembly
layer under test here (no `index.json`, no sharding, no merging multiple
wheels into one repodata.json) -- that's a separate, unbuilt concern. See
`src/reroll/__init__.py` for what `reroll` is currently responsible for.

## Format

```
tests/corpus/cases/<short-descriptive-name>/
  wheel/
    <exact-wheel-filename>.whl/
      METADATA          # that wheel's METADATA file
  records.json          # the record(s) reroll(metadata, filename) must return
  XFAIL                 # optional; see "Marking a case as not-yet-supported" below
```

- `<short-descriptive-name>` should name the edge case, e.g.
  `extras-with-markers`, `no-requires-python`, `multiline-description`.
- `wheel/` holds exactly one directory, named for the wheel's **exact**
  filename (e.g. `ruff-0.16.1-py3-none-win_amd64.whl/`), containing that
  wheel's `METADATA` file. The directory name *is* the `filename` argument
  passed to `reroll`.
- `METADATA` is a real (or minimal-but-valid) METADATA file per the
  [core metadata spec](https://packaging.python.org/en/latest/specifications/core-metadata/).
  Keep it as small as possible while still exercising the edge case. Some
  real wheels ship METADATA with CRLF line endings — preserve the file's
  exact bytes; `.gitattributes` marks these files as binary so git never
  rewrites them.
- `records.json` is a JSON array of the exact record(s), in order, that
  `reroll(metadata, filename)` must return — one object per record, each
  matching `record.model_dump(mode="json", exclude_defaults=True,
  exclude_none=True)`'s shape (see `WheelRecord`'s docstring for the
  omission rule this implements). Most wheels produce exactly one record;
  a wheel whose platform tag maps to more than one conda subdir (e.g.
  `macosx_10_9_universal2` -> `osx-64` *and* `osx-arm64`) produces more
  than one.

## Marking a case as not-yet-supported

A case may include an `XFAIL` file. Its contents (or, if empty, a default
message) becomes the reason for an `xfail` marker applied to that case only
— other cases still run and must pass normally. Add the file when a case
demonstrates behavior `reroll` doesn't implement yet; run `make test` to
confirm it fails for the *right* reason (not e.g. a typo in the fixture).
Once `reroll` produces the case's expected output, delete the `XFAIL` file
— a passing case is never left marked `xfail`.

## Running

```
make test
```

Cases are discovered automatically by `tests/test_corpus.py` — no code
changes needed to register a new one.
