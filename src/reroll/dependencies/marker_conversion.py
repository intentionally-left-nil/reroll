"""Convert a `markerpry` marker tree into a matchspec `when=` condition.

Implements docs/matchspec.md's marker-to-matchspec conversion table.
"""

from __future__ import annotations

from markerpry import BooleanNode, CompareNode, ContainsNode, LeafModifier, Node, OperatorNode
from packaging.version import InvalidVersion, Version

from reroll.dependencies.python_version_membership import (
    is_python_version_in_literal,
    rewrite_python_version_in_modifier,
)
from reroll.dependencies.version_format import format_version, format_version_literal
from reroll.errors import UnconvertableMarkerError, UnconvertablePythonVersionEqualityError
from reroll.filename.python_latest_release import resolve_upper_bound

_SYS_PLATFORM = {"linux": "__linux", "darwin": "__osx", "win32": "__win"}
_PLATFORM_SYSTEM = {"Linux": "__linux", "Darwin": "__osx", "Windows": "__win"}
_OS_NAME = {"posix": "__unix", "nt": "__win"}
_VIRTUAL_PACKAGE_KEYS = {
    "sys_platform": _SYS_PLATFORM,
    "platform_system": _PLATFORM_SYSTEM,
    "os_name": _OS_NAME,
}
_FULL_VERSION_KEYS = frozenset({"python_full_version", "implementation_version"})
_ORDERED_COMPARATORS = frozenset({">=", ">", "<=", "<"})
_EQUALITY_COMPARATORS = frozenset({"==", "!="})


def marker_condition(node: Node, *, abi3_upper_bound: str | None = None) -> str:
    """`node`'s matchspec `when=` value (unquoted), per
    docs/matchspec.md#marker-to-matchspec-conversion.

    Every `python_version in "<literal>"` clause (key on the left, not
    negated) is rewritten first, per docs/matchspec.md's "python_version
    workaround": an `or`-chain of `python_version == "3.<minor>"` terms,
    one per candidate minor from `3.0` through `abi3_upper_bound` whose
    `"3.<minor>"` form is a substring of `<literal>`
    (`reroll.dependencies.python_version_membership`). `abi3_upper_bound`
    is the same minor-only version string (e.g. `"3.15"`) `explode_abi3`
    takes -- `None` (the default) defers to `latest_python_minor`, and
    only once a clause actually needing it is found, so a marker with no
    `python_version in` clause never triggers that lookup.

    Raises `UnconvertablePythonVersionEqualityError` if `node` -- after
    that rewrite -- is constant: either handed in as a bare boolean, or
    reduced to one by a `python_version in "<literal>"` clause matching no
    candidate minor at all. A constant has no matchspec representation
    (docs/matchspec.md's "universally false"/"universally true" note).

    Raises `UnconvertableMarkerError` for anything else the table can't
    represent: any other `in`/`not in` test, `platform_machine` or any
    other marker key without a matchspec equivalent, `!=` (or any
    comparator besides `==`) against `sys_platform`/`platform_system`/
    `os_name`, or an unrecognized value or comparator for a key this
    function does handle. A marker containing an `extra` clause is not
    this function's concern -- callers should check for that
    (`"extra" in node`) before calling.
    """
    rewritten = node.modify(leaf=_rewrite_python_version_in(abi3_upper_bound))
    if isinstance(rewritten, BooleanNode):
        raise UnconvertablePythonVersionEqualityError(
            f"marker {node} is always {bool(rewritten)} once its python_version "
            "'in' clause(s) resolve, which has no matchspec representation"
        )
    return _condition(rewritten)


def _condition(node: Node) -> str:
    if isinstance(node, OperatorNode):
        return f"{_wrap(node._left)} {node.operator} {_wrap(node._right)}"
    if isinstance(node, ContainsNode):
        raise UnconvertableMarkerError(
            f"'in'/'not in' markers are not supported for matchspec conversion: {node}"
        )
    if isinstance(node, CompareNode):
        return _compare_condition(node)
    raise AssertionError(f"unreachable: unexpected marker node type {type(node).__name__}")


def _wrap(node: Node) -> str:
    condition = _condition(node)
    return f"({condition})" if isinstance(node, OperatorNode) else condition


def _rewrite_python_version_in(abi3_upper_bound: str | None) -> LeafModifier:
    """Builds the `markerpry` leaf modifier `marker_condition` runs over
    the whole marker tree once, up front: `rewrite_python_version_in_modifier`,
    with `abi3_upper_bound` resolved to a concrete minor -- lazily, and at
    most once per `marker_condition` call, since `resolve_upper_bound` may
    hit the network (`latest_python_minor`) and most markers have no
    `python_version in` clause to justify that cost at all.
    """
    resolved_max_minor: int | None = None

    def leaf(node: Node) -> Node:
        nonlocal resolved_max_minor
        if not is_python_version_in_literal(node):
            return node
        if resolved_max_minor is None:
            resolved_max_minor = resolve_upper_bound(abi3_upper_bound)
        return rewrite_python_version_in_modifier(resolved_max_minor)(node)

    return leaf


def _compare_condition(node: CompareNode) -> str:
    key, comparator, literal = node.key, node.comparator, node.literal
    if key == "python_version":
        return _python_version_condition(comparator, literal)
    if key in _FULL_VERSION_KEYS:
        return _full_version_condition(key, comparator, literal)
    if key in _VIRTUAL_PACKAGE_KEYS:
        return _virtual_package_condition(key, comparator, literal)
    raise UnconvertableMarkerError(f"marker key {key!r} has no matchspec equivalent")


