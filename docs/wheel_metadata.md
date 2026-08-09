# Decisions
* Callers are responsible for validating the METADATA checksums
* Supported inputs must include: A pydantic object with just the important fields (to support data in databases), or the METADATA contents as a string
* The Name and version must match the filename
* Use metadata.parse_email to parse the metadata file.
* Items which are supposed to appear once (like Name), but actually appear multiple times with unique values should be treated as an error, not silently discarded.
* Reroll will first try to use the packaging module's modern parsing and validation to handle `Requires-Dist` and `Requires-Python`, matching how modern pip parses the fields
* Upon validation failure, reroll will implement uv's `LenientRequirement` fixups and try again
* If the LenientRequirement succeeds, reroll will use that packaging.Requirement, and emit a debug message with the fixup note
* Reroll will reject any wheel that cannot be parsed by LenientRequirement
* LenientRequirement coding will be implemented in a "copy the behavior identically as much as possible, cite UV's MIT license", so that future updates can stay in sync
* For `Provides-Extra`, each string will be handled with `canonicalize_name` but without validate=True (unlike the name field). Therefore, `Provides-Extra` will not cause metadata parsing to fail.
* For `license-expression`, it must pass the validator to be accepted into the struct. If the entry is not valid, it will be replaced by `None` instead of failing (with a debug message)
* Name and version are required, other fields are optional
* Failure to parse one of the fields we are tracking corresponds an overall failure, not a silent default value, except for `License-Expression` and `Provides-Extra` as called out above.

# Decision explanation
Reroll wants to support most of the python wheel ecosystem from Python 3.4 onwards. By definition, this means that we will need to handle wheels that were historically installable by pip. Some of these wheels conformed to an earlier spec, and some of these wheels were out-of-spec but pip had lower thresholds.

So, in order to achieve our goals we will need some amount of handling that is more relaxed than the newer [PEP-508](https://peps.python.org/pep-0508/) (and beyond) standards.

Utilizing pip's legacy code is not an easy option. Building our own exception handling poses to be a lot of work, especially around investigating what non-conformant examples are out there in the real world. So, instead the correct middle-ground is based around uv's logic. UV also has some amount of pressure to install somewhat older (or somewhat out-of-spec) wheels for compatibility purposes. Unlike pip, there isn't an "old uv" that only works for older versions. Since uv is permissively licensed open source, we can just re-use the logic that uv has.

It also provides a clear boundary for our initial implementation: If it's supported by uv's logic, we'll take it, otherwise we'll leave the wheel as unparseable.

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

## Other requires-dist quirks
pip <= 24.1 allowed relaxed parsing of requirement versions that are no longer allowed. For example ADLSstream 0.1.1
has a packaging bug:
```py
INSTALL_REQUIRES = [
    "numpy",
    "tensorflow>=2.1.0",
    "tensorflow-addons>=0.11.0keras-tcn",  # <-- missing comma
    "scikit-learn",
    "kafka-python",
]
```
which generates a requirement like this:
`tensorflow-addons (>=0.11.0keras-tcn)`

Previous versions of pip would install this as 
```py
>>> pkg_resources.Requirement.parse("tensorflow-addons (>=0.11.0keras-tcn)")
tensorflow-addons>=0.11.0keras-tcn   # single req, nonsense version
```

but uv will reject this requirement

## Packaging.Metadata quirks
`from packaging.metadata import Metadata, parse_email` encodes two different mechanisms of digesting a wheel's metadata. You could skip the `packaging` module entirely and use `email.message_from_string(metadata_text)` from the email module instead.

This is again a case of back-compat and how flexible to be with the requirements.

[parse_email](https://github.com/pypa/packaging/blob/main/src/packaging/metadata.py#L360) takes a relaxed posture. It handles some encoding inconsistencies, and otherwise handles some of the fields we don't care about (like keywords and project url's) and it normalizes the casing, but otherwise leaves things pretty-much alone

parse_email does have a notion of known string fields and raw fields. This creates a fun edge case - if a METADATA file contains multiple `Requires-Python` fields (for example), parse_email will detect this is not a string, and instead it will route it to the unparsed field. This leads us to the simple rule of treating this as an error, which mirrors how the api for parse_email is supposed to be used. The pseudo-logic is that if the field exists in the parsed data set, it is present. If it is not present, we need to check the unparsed data to see if it was omitted, or if it exists multiple times. If it exists multiple times, we further need to de-duplicate the entries. After deduplication, there might only be one unique value, in which case this should be treated as VALID. Otherwise, if there are zero entries, it didn't exist (depending on the field that might be okay). If there are multiple unique values then that's a spec violation and the METADATA should be discarded

[Metadata(validate=true/false)](https://github.com/pypa/packaging/blob/main/src/packaging/metadata.py#L814) uses parse_email, and then adds extra checks on top of it. It sees whether the fields are allowed by the `Metadata-Version`. If `validate` is passed in, it eagerly validates all fields, otherwise they are validated at access time

# uv METADATA parsing logic
uv is dual MIT/Apache licensed, so we can directly implement its logic (with appropriate inclusion of the MIT license for the rust-> python ported code).

Uv defines a [LenientRequirement struct](https://github.com/astral-sh/uv/blob/main/crates/uv-pypi-types/src/lenient_requirement.rs#L100)
Which provides a series of fixups (including regex replacements) in order to coerce a non-conforming requirement into a conforming requirement. Some of the fixups include emitting commas that were previously not required
```rust
// Given `>=7.2.0<8.0.0`, rewrite to `>=7.2.0,<8.0.0`.
(
    |input| regex!(r"(\d)([<>=~^!])").replace_all(input, r"$1,$2"),
    "inserting missing comma",
),
```

and so on. The `Requires-Python` code goes through the [LenientVersionSpecifiers](https://github.com/astral-sh/uv/blob/main/crates/uv-pypi-types/src/lenient_requirement.rs#L120) struct, which applies similar logic.

Failures to parse either of these fields after the Lenient fixups is treated as a hard error. Finally, `Provides-Extra` is [parsed](https://github.com/astral-sh/uv/blob/main/crates/uv-normalize/src/lib.rs#L17) to ensure conformance, 
```
let provides_extra = headers
  .get_all_values("Provides-Extra")
  .filter_map(
      |provides_extra| match ExtraName::from_owned(provides_extra) {
          Ok(extra_name) => Some(extra_name),
          Err(err) => {
              warn!("Ignoring invalid extra: {err}");
              None
          }
      },
  )
```
but then failures are [ignored](https://github.com/astral-sh/uv/blob/main/crates/uv-pypi-types/src/metadata/metadata_resolver.rs#L68-L74) by completely ignoring the extra

## Reimplementing vs CLI or crate usage
Uv does not provide python bindings (or even stable rust crates) for its logic. The CLI has no ability to parse just a wheel metadata, but instead would try to solve/install the file. Neither of these are thus options for reroll. However, the source code is straightforward, and not too sprawling to replicate in python. The main consideration would be the difference in regex handling between the two languages, but that can be carefully checked (both in terms of code inspection, but also via tests)

# Pip Metadata parsing logic
Pip switched over to a strict parsing logic as of modern versions of pip. So, older versions of pip would allow more packages to be installed, compared to the current versions which have more restrictions.

Modern pip has some interesting behaviors though:
1. It strictly requires packaging.Requirement to succeed for Requires-Dist
2. It uses packaging.specifiers.SpecifierSet to parse Requires-Python, but upon failure, warns and drops any requirement here
3. `Provides-Extra` names are canonicalized, but never dropped
