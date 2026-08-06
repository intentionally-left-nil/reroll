Parselmouth's data has lots of reliability issues. It detects .dist-info files that map to dependencies of packages, not the package itself. Additionally, parselmouth will try to map a specific pypi version to a conda package, independently of other versions. If the conda channel is missing a matching version (or if there are versions before conda started building packages), parselmouth may wind up choosing other similar-sounding package names.


To work around this, we will use the raw parselmouth data, and apply our own heuristics in order to improve the reliability of the data


# Database layout
- `parselmouth_version` - A one-row table containing the metadata of the underlying parselmouth data. The etag is used to know whether to fetch new data or not
- `pypi_claim` - This is the underlying data from parselmouth. It contains a pypi name and version, mapped to a conda package and version. There's also a small `version_state` enum which precomputes whether the two version identifiers are equal, similar, or different. `pypi_claim` gets a multi-column primary key (conda name, conda version, pypi name, pypi version) in order to support left-prefix based lookups (B-trees are cool)
- `pypi_conda_mapping` aggregates parselmouth's data to be package to package specific. The primary key (pypi_name, conda_name) enforces that each mapping has exactly one row. Each row contains our custom heuristics to look over the corpus of parselmouth data.

# Heuristic logic
* Detect whether a conda package is claimed by similarly spelled pypi packages
* Detect whether multiple versions of the same pypi package map to the same conda package
* Detect whether the pypi name and conda name are similar (after stripping common conda naming conventions)
* Attempt to detect whether the pypi package is mistakenly vendored by seeing if a different pypi name already agrees on the same conda release


# Working around bugs
- The file `conda-package-handling-1.3.0-py27_0.tar.bz2` is currently mapped to the conda name of `mock` which is a parselmouth bug. This is worked around by dropping rows where the conda name doesn't match the filename.
-
