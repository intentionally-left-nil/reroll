# Decisions
Moving from PyPI to conda via reroll is a new codepath, rather than an exact duplication of an existing feature. Therefore, reroll is bound only by the current filename spec and current use cases, not historical practices. The following decisions stem from this framing:

* Filenames must be parseable by packaging.parse_wheel_filename. Any legacy, out of compliance wheel name is ignored
* Reroll will only initially support CPython 3.x, not PyPy, GraalPy, or other ABI's. Further ABI support can be added later based on real use cases
* Reroll will not support Python 2.x wheels (or CPython 2.x)
* Reroll will not support the following platform tags (support can be added later based on real use cases)
  * musl linux
  * 32 bit operating systems
  * iOS and Android
  * linux builds outside of manylinux w/glibc pairings
  * Mac builds outside of x86_64, arm64, and universal2
* Reroll will only support x86_64 and arm64/aarch64 architectures (regardless of the platform)
* Reroll will only support the following python/abi combinations:
  * Any python 3.x version paired with the `none` ABI
  * A Python version paired with that exact CPython ABI e.g.  `cp314-cp314` or `cp314-cp314t`
  * Any CPython version >= 3.2 paired with the `abi3` ABI
  * Any CPython version >= 3.15 paired with the `abi3t` ABI
* Reroll will not accept build tags with the `d` debug, `m` pymalloc or `u` unicode ABI suffixes and will  disregard the wheel
* For a wheel with compressed tags, only the tags matching the above decisions will be used, others will be  discarded
* The filename parsing code for reroll may return zero, one, or multiple configs for a given filename:
  * If reroll doesn't support the filename due to the above constraints, zero configs will be returned
  * For many wheels, there will be exactly one matching config, corresponding to one repodata entry for the wheel
  * For some wheels, multiple configs will be returned (which will eventually map to multiple repodata entries). Compressed tag expansion and platform tag expansion are the most common reasons for this. Reroll will prefer resolver correctness and simplicity over repodata terseness by emitting multiple entries to the same wheel


# Background

## Reading material
* https://blog.yossarian.net/2024/06/12/Python-wheel-filenames-have-no-canonical-form
* https://snarky.ca/the-challenges-in-designing-a-library-for-pep-425/

In the PyPI-based ecosystem, the wheel filename contains meaningful information (and sometimes the only source of information) about a wheel to determine whether it can be installed.

