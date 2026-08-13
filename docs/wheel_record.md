# Decisions
* Reroll(filename) without any other arguments will take a wheel and spit out the repodata
* The version will be CEP-26 transformed, not PEP-440 as in the spec
* The wheel record will contain an optional url field, but unset unless a user passes the data in
* The `noarch` key is omitted for subdir-specific repodata
* sha256, size, and url fields are optional, and must be passed in from the caller to be set
* Create reroll.stages to allow callers to break up the pipeline into individual parts (e.g. for working with database entries)
# Repodata fields
According to the CEP proposal, here are the required fields

- **`name`**: Taken from the wheel's METADATA `Name` field, normalized per [CEP 26][cep-26] and any name mappings aligned with the channel declared in `info.channel_relations.base` when present (see [Naming standard and channel mapping](#naming-standard-and-channel-mapping)).
- **`version`**: Taken from the wheel's METADATA `Version` field, normalized per PEP 440.
- **`build`**: Format `py{PY_MAJOR_VERSION}_{abi_tag}_{platform_tag}_{build_number}` (e.g., `py3_none_any_0`), conforming to CEP 26 build string conventions and the repodata record schema pattern `^([a-z0-9_.]+_)?[0-9]+$`. The build number MUST be at the end of the build string. The `{abi_tag}` and `{platform_tag}` are extracted from the wheel filename.
- **`build_number`**: As in regular conda packages. MUST be 0 initially. MAY be incremented for rebuilds.
- **`depends`**: Array including:
  - `python` dependency from `Requires-Python` (if present), converted to conda format
  - All `Requires-Dist` entries from METADATA, converted from PEP 440 to conda format per [Dependency conversion](#dependency-conversion)
  - Package names normalized to conda-style names per [CEP 26][cep-26]
- **`extra_depends`**: MAY be present. When present, MUST be an object mapping extra names to lists of dependency strings for optional groups, per [CEP 44][cep-44]. When absent or empty, the record declares no optional groups beyond `depends`.
- **`subdir`**: MUST be `"noarch"`.
- **`noarch`**: MUST be `"python"`.
- **`fn`**: MUST be the wheel filename (`.whl`), as in standard conda repodata records (for example `requests-2.32.5-py3-none-any.whl`).
- **`sha256`**, **`size`**: Standard repodata fields for the wheel file.

## Name
The most important thing here is that this is a conda name. It needs to be in conda-syntax, and if there is a different pypi vs conda name, the conda name should be chosen. Our implementation is covered in detail in [pypi_conda_mapping.md](./pypi_conda_mapping.md)

## Version
The CEP proposal claims this should be a PEP-440 identifier, but we are going to emit matchspec compatible versions, under the belief the proposal is mistaken. Any conda tooling installing the wheel or its dependencies is going to have matchspec-based parsers of the version, not PEP-440

## build
This is another case where the CEP is laser-focused on noarch builds. Instead of `py{PY_MAJOR_VERSION}` we will just choose to adopt the `python_tag` from the wheel filename for that segment

## build_number
A wheel can have their own build tag, but for now our build numbers will always be `0`. Adjusting the build_number can be a future followup.

## depends, extra_depends
This is covered in detail in [wheel_to_conda_dependencies.md](./wheel_to_conda_dependencies.md)

## subdir
This must always match the repodata that the record is being distributed on. So anything that was generated for `noarch` gets that tag, `linux-64` gets that tag and so on.

## noarch
This only makes sense for wheels that have the subdir noarch. If an installer actually allowed a different subdir + noarch: python, it would put the files in the wrong location. Therefore, we will deviate from the spec, and include `noarch: python` for noarch wheels, and omit this record for subdir-specific fields

## fn
Just the wheel filename, as is

## sha256, size
These fields are present in the PYPI simple index data, and thus easily accessible without having the actual wheel. In reality, these fields are both optional, but desirable

## URL
This field was removed from the proposed CEP. For now, we will allow it as an optional parameter, but not hardcode it to anything. Instead, it can be user-set

## License
This field is not declared by the CEP, however it is an optional part of repodata.json. We will emit the SPDX license if we have it, or omit the key
