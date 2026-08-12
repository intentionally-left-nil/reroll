# Matchspec conversion

This document covers the parts of converting a pypi-ecosystem string (a
dependency's name, operator, version, extras, or a PEP 508 marker) into a
conda [MatchSpec (CEP-29)](https://conda.org/learn/ceps/cep-0029/).

# Decisions
* Per CEP-29, ~= is deprecrecated. Any instance of  ~= should be expanded to the appropriate range specifier
* Pre-release tags (dev, devN, alpha, beta, rc and their short-forms) are not allowed for filenames or dependencies, unless specifically opted into (via a user-controllable --allow-pre flag)
* Matchspec cannot easily handle virtual packages, especially `!=`. Therefore, these fields will fail at the matchspec level. Callers should use e.g. subdir splitting to avoid failures

# Name
The name portion of a MatchSpec starts from the pypi name, normalized per
[wheel_filename.md's Package name](./wheel_filename.md#package-name), and is then mapped to a
channel-specific conda name using the mapping chain described in
[pypi_conda_mapping.md](./pypi_conda_mapping.md).

# Operator conversion
Most operators can be passed as-is - conda's matchspec covers the same comparison operators PEP-440 does (`==`, `!=`, `<`, `<=`, `>`, `>=`, `~=`). Three cases need more care: `~=`, PEP-440 prefix matching, and `===`

First, the `~=` syntax historically was not well-supported via conda. This was properly fixed in [Conda 4.9](https://docs.conda.io/projects/conda/en/latest/release-notes.html#id307), however the syntax is considered deprecated in CEP-29
> Semver-like comparisons, expressed as a version literal prefixed by the ~= string, MUST be interpreted as greater than or equal to the version literal while also matching a fuzzy equality test for the version literal sans its last segment (e.g. ~=0.5.3 expands to >=0.5.3,0.5.*). This operator is considered deprecated, and its expanded alternative SHOULD be used instead
Therefore, the correct conversion of `~=3.13.2` is (`>=3.13.2,<3.14.0a0`). The `0a0` suffix check is important because otherwise 3.14 pre-releases would be considered part of the range.

Next, `==` with a glob is a grey area: CEP-29's rationale says mixing globs with the other operators is disallowed. To avoid confusion, prefix specifiers should be rewritten to the canonical fuzzy form, so `==X.Y.*` becomes `=X.Y`. Prefix *exclusion* needs no rewrite, so `!=X.Y.*` passes through unchanged.

Lastly, triple equals `===` means different things for PyPI vs Conda. For pypi, triple equals is almost like an escape hatch. It means "do an exact string comparison, don't worry about any details about the format". This is to handle things like local tags, url's or other oddities.

The `===` is very rare in pypi, so in some regards it doesn't matter too much what we do with it. However, by default conda's matchspec will parse `===` and treat it as `==`, so we will continue observing the same behavior and emit `==`.

# Version conversion
Pip supports more tweaks on the version than conda natively supports. This includes:
* local tags - `package 1.0.0+awesome`
* direct URL's - `package @ https://example.com`
* Pre-releases - `package 1.0.0rc1`

In addition, the Conda version syntax, specified by [CEP-33](https://conda.org/learn/ceps/cep-0033) is different than PEP-440. For example: `1.0.0rc1` for pypi is most closely translated as `1.0.0.rc1` (note the period) for conda. Another example is that `1.0-1` PEP440 shorthand for `1.0.post1`.

Some of these we can dismiss out of hand. Dependencies with direct URL's are to be reject out-of-hand. It shouldn't occur and it's natural to emit an log item and stop processing the wheel when this happens. Presumably the author is deliberately trying to access another registry,
but that wouldn't know anything about conda and also it's a security risk. Rejecting the entire wheel, rather than silently stripping the URL brings visibility to the problem.

Local tags are by-design not intended to be uploaded to a repository. PyPI will not allow a package with a local tag to be uploaded. That also means that if a package has a `Requires-Dist: package 1.0.0+awesome`, this would never be able to resolve from a wheel on PyPI.
Therefore, we can also reject local tags.

## Version conversion algorithm
1. start with a packaging.version.Version object.
2. If the object contains local tags, remove it.
3. Append the epoch (if it exists)
4. Append the release segments, joined by a `.`
5. If there is a pre-release, emit `.{letter}{num}` from v.pre
6. If there is a post-release, emit `.post{v.post}`
7. If there is a dev release, emit `.dev{v.dev}`

## Pre-release
Pre-releases are different, in that they are valid specifications in both pypi and conda. The problem is that pip will not install pre-releases without `--pre` or a specific requirement for an exact pre-release (alpha/beta/rc) version. So `pip install mypackage==1.0.0` will not pick up `package 1.0.0rc1` even if present in the registry.

Conda doesn't have this support, but the pre-release would be a lower-priority match. So, given a conda version of `1.0.0` and `1.0.0.rc2` with a specifier match of `mypackage==1` or `mypackage==1.0.0` it would prefer the non-rc candidate. The only time an RC candidate would be picked up would be when the production version does not exist. Unfortunately... the whole point of a pre-release is exactly that (to release something before the final version exists). The conda solution is to add labels (which essentially act as micro-channels). So the default label would have the main release, and a `dev` label would have the prerelease.

Even in this label case, the label is not part of the dependency specification. There's no conda way to specify `mypackage/label/dev::mypackage==1.0.0.rc2` in order to get an rc package.

Therefore, putting all this together leads to a strong reason to reject alpha suffixes in releases. It would probably be okay to strip them, under the assumption that the code in a final release trumps a release candidate. However, for the sake of clarity, clear failure cases (rather than hidden changes), it's better to initially reject such dependencies, see if it actually occurs, and adjust from there.

To mimic the concept of labels, a repodata author might intend for a certain channel to contain -alpha, -beta, -rc packages in it. In this case an `allow_pre` flag can toggle this behavior
One final note is that `allow_pre` will apply equally to both package versions, as well as version dependencies. This keeps things consistent (channels can either have pre-release packages and rely on other pre-release packages, or they can't)

## Post-release
Post releases are fine, however, because they are intended to take priority over an existing wheel. So, any pypi dependency like `mypackage 1.0.0.post1` is accepted, (and the package with that release would also be accepted by the filename command)

# Extras
See [wheel_to_conda_dependencies.md's Dealing with extras](./wheel_to_conda_dependencies.md#dealing-with-extras) for what extras are and how the set of extras a package has is calculated. This section covers only how an extra becomes part of a MatchSpec string.

## Extras name normalization
`Provides-Extra` must match the following specifier: `^[a-z0-9]+(-[a-z0-9]+)*$`. In the pypi ecosystem, this is normalized by applying the same normalization `re.sub(r"[-_.]+", "-", name).lower()` mechanism used for other names.
The conda CEP for extras has a slightly different regex: `[a-z0-9_.+-]{1,64}`. It doesn't restrict the starting character, it allows `_`, but it also restricts the length to 64 characters. We can therefore create a compliant extras normalizer by running: `canonicalize_name` and then rejecting any extra > 64 characters.

## Requiring a dependency with an extra
On the flip side, what happens if another package includes `Requires-Dist: fastapi[all]` in their requirements?
This needs to be translated into the conda-equivalent matchspec `fastapi[extras=[all]]`. However, matchspec does not accept `fastapi[all]` (since the [] notation is already overloaded in conda-land). So, instead, the extra markers need to be extracted, f'extras=[','.join(extracted)]' needs to be called (after normalizing the names of course)

# Conditional markers
[Environment markers](https://peps.python.org/pep-0508) allow for installation of a dependency to be dependent on certain properties of the system during install time. Previously this was a source of headaches trying to automatically convert pip dependencies into conda dependencies. However, conda now supports conditional dependencies via [CEP-43](https://conda.org/learn/ceps/cep-0043)

Here's what each environment marker variable means, which gives the [Marker to matchspec conversion](#marker-to-matchspec-conversion) table below the context for which ones can convert to a MatchSpec, and which can't.

## python_version
This is the major.minor (not patch) version (`3.13`, not `3.13.X`).

## python_full_version / implementation_version
`python_full_version` is the same value as `python_version`, but also including the patch (3.13.0). Many actual uses of this in wheel markers are not patch-specific, but should be using python_version instead. Perhaps the legitimate case would be if something were specifically targeting a beta patch level like `3.13.2b`

`implementation_version` has a complicated algorithm derived from [sys.implementation](https://docs.python.org/3/library/sys.html#sys.implementation). However, for CPython, this value is always equal to the same value as python_full_version, even for beta or free threading variants. Thus, whatever applies to python_full_version applies to implementation_version.

See [Reducing python_full_version / implementation_version to python_version](#reducing-python_full_version--implementation_version-to-python_version) below for how a constraint on either of these is checked against a known python_version to decide whether it can be represented at all.

## platform_system
Corresponds to [platform.system()](https://docs.python.org/3/library/platform.html#platform.system)
> Returns the system/OS name, such as 'Linux', 'Darwin', 'Java', 'Windows'. An empty string is returned if the value cannot be determined.

## sys_platform
A close relative of platform_system. [sys.platform](https://docs.python.org/3/library/sys.html#sys.platform)

## os_name
[os.name](https://docs.python.org/3/library/os.html#os.name) can either be `posix` (linux and mac), or `nt` (windows)

## platform_machine
the docs [platform.machine](https://docs.python.org/3/library/platform.html#platform.machine) don't give a concrete list
but we can provide the known values that we care about as: 
| Value | Description |
|---|---|
| `x86_64` | macOS Intel or Linux 64 bit |
| `arm64` | macOS Apple Silicon (M1/M2/M3/etc.) |
| `aarch64` | Linux 64-bit ARM |
| `AMD64` | Windows 64-bit x86/AMD64 |
| `ARM64` | Windows 64-bit ARM |

Platform machine is especially complicated because there isn't an exact virtual package that maps this. We can kind of use `__archspec` but that value is not specific about operating systems, but rather CPU features. So supported archspecs of `x86_64` and `aarch64` can restrict the CPU architecture, but they can't restrict the operating system. This is why `platform_machine` has no entry in the conversion table below.

## platform_python_implementation / implementation_name
`platform_python_implementation` corresponds to the interpreter, and `implementation_name` is the same thing lowercased (`cpython`). Reroll fixes both of these to a known CPython value in its base environment (see [wheel_to_conda_dependencies.md's Noarch base environment](./wheel_to_conda_dependencies.md#noarch-base-environment)), so any marker referencing them fully evaluates away, and neither has an entry in the conversion table below.

## platform_release
Returns the os release (like 10 for Windows 10). There's no mapping for this value, but it is not widely used in the pypi ecosystem.

## platform_version
This is the full build string, and also is not something that we can map here

# Marker to matchspec conversion
There is no existing tooling to take a python marker and convert it to a matchspec-compatible string. We can use py-rattler to confirm the string is valid once the transformation is complete. Thus, we need to define the mapping of marker variables ourselves. Note: This section covers the matchspec conversion as an isolated question. Due to the difficulties discovered here and otherwise, the solution of mapping e.g. `platform_system` to matchspec was dropped. The full table is presented for completeness, but some pieces need not be implemented due to other restrictions preventing the marker key from being present as input

| marker key | matchspec |
|---|---|
| `python_version == "X.Y"` | `python>=X.Y.0a0,<X.(Y+1).0a0` |
| `python_version != "X.Y"` | `python!=X.Y.*` |
| `python_version >= "X.Y"` | `python>=X.Y.0a0` |
| `python_version > "X.Y"` | `python>=X.(Y+1).0a0` |
| `python_version <= "X.Y"` | `python<X.(Y+1).0a0` |
| `python_version < "X.Y"` | `python<X.Y.0a0` |
| `python_full_version` / `implementation_version` `== "X.Y.Z"` | `python==X.Y.Z` |
| `python_full_version` / `implementation_version` `!= "X.Y.Z"` | `python!=X.Y.Z` |
| `python_full_version` / `implementation_version` `<op> "X.Y.Z"` (`>=`, `<=`, `>`, `<`) | `python<op>X.Y.Z` (passthrough) |
| `platform_system == "Linux"` / `sys_platform == "linux"` | `__linux` |
| `platform_system == "Darwin"` / `sys_platform == "darwin"` | `__osx` |
| `platform_system == "Windows"` / `sys_platform == "win32"` | `__win` |
| `os_name == "posix"` | `__unix` |
| `os_name == "nt"` | `__win` |

`in/not in` is unsupported. See [Dealing with in and not in](#dealing-with-in-and-not-in) for details

`python_version` is truncated to major.minor, but conda compares the full `python` version, so the ordered comparisons cannot be passed through: `python_version <= "3.9"` as `python<=3.9` would exclude 3.9.1, and `python_version > "3.9"` as `python>3.9` would wrongly include it. Every boundary is anchored at `.0a0` so that a pre-release of the boundary minor lands on the same side of the comparison that the marker would put it on (`python_version` of `3.9.0rc1` is `3.9`).

## Reducing python_full_version / implementation_version to python_version
`python_full_version` and `implementation_version` include the patch (`3.13.0`), where `python_version` is major.minor only (`3.13`). Assuming that the known version of python is restricted to a specific minor version, ordered comparisons (`>=`, `>`, `<`, `<=`) can get acceptable coverage by checking two values (3.minor.0), and (3.minor.100). If the resolution is the same in both cases, we can essentially treat this as `python_version`, and otherwise we can know not to emulate this value.

Equality and containment (`==`, `!=`, `in`, `not in`) cannot be satisfied with point-checks. It's too much work to enumerate all possibilities of patches, and to test every single one, so we will just not try to reduce this requirement.

Thus a fully comprehensive check of python_full_version (and, identically, implementation_version) is as follows:

Given a known python_version 3.XX:
1. Parse the tree for `python_full_version` used with (`==`, `!=` or `in/not in`) operator. If it exists, do not use python_full_version
2. Try solving for `python_full_version` set to `3.XX.0`
3. If `python_full_version` is still set after step 1, perform another solve with `3.XX.100` - If the tree is identical, then use it. Otherwise, do not set `python_full_version`

## Dealing with != with matchspec and virtual packages
Consider the two pypi markers: `os_name == "nt"` and `os_name != "nt"`
For the first case, the equivalent matchspec is `__win` (just a bare win, no qualifier).
What about the negation expression? `not __win` is not valid matchspec syntax. `__win != ???` doesn't work, either. First off, a virtual package doesn't exactly have a public version or build, but even otherwise this is the wrong question. `os_name != nt` means accept any OS besides windows. However, `__win != 1.0` (or similar) means any version of `__win` where that "package" has the version of anything besides 1.0.

What we really want is some virtual package for __os_name, so we could write a matchspec `__os_name != 'win'`
Since this doesn't exist, and bare negation (`not __win`, `!__win`) is not part of matchspec, we need a different workaround.

If we changed our mind and decided in the future to allow `os_name` as a matchspec target, this is an algorithm we could apply:
Convert `platform_system != Linux` into `__osx or __win`. This can be accomplished by having a mapping table of `platform_system` values to their corresponding virtual package names. Then, when `!= is seen` take all keys except for the one being negated, and expand the node into a collection of OR nodes with the other mapped values.

For `os_name` the pattern is the same except the mapping would be `{"posix": "__unix", "nt": "__win"}`

## Dealing with in and not in

in/not in perform substring matches, instead of exact equality with `==`. Since it's a substring, order matters `apple in apple_pie` is not the same as `apple_pie in apple`.
Conda matchspec doesn't provide a clean mapping to matching this behavior. On first glance, one could try to model `python_version in "3.11"` with the matchspec `packageA[when="python *3.11*"]` 
However, rattler-py doesn't support a leading `*` for the version field (this may be a rattler bug) as `VersionSpec('*3.1*')` throws an exception. This bug has been filed as https://github.com/conda/rattler/issues/2679

Until this is fixed, we will NOT support `in/not in` with matchspec. Instead, the conversion code should raise an error if that is present.
Potentially this could be worked around for python_version by means of exhaustive expansion of the possibilties, pre-computing the actual matches, and then selecting those. However that's a lot of work and so the initial decision is just to not allow `in/not in`