The [PEP 491 specification](https://peps.python.org/pep-0491/#file-name-convention) and other documents specify that the wheel filename is in the following format: `{distribution}-{version}(-{build tag})?-{python tag}-{abi tag}-{platform tag}.whl`

Each sub-part of the wheel format must be further broken down, both by specification as well as the historical acceptance patterns for pip.

## Package name
The name component of a wheel is allowed to be non-normalized. E.g. for a package `reroll`, the package name could be `REROLL`, `ReRoll` or other equivalent variants. The normalization part of this segment is described via this [algorithm](https://packaging.python.org/en/latest/specifications/name-normalization/#name-normalization)

```py
def normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()
```

The filename here is the PyPI ecosystem filename. Future business logic outside of the filename parsing will be required to map PyPI names to conda-channel specific names when they diverge

## Package version
The version specifier is a very [complicated](https://packaging.python.org/en/latest/specifications/version-specifiers/#version-specifiers) spec.

This spec not only has a normalized form `[N!]N(.N)*[{a|b|rc}N][.postN][.devN]`, but it also allows for an expansive syntax which can then be normalized.

For example, `1.2.3.a1` is the normalized version of `1.2.3.alpha1`

Beyond the specification, pip used to have relaxed requirements around the wheel regex, which was then enforced starting in pip 25.3: https://github.com/pypa/pip/issues/12938

What this means in practice is that there are potentially old wheels which old versions of pip would install, but not new versions of pip.
Note that some of these differences in behavior apply to other fields, but the version is one of the main ones that caused old wheels to fail current validation

## Build tag
The build tag must start with a number, then it can be any unicode string except a hyphen `-`. In practice, this string is probably still ascii restricted. The only purpose of the build tags is to break ties when the wheels are otherwise identical in terms of matching. From the spec:

> Acts as a tie-breaker if two wheel file names are the same in all other respects (i.e. name, version, and other tags). Sort as an empty tuple if unspecified, else sort as a two-item tuple with the first item being the initial digits as an int, and the second item being the remainder of the tag as a str

Importantly from the PyPI side, a build number is not something really intended to be used from a version specifier (like pip install <build_tag>), and it's just supposed to be for tie-breaking

## Platform compatibility tags
Compatibility tags come in triplicates (python, abi, platform). For a wheel to be installed, it must satisfy all of these requirements. In the future, there may additionally be a `variant` build tag (e.g. for musl), but [PEP 825](https://peps.python.org/pep-0825/#variant-label) is in draft, and is not included in the following analysis.

### Python tag
The python tag either begins with `py` (any python interpreter) or `cp` (CPython ) followed by the python version specified.

* `py3` - Any version of python
* `py34` - Python 3.4 or newer
* `cp313` - CPython 3.13 (floor or pinned depends on the ABI)

As you can see, the python tag can either specify the minimum python to use, or it can specify the exact minor version to use. The conceptual model is that interpreter-specific python tags are assumed to be exact-version-only requirements, unless the wheel explicitly loosens that requirement with the `abi3` or `abi3t` ABI tags. Conversely, the vanilla `py` python tags are assumed to just specify the minimum version, and there is no way of specifying a maximum version of pure python via the filename alone.

Specifically:

| Python Tag  | abi tag | version | floor or exact  |
--------------|---------|---------|-----------------|
| py3         | none    | 3.0     | floor           |
| py37        | none    | 3.7     | floor           |
| py37        | cp37    | invalid | invalid         |
| py37        | abi3    | invalid | invalid         |
| cp3         | none    | invalid | invalid         |
| cp37        | none    | 3.7     | exact           |
| cp37        | cp37    | 3.7     | exact           |
| cp37        | abi3    | 3.7     | floor           |

In terms of validity, `py` tags require no python-specific functionality. To have any kind of ABI associated (whether it's abi3 or cp3XX) would violate that principle, hence the combination is invalid.

`cp3` bears specific calling out. The PEP spec supports it (as a peer to py3), and pip used to respect it. However, it was removed in [pip 20](https://pip.pypa.io/en/stable/news/#id997)

### ABI tag
The ABI tag is needed for compiled wheels which rely on specific binary layouts in order to operate properly. In practice, there are three main types of wheels:
* Wheels which do not require any binary-specific compatibility (most likely pure-python wheels). These should use the `none` ABI
* Wheels which use only the Stable Python 3 API (guaranteed to be binary-compatible for all versions of python). These wheels should use the `abi3` tag.
* Wheels which are compiled against a specific version (major+minor) of python. These wheels should specify the exact ABI they require e.g. `cp312`

Beyond these primary ABI's there are also free-threading related ABI's
* `abi3t` is supported for python >= 3.15 via [PEP 803](https://peps.python.org/pep-0803/). This has similar semantics as `abi3` - the wheel is compatible with any free-threading python greater than the baseline established at python 3.15
* The `t` suffix indicates the ABI is free threaded - e.g `cp314t` requires the free-threaded version of python 3.14

The `t` suffix is actually just one of many suffixes that the ABI can reference. The suffixes can be combined
* `d` - Python compiled with debug flags
* `u` - Python compiled with unicode support
* `m` - Python with pymalloc support

Unlike the `t` flag, the other flags are currently not supported by reroll. Instead, this will cause the wheel to be dropped.

Note that abi tags are fraught with ecosystem mislabling. According to [trailofbits](https://blog.trailofbits.com/2022/11/15/python-wheels-abi-abi3audit/), 15% of wheels mislabeled their ABI specifications (such as claiming to be universal ABI3 but really requiring version specific behavior). Compressed tags (described later) also contribute to this ecosystem fragility.

### Platform tag
The platform represents what underlying operating system (and other platform specifiers) the wheel requires. A wheel which can work on any platform (again, likely a pure python wheel) should use the `any` platform tag.

Other common tags can be seen in the [platform tag docs](https://packaging.python.org/en/latest/specifications/platform-compatibility-tags/)

Mac binaries in particular can combine many versions inside of one binary. These are given the platform tag of `universal2` or `universal`, and indicate support for e.g. both ARM and intel variants of macs.

The `manylinux` flavors are interesting in the sense that they define the minimum version of glibc the platform must support, but are otherwise meant to be compatible with a wide swath of linux distributions.

The `musl` platform tags are out of scope for reroll right now, and may be revisited if conda ever supports these variants.


## Compressed tags
Compressed tags allow a single wheel filename to indicate support for multiple platform tags, using periods.

For example: `py2.py3` is a valid python tag. It means "Allow both python 2 and python 3". Similarly, `cp312.cp313` is a valid ABI tag, allowing the wheel to work with both CPython 3.12 and CPython 3.13

There are lots of sharp edges with compressed tags. First off, when you combine this system to multiple parts of the platform tag, you end up with the superset of possibilities:
```py
for x in pytag.split("."):
    for y in abitag.split("."):
        for z in platformtag.split("."):
            yield "-".join((x, y, z))
```

So, if you have a platform tag with `py312.py313-cp312.cp313-any`
This is the same as if you had the following tag strings:
* `py312-cp312-any`
* `py312-cp313-any`
* `py313-cp312-any`
* `py313-cp313-any`

This can produce many tag combinations that are not possible. For example, no CPython version ever advertises the ABI except its exact version. So `py313-cp312` will never resolve to something that can build.
