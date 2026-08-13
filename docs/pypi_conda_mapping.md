# Decisions
There are no perfect answers for remapping names. The decisions taken are aimed at pragmatism, and are subject to be revisited as the tooling improves, and the community standardizes around things like PURL's

* Reroll will allow for a chain of mappings in order to support multiple providers
* A mapper receives the candidates accumulated by earlier mappers in the chain, and either returns a conda name directly (ending the chain immediately), or returns an array of candidates -- typically the input candidates plus new ones -- for the next mapper to consider
* If the chain ends without any mapper returning a conda name directly, resolution raises `UnresolvedCondaNameError`: candidates are never picked automatically. A chain that can end in ambiguity must include a final mapper that commits to one name
* Parselmouth will use a sqlite database to manage its entries
* To avoid Github rate limits, the bulk parselmouth data download will come from https://conda-mapping.prefix.dev
* The top-level API's will require passing in a mapping
* Package names larger than 64 characters after re-mapping raise `InvalidCondaNameError`

# Remapping package names from pypi to conda
Conda (and potentially each different conda channel) and pypi have different names for some python packages. In order to generate repodata, both the package name, and any of its dependencies must be converted to their conda equivalent. This is not an easy problem. There's no centralized registry of mappings, and mappings can be unique per conda-channel (e.g. conda-forge vs main). All of the known mapping tools today have issues, so there will regardless need to be a mechanism for users to provide custom mappings

