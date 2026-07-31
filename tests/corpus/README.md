# Corpus cases

Hand-crafted METADATA edge cases. Add one *before* implementing the code
that should handle it.

## Format

```
tests/corpus/cases/<short-descriptive-name>/
  METADATA        # the wheel METADATA file to convert
  repodata.json   # the v3 repodata reroll must produce for it
```

- `<short-descriptive-name>` should name the edge case, e.g.
  `extras-with-markers`, `no-requires-python`, `multiline-description`.
- `METADATA` is a real (or minimal-but-valid) METADATA file per the
  [core metadata spec](https://packaging.python.org/en/latest/specifications/core-metadata/).
  Keep it as small as possible while still exercising the edge case.
- `repodata.json` is the exact expected output for that single package.

## Running

```
make test
```

Cases are discovered automatically by `tests/test_corpus.py` — no code
changes needed to register a new one.
