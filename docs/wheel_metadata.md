# Decisions
* Callers are responsible for validating the METADATA checksums
* Supported inputs must include: A pydantic object with just the important fields (to support data in databases), or the METADATA contents as a string
* The Name and version must match the filename
* Use metadata.Metadata() to parse the metadata file, but use `validate=False`. We will self-type-check the fields we care about, and not worry about exceptions in other fields
* Wheels with PEP-345 style names are rejected

# Background
The .dist-info/METADATA file corresponds to the following spec: https://packaging.python.org/en/latest/specifications/core-metadata/

There are several versions of the METADATA spec, so we will need to be able to handle all of the versions supporting 3.x wheels.

## Important fields
- Name - Allowed regex has changed over time, but it has to match the wheel name anyways
- Version - Again must match the wheel version
- License-Expression - SPDX license, added 2024 in [PEP-639](https://peps.python.org/pep-0639/)
- License - Free text license, used to be the standard before License-Expression
- Classifier License - Deprecated as of PEP-639
- Requires-Python - Intersection with the wheel filename to determine feasibility
- Requires-Dist - Environment markers added later in [PEP-566](https://peps.python.org/pep-0566/)
- Provides-Extra - Defines extra packages that someone else can request with [] syntax like `fastapi[cli]`. Also added in PEP-566 

## Encoding challenges
> Whenever metadata is serialised to a byte stream (for example, to save to a file), strings must be serialised using the UTF-8 encoding.

In practice, this means we need to support:
Embedded null characters. Windows line endings, Mac line endings, Unix line endings.
When parsing the fields, (e.g. for dependencies) we need to reject files which have non-ascii entries for the fields we care about

In the actual pypi corpus, embedded nulls and different line endings have been observed.

## File format challenges
The email headers-ness of the METADATA causes potential parsing issues. All fields are essentially key-value pairs, but it's a flat list. Therefore, keys/headers can be repeated multiple times. You could have multiple Name fields, multiple Requires-Python fields. Sometimes this is a good thing. For example, this is how you create an array, by repeating e.g. `Requires-Dist` multiple times for each key.

## Overlapping with filename data
The name, version, and python requirements are also specified in the wheel filename. Pip doesn't validate the METADATA matches, (and also it uses the .dist-info/ folder name for name checking), but uv does. UV has a flag [UV_SKIP_WHEEL_FILENAME_CHECK](https://github.com/astral-sh/uv/issues/8082) to allow users to disable the match. However by default, uv will refuse to install a wheel where the METADATA doesn't match the name and version of the filename.

On the python side, both uv and pip support Requires-Python. This can produce an unsolvable wheel, as there is a two step process. First, the other package managers will look at the filename to determine if a wheel is even a candidate. Then, for all candidates, the METADATA is extracted, and all of the requirements are checked. So, it's not out of spec for Requires-Python to be incompatible with the wheel filename (like `mypackage-py312-py312-any` paired with `Requires-Python: = 3.13`), but it will cause the solver to fail. If there's a partial overlap, then the intersection will be solvable.

## Obsoletes-Dist
This is defined in the METADATA spec as something which would uninstall the specified package in the same environment. However, it is not implemented by any packaging manager. For example, here's a [declined request](https://github.com/pypa/pip/issues/12196) to add it to pip. Thus we can safely ignore this field

## Metadata-Version
Technically, the spec requires failing on metadata versions we don't expect, but in practice new metadata fields have been additive, so we will just ignore this field entirely. If underlying tools parse and detect the version, that's okay, but it's not a requirement in either direction

## Requires-Dist environment markers
PEP-566 changed the format for Requires-Dist to formally encode environment markers. Beforehand, there was [PEP-508](https://peps.python.org/pep-0508/) which defined an earlier spec.

For example `Requires-Dist: pywin32 (>1.0); sys.platform == 'win32'` breaks PEP-566 formatting because this should be `platform_system` as the marker, and there shouldn't be parenthesis on (>1.0)

Luckily, the packaging tool already handles both standards and translates the old values as new-style markers:
```py
>>> from packaging.requirements import Requirement
# New style
>>> req = Requirement('pywin32 (>=1.0); sys_platform == "win32"')
>>> req.marker
<Marker('sys_platform == "win32"')>
# Old style, upgraded to have new style values
>>> req2 = Requirement("pywin32 (>1.0); sys.platform == 'win32'")
>>> req2.marker
<Marker('sys_platform == "win32"')>
```

So in practice we don't need to worry about this - if `packaging.Requirement` can parse the value we're in a good spot

## Packaging.Metadata quirks
`from packaging.metadata import Metadata, parse_email` encodes two different mechanisms of digesting a wheel's metadata. You could skip the `packaging` module entirely and use `email.message_from_string(metadata_text)` from the email module instead.

This is again a case of back-compat and how flexible to be with the requirements.

[parse_email](https://github.com/pypa/packaging/blob/main/src/packaging/metadata.py#L360) takes a relaxed posture. It handles some encoding inconsistencies, and otherwise handles some of the fields we don't care about (like keywords and project url's) and it normalizes the casing, but otherwise leaves things pretty-much alone

[Metadata(validate=true/false)](https://github.com/pypa/packaging/blob/main/src/packaging/metadata.py#L814) uses parse_email, and then adds extra checks on top of it. It sees whether the fields are allowed by the `Metadata-Version`. If `validate` is passed in, it also matches the types.
