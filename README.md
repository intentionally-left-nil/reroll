# Introduction
Reroll is an exploratory codebase which generates conda repodata.json-like entries for a wheel. It's uploaded as `py-reroll` on pypi.org

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
