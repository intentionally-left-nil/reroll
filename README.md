# Introduction
Reroll is an exploratory codebase which generates conda repodata.json-like entries for a wheel. It's uploaded as `py-reroll` on pypi.org. Reroll has a **99.9%** conversion success rate [[stats](#stats)]

```py
from reroll import reroll

reroll("path-to-wheel.whl")
```

Reroll's principles are:
1. Correctness - A rerolled package should faithfully translate name, version, architecture, extras and more between the pypi and conda ecosystem without loss-of-specificity
2. Ecosystem-wide compatibility - Reroll is designed towards broadly supporting the pypi ecosystem. That means not just supporting simple regex-based solutions or other shortcuts, but truly parsing and understanding the wheel requirements
3. Broadly usable - Reroll is modular and designed for integration into larger projects. It doesn't have any conda dependencies, so it can be installed in a normal python environment. Its API surface is compatible for conversion of large scale pypi registries - neither the wheel or the full metadata is required (just store the parsed fields reroll cares about)

In order to achieve these goals, reroll is deliberately not fully-compliant with the [proposed wheel repodata CEP](https://github.com/conda/ceps/pull/145), although it aims to be directionally compatible. the -145 pull currently does not support arch-specific repodata, and that is required to achieve broad, accurate repodata for all wheels (even `*-none-any` wheels with arch-specific dependencies)

# How it works
At its core, reroll is based around two technologies: [markerpry](https://github.com/intentionally-left-nil/markerpry) and the [pep508-to-matchspec](./src/reroll/pep508_to_matchspec.py) converter in this repository. Creating accurate repodata largely involves accurate mapping between conda's subdir-based architecture for platform-specific packages, and pypi's wheel name and conditional dependencies system. Markerpry helps bridge the gap by partially solving a packaging.requirements.Marker, in order to simplify it under certain conditions. For example, if you were creating repodata for linux-64, you would know that your `os_name` was `posix`. By partially solving, we can eliminate conditional markers that depend on os_name, and collapse them to true/false. The remaining tree is then (hopefully) expressable in conda's new conditional dependency syntax.

Additionally, this repository hopes to contribute a useful tool of converting a PEP-508 style requirement to a matchspec requirement. There are lots of subleties around versions (with .rc1 for example), ranges, varying support for conditional expressions and more.

# Batch usage
Reroll is designed to allow users to have a separate wheel data ingestion stage, separate from repodata emission. Reroll only needs the filename and a JSON-serializable representation of a subset of the wheel's metadata.
The [reroll-data](https://github.com/intentionally-left-nil/reroll-data) repository contains one such mechanism for saving the entire pypi metadata on a local sqlite database.

In such a scenario, the first step is data ingestion. Reroll does not care, nor does it provide any mechanism to download data from a pip index url. Use reroll-data, or other tools to accomplish it.
However, once you _do_ have the filename and the METADATA file, simply call:
```py
from reroll.stages import extract_metadata_file, parse_metadata

wheel_metadata_contents: str = "..."
metadata = parse_metadata(wheel_metadata_contents)
data_json = metadata.model_dump_json()  # Save this to a database, along with the filename
```

And later, when you're ready to generate repodata:
```py
from reroll import WheelMetadata
from reroll.stages import get_wheel_records

data_json = "..."  # Load from your database
wheel_filename = "..."  # Load from your database
metadata = WheelMetadata.model_validate_json(data_json)
records = get_wheel_records(metadata, filename)
```

# PEP to Matchspec helper
If you ever have a PEP-440 style requirement and want to turn it into a matchspec, `to_matchspec` has you covered. It will convert the string if it can, otherwise an error will be raised if there's no compatible translation to matchspec
```py
>>> from reroll import to_matchspec
>>> to_matchspec('packageA ; python_version < "3.9"')
'packagea[when="python<3.9.0a0"]'
```

# Stats
* Reroll can successfully convert 99.9% of supported wheels into repodata
* Reroll considers 86.1% of all wheels on pypi as supported

## What constitutes a supported wheel?
Reroll supports 86.1% of all wheels on pypi. As an aspiration, reroll aims to support any wheel you could run by taking a conda environment from the modern supported platforms (linux-64, linux-aarch4, osx-64, osx-arm64, win-64, win-arm64) and then pip or uv would generally dry-run install the wheel (assuming its dependencies are met).

Since this would involve downloading and installing a lot of (perhaps dodgy) wheels, instead, reroll has the following rules that approximate this level of support
* The wheel must be target python 3.4 and up
* It must support CPython (not pypy or other interpreters)
* It must have a valid wheel filename, and metadata
* Older metadata (conforming to previous PEP specs or otherwise) are allowed if they are repairable by uv repair mechanisms
* The metadata must be syntactically valid and consistent with other metadata, as well as the filename
* Wheels targeting linux must be modern, manylinux-based.

When reroll encounters an unsupported wheel, it raises a RerollScopeError for reroll's policy decisions (python 3.4, platform, manylinux etc), or a RerollInvalidWheel error for parsing or semantic problems with the metadata. Reroll considers any parsing error fatal and will not skip or suppress partial failures.

## Current reroll failures
Reroll supports 99.9% of supported wheels. For the remaining 0.11%, why does reroll fail? Half of all failures are due to local build tags (like `+cpu`) which conda does not have direct support for. The other half are a long tail of issues Reroll would never directly support (too long filenames, url's in dependencies and edge cases which need further exploration (~= with python_version).

| sub category | description | % of all supported wheels |
|---|---|---|
| `UnconvertableRequirementError` | PEP 440 local version label (torch/ipex-llm/vllm-cpu `+cpu`/`+xpu` hardware variants) | 0.061% |
| `InvalidCondaNameError` | name >64 chars (SEO-spam PyPI project names) | 0.012% |
| `UnconvertableRequirementError` | illegal conda extra name (CEP-29) | 0.006% |
| `UnconvertableMarkerError` | unsupported comparator (`~=`) for `python_version(_full)` | 0.006% |
| `UnconvertableMarkerError` | `python_version` literal not a valid version (e.g. `'2.7.*'`, `'3.x'`, `'dev'`) | 0.006% |
| `UnconvertableMarkerError` | unpermitted key (`platform_release`/`_version`/`_machine`/`sys_platform`/`platform_python_implementation`) | 0.005% |
| `UnconvertableRequirementError` | `amulet-compiler-version` numeric-overflow version | 0.004% |
| `UnconvertableMarkerError` | `python_version` literal not major.minor precision | 0.002% |
| `UnconvertableMarkerError` | `in`/`not in` marker unsupported (no matchspec equivalent to set membership) | 0.002% |
| `UnconvertablePythonVersionEqualityError` | `python_version ==`/`!=` a non-major.minor literal | 0.001% |
| `UnconvertableRequirementError` | direct URL reference | 0.001% |
| `UnresolvedCondaNameError` | 4 unresolved dep names (`alchemiscale`, `oasis`, `nr-config`, `sktools`) | 0.000% |
| `InvalidCondaNameError` | homoglyph/Cyrillic lookalike name (`taаmo`) | 0.000% |

## Unsupported wheels breakdown
Of the 14% of wheels that markerpry does not consider supported, they fail for the following reasons:
Many of these reasons are unresolvable problems with the wheels themselves, and a few categories are particular reroll restrictions to prioritize initial support of modern systems.

| sub category | description | % of all wheels |
|---|---|---|
| `UnsupportedPlatformError` | Wheel's platform tag is out of policy scope (e.g. musllinux, 32-bit Windows/Linux, iOS/Android, wasm, non-manylinux glibc, or an unsupported CPU arch) | 9.605% |
| `InvalidInterpreterTagError` | Wheel filename has a malformed/unrecognized interpreter tag (e.g. PyPy `pp37`) | 2.228% |
| `UnsupportedInterpreterError` | Wheel targets a non-CPython interpreter major (e.g. `py2`, no CPython tag at all) | 1.472% |
| `PythonRangeMismatchError` | Wheel filename's implied CPython version range doesn't intersect the sdist's declared `Requires-Python` | 0.244% |
| `InvalidPythonRequirementRangeError` | `Requires-Python` is not a contiguous Python 3.x minor-version range (e.g. `==2.7`) | 0.204% |
| `InvalidRequirementError` | A `Requires-Dist` entry fails to parse as a PEP 508 requirement string | 0.059% |
| `UnsupportedInterpreterVersionError` | Wheel targets CPython < 3.4 (below reroll's minimum supported version) | 0.044% |
| `MetadataFilenameMismatchError` | METADATA's Name/Version disagrees with the wheel filename's Name/Version | 0.011% |
| `InvalidVersionSpecifierError` | A version specifier fails to parse as PEP 440 (e.g. `>-3.6`) | 0.008% |
| `InvalidAbiTagError` | Wheel filename has a malformed/unrecognized ABI tag | 0.006% |
| `InvalidFilenameError` | Wheel filename doesn't parse at all (wrong number of `-`-separated parts, etc.) | 0.002% |
| `InvalidMetadataError` | METADATA fails pydantic validation (e.g. an unparseable `Version:` field) | 0.000% |

# Acknowledgements
I'm so grateful to have learned much about this topic from @jjhelmus. Together we created the original prototype for this concept several years ago, before conda had any support for conditional dependencies, optional dependencies (extras), or .whl support in repodata.json. The reroll codebase in a large sense is just an outgrowth of the original [conda-whl-channel](https://github.com/anaconda/conda-whl-channel) prototype to explore repodata conversion at scale.

This approach wouldn't be possible without conditional or optional support in conda, so cheers to everyone who pushed those CEP's over the line.

Reroll takes advantage of other community tooling to parse, translate, and validate pieces of data, including:
* Rattler
* uv
* conda-lock
* grayskull
* parselmouth

The usage of these tools for this project has uncovered corner cases (or otherwise) in some of these tools, and I appreciate folks taking a look at the bug reports and PR's
