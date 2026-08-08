# lenient_parser

A Python port of uv's `LenientRequirement` and `LenientVersionSpecifiers`
(`crates/uv-pypi-types/src/lenient_requirement.rs`), used by reroll to
accept the same non-conforming `Requires-Dist`/`Requires-Python` values uv
does. Background and the decision to depend on uv's logic specifically
(rather than pip's, or a from-scratch implementation): `docs/wheel_metadata.md`.

## License

uv is dual MIT/Apache-2.0 licensed. The logic in this package -- the
`FIXUPS` table and the fixup-retry loop in `__init__.py` -- is copied from
uv's source under the MIT license reproduced in [`LICENSE-MIT`](./LICENSE-MIT).

Copyright (c) 2025 Astral Software Inc.

## Staying in sync with uv

`__init__.py` is kept as close to a line-for-line port of
[`lenient_requirement.rs`](https://github.com/astral-sh/uv/blob/main/crates/uv-pypi-types/src/lenient_requirement.rs)
as Python idiom allows -- same `FIXUPS` order, same regexes, same
compounding retry loop -- so that a future upstream change to that file
can be diffed against uv's git history and re-applied here directly. Any
deviation from the Rust source is called out in `__init__.py`'s module
docstring, along with why it was necessary (e.g. a Rust regex replacement
construct with no direct Python `re` equivalent).

`test_lenient_parser.py` (in the top-level `tests/` directory, alongside
reroll's other test modules) ports every test case from
`lenient_requirement.rs`'s own `#[cfg(test)] mod tests`, plus additional
cases for branches uv's Rust tests don't happen to exercise and for
rust-to-python regex porting sharp edges.