# Existing tools
A great overview of the tooling ecosystem, how it works, and where the edge cases are can be found in this [grayskull github issue](https://github.com/conda/grayskull/issues/564)

* Grayskull
* conda-forge-bot
* conda-lock
* Parselmouth

Ben Mares has already done a good job of [summarizing](https://github.com/conda/grayskull/issues/564#issuecomment-2433467419) how the tools work, which I have quoted below:

> To be more specific, Grayskull's approach is basically to guess at the correct conda-forge name by querying anaconda.org with the original PyPI name and a few variations. There is a community-maintained table of exceptions for cases where the heuristic fails and someone bothers to add it. This works well surprisingly often, but fails on cases where it discovers the wrong package, like tzdata or ruamel-yaml, or when the heuristic fails to locate the package, like zope-interface.
> 
> Conda-lock's approach is to rely on bot-generated mappings with a separate table of exceptions. I looked at the bot code in detail a few years back, and I was impressed by the clever graph-theoretic heuristics to prioritize conflicting dependencies. However, the code is fairly old and tricky to understand, and when something goes wrong (conda-forge/conda-forge-bot#3031) it's not so easy to diagnose. The source of truth for the PyPI package is the recipe's source URL. This fails in cases like msal-extensions, redshift-connector, and jupyter-core since the source URL is GitHub instead of PyPI.
> 
> Parselmouth takes another approach and parses the metadata in the individual Conda archives. In particular, it uses the conda-forge-metadata package mentioned by @beckermr to grab the info.tar.gz and get the files list. Then it locates .dist-info/METADATA and .egg-info/PKG-INFO files and parses the PyPI name (and version) from the directory name. This works extremely well except for packages that alias other packages without including the source, like bs4. Also I find the source code fairly easy to read.

# Using Parselmouth
[Parselmouth](https://github.com/prefix-dev/parselmouth) has the raw data uploaded in the github source. However, this raw data needs to be transformed in order to be useful. There is a hosted API for the pypi mapping, such as this URL for requests:

https://conda-mapping.prefix.dev/pypi-to-conda-v1/conda-forge/requests.json

and there is also a CLI tool. The python CLI isn't published anywhere, and it is also aimed more at the pixi ecosystem. However, this command currently works to grab the same data as the URL above:
```sh
uvx --python 3.12 \
  --from "git+https://github.com/prefix-dev/parselmouth" \
  --with typer --with pydantic --with rich --with boto3 --with aioboto3 --with python-dotenv --with packaging --with "ruamel.yaml" --with tqdm \
  parselmouth explore-pypi --endpoint production --channel conda-forge --pypi-name requests --version 2.31.0 2>&1 | tail -30
```

The full data set needed for this mapping is controlled by the github actions in that repository. Namely, it downloads data from the conda-forge channel, and uses that to incrementally update data and generate the mappings and reverse mappings. The final data is uploaded to prefix.dev

This full dataset is located here: https://conda-mapping.prefix.dev/relations-v1/conda-forge/relations.jsonl.gz

## Parsing the bulk data
The bulk data can be converted into a one way pypi -> conda lookup with a full scan of the data. Thus, it makes sense to do a one-time parsing into a hashmap, and then performing lookups on the hash map itself. This data needs to be stored on disk, and then loaded. Storing the data in a sql database allows for future modifications of the algorithm without needing to throw away data.

## Updating the bulk dataset
A separate file: https://conda-mapping.prefix.dev/relations-v1/conda-forge/metadata.json tracks the state of the bulk data (so downloading the whole thing isn't necessary to check for an update). metadata.json contains
```json
{
  "format_version": "1.0",
  "channel": "conda-forge",
  "generated_at": "2026-08-05 12:39:41.976450+00:00",
  "total_relations": 1792379,
  "unique_conda_packages": 1758073,
  "unique_pypi_packages": 20409
}
```

and so the `generated_at` timestamp can be used to determine if out of date. Even more simply, the cloudflare bucket supports ETags, so sending the request header `'If-None-Match: etag-from-previous-response'` also results in a 304 if nothing has changed.

Given that generating the entire database from-scratch takes ~5 seconds, and that mappings can be removed (not just added), it makes sense to just create a whole brand new database when updates are needed. Atomic file operations can be used to swap out the database (and if anything is referencing the old sql database it will continue to work until closed)

## Parselmouth problems
Parselmouth uses the .dist-info generated from a conda-wheel in order to determine the corresponding pypi mapping. However, the logic for determining this pulls in _any_ .dist-info generated by the conda package. Some recipes add other packages, (even though this is most likely a recipe bug). This causes parselmouth to think that the package provides completely unrelated packages. This is filed as https://github.com/prefix-dev/parselmouth/issues/82 Detection has found that a heuristic of matching the version is a reasonable heuristic to prevent false matches, potentially along with a restricted levenshtein distance for similarity checks.

# Converting unknown names
Reroll, by intent, will be converting pypi packages which do not exist in a conda channel. Thus there is no existing mapping to use. In this case, we can generally use the normalized python name. However, conda package names are more restrictive than python names, so we need to perform further actions:
* Conda package names are limited to 64 characters

# Dealing with uncertainty
To provide for multiple sources to increase the confidence, the mapper API supports returning an array of candidates, along with their probabilities (and metadata about who is choosing the probabilities). This allows for composable algorithms which probe a source (potentially multiple times with different pieces of logic), and logic-only mappers which try to determine success from multiple sources.

A mapper is a function of `(name, candidates) -> name | candidates`:

* `candidates` in and out is an array of `Candidate`, not a set. The same conda name can appear multiple times, contributed independently by different sources -- deduplication and aggregation (e.g. "three sources independently guessed `python-tzdata`, so raise its combined confidence") is itself something a logic-only mapper does, not something the chain does for free.
* A `Candidate` has:
  * `conda_name` -- the candidate conda package name
  * `probability` -- a `float` in `[0.0, 1.0]`
  * `source` -- which underlying data source produced this guess, one of a fixed set: `parselmouth`, `grayskull`, `conda-lock`, or `other`
  * `mapper` -- the name of the specific mapper (function/instance) that added this candidate, distinct from `source`: two different mappers can both draw on the same `source` (e.g. two different heuristics both reading Parselmouth data) and remain distinguishable by `mapper`
* A mapper with no opinion returns `candidates` unchanged (there is no `None` "pass" sentinel in this design -- returning the input back out *is* passing).
* A mapper that has settled on one answer returns that name directly as a string, ending the chain immediately -- e.g. a hand-maintained static override table is authoritative by construction, so a hit returns a plain string rather than a `Candidate` with `probability=1.0`.
* If every mapper in the chain returns candidates (nothing ever collapses to a string), resolution raises `UnresolvedCondaNameError`: unlike the earlier design, there is no implicit "highest probability wins" tie-break and no fallback to the normalized PyPI name. A chain that can legitimately end ambiguous must end with a decision-making mapper that always commits to one name.
* The chain (`mappers`) itself must be non-empty for the same reason: with nobody to ask, there is no name to produce.
