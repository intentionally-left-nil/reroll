# Decisions
There are no perfect answers for remapping names. The decisions taken are aimed at pragmatism, and are subject to be revisited as the tooling improves, and the community standardizes around things like PURL's

* Reroll will allow for a chain of mappings - where the first mapping to produce a transformation wins
* Reroll will include built-in support for Parselmouth (both bulk mode and one-off http)
* Bulk-mode processing will rely on a sqlite database for storage and updating
* To avoid Github rate limits, the bulk parselmouth data download will come from https://conda-mapping.prefix.dev
* The top-level API's will require passing in a mapping
* Package names larger than 64 characters after re-mapping are rejected

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
The bulk data can be converted into a one way pypi -> conda lookup with a full scan of the data. Thus, it makes sense to do a one-time parsing into a hashmap, and then performing lookups on the hash map itself. This data needs to be stored on disk, and then loaded. This could be a JSON file, a SQL database, or some other serialization method. The sql implementation is straightforward:
```py
import json, time, sqlite3

con = sqlite3.connect("relations.sqlite")
cur = con.cursor()
cur.execute("CREATE TABLE relations (pypi_name TEXT, pypi_version TEXT, conda_name TEXT)")
batch = []
with open("relations.jsonl") as f:
    for line in f:
        r = json.loads(line)
        batch.append((r["pypi_name"], r["pypi_version"], r["conda_name"]))
        if len(batch) >= 50000:
            cur.executemany("INSERT INTO relations VALUES (?,?,?)", batch)
            batch = []
if batch:
    cur.executemany("INSERT INTO relations VALUES (?,?,?)", batch)

cur.execute("CREATE INDEX idx_pypi ON relations(pypi_name, pypi_version)")
con.commit()

# Example usage
cur.execute(
    "SELECT DISTINCT conda_name FROM relations WHERE pypi_name=? AND pypi_version=?",
    ("requests", "2.31.0"),
)
print(cur.fetchall())
con.close()
```

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

# Converting unknown names
Reroll, by intent, will be converting pypi packages which do not exist in a conda channel. Thus there is no existing mapping to use. In this case, we can generally use the normalized python name. However, conda package names are more restrictive than python names, so we need to perform further actions:
* Conda package names are limited to 64 characters