def _python_version_condition(comparator: str, literal: str) -> str:
    if comparator in _EQUALITY_COMPARATORS:
        glob_major = _python_version_major_glob(literal)
        if glob_major is not None:
            return f"python!={glob_major}.*" if comparator == "!=" else f"python={glob_major}"
    major, minor, exact = _parse_python_version_literal(literal)
    this_minor = f"{major}.{minor}"
    next_minor = f"{major}.{minor + 1}"
    if comparator == "==":
        if not exact:
            raise UnconvertablePythonVersionEqualityError(
                f"python_version literal {literal!r} is not major.minor precision, so "
                "'==' against it can never hold"
            )
        return f"python>={this_minor}.0a0,<{next_minor}.0a0"
    if comparator == "!=":
        if not exact:
            raise UnconvertablePythonVersionEqualityError(
                f"python_version literal {literal!r} is not major.minor precision, so "
                "'!=' against it always holds"
            )
        return f"python!={this_minor}.*"
    if comparator == ">=":
        return f"python>={this_minor if exact else next_minor}.0a0"
    if comparator == ">":
        return f"python>={next_minor}.0a0"
    if comparator == "<=":
        return f"python<{next_minor}.0a0"
    if comparator == "<":
        return f"python<{this_minor if exact else next_minor}.0a0"
    raise UnconvertableMarkerError(f"comparator {comparator!r} is not supported for python_version")


def _python_version_major_glob(literal: str) -> int | None:
    """`literal`'s major version if it's a bare major-only PEP 440 prefix
    glob (`"X.*"`), else `None`. `python_version == "X.*"` is a
    well-formed marker meaning "any Python X.y" -- `packaging`'s own
    marker evaluation resolves it as an ordinary prefix match, and
    matchspec's fuzzy match (`=X`) resolves the same way, so the
    conversion is `=X`, not the anchored range the plain (non-glob) table
    entries need.

    Only a *bare* major glob is handled here, deliberately not `_glob_prefix`'s
    more general one: matchspec's fuzzy match is sensitive to how many
    segments it's given (`=3.9.0` and `=3.9` are different matches), where
    a plain comparison is not (PEP 440 treats a trailing zero release
    segment as insignificant) -- so reusing `_glob_prefix` verbatim would
    misconvert a glob at minor-or-deeper precision.
    """
    if not literal.endswith(".*"):
        return None
    prefix = literal.removesuffix(".*")
    try:
        version = Version(prefix)
    except InvalidVersion:
        return None
    if (
        len(version.release) != 1
        or version.epoch
        or version.pre is not None
        or version.post is not None
        or version.dev is not None
        or version.local is not None
    ):
        return None
    return version.release[0]


def _parse_python_version_literal(literal: str) -> tuple[int, int, bool]:
    """`literal`'s major, minor, and whether every release segment beyond
    minor is zero ("exact", e.g. `"3"` and `"3.9.0"`, as opposed to
    `"3.9.2"`) -- a version's trailing zero release segments are
    insignificant for comparison purposes (PEP 440), so an exact literal
    compares against `python_version` exactly as its bare major.minor
    form would.

    Raises `UnconvertableMarkerError` if `literal` isn't a plain
    major[.minor[.micro...]] release: an unparseable string, or one with
    an epoch, pre-release, post-release, dev-release, or local segment.
    """
    try:
        version = Version(literal)
    except InvalidVersion:
        raise UnconvertableMarkerError(
            f"python_version literal {literal!r} is not a valid version"
        ) from None
    if (
        version.epoch
        or version.pre is not None
        or version.post is not None
        or version.dev is not None
        or version.local is not None
    ):
        raise UnconvertableMarkerError(
            f"python_version literal {literal!r} is not a plain major.minor(.micro) version"
        )
    release = version.release
    minor = release[1] if len(release) > 1 else 0
    return release[0], minor, all(segment == 0 for segment in release[2:])


def _full_version_condition(key: str, comparator: str, literal: str) -> str:
    if comparator not in _EQUALITY_COMPARATORS | _ORDERED_COMPARATORS:
        raise UnconvertableMarkerError(f"comparator {comparator!r} is not supported for {key}")
    if comparator in _EQUALITY_COMPARATORS:
        glob_prefix = _glob_prefix(literal)
        if glob_prefix is not None:
            return f"python!={literal}" if comparator == "!=" else f"python={glob_prefix}"
    return f"python{comparator}{format_version_literal(literal)}"


def _glob_prefix(literal: str) -> str | None:
    """`literal`'s conda-style prefix if it's a valid `"X.Y[.Z...].*"`
    PEP 440 prefix-match glob (docs/matchspec.md's Operator conversion:
    `==X.Y.*` becomes `=X.Y`, `!=X.Y.*` passes through unchanged), else
    `None`.
    """
    if not literal.endswith(".*"):
        return None
    prefix = literal.removesuffix(".*")
    try:
        return format_version(Version(prefix))
    except InvalidVersion:
        return None


def _virtual_package_condition(key: str, comparator: str, literal: str) -> str:
    if comparator != "==":
        raise UnconvertableMarkerError(
            f"comparator {comparator!r} is not supported for {key} "
            "(only '==' can map to a virtual package)"
        )
    mapping = _VIRTUAL_PACKAGE_KEYS[key]
    if literal not in mapping:
        raise UnconvertableMarkerError(
            f"{key} value {literal!r} has no known virtual package mapping"
        )
    return mapping[literal]
