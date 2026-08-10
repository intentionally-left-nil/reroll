# Decisions
* Dependency generation is intended to be compatible between both conda-forge and main. Features (like python_abi support) are only used in scenarios where both channels support it
* The conda ecosystem doesn't prevent ABI breakages of e.g. pypy before `python_abi` was introduced, so the wheel work will not try to prevent ABI breakages whene `python_abi` is unsupported
* Expand out abi3 and abi3t into multiple repodata entries, rather than relying on `python-gil`
* Reroll must silently strip out (but not error) any `Requires-Dist: Python ...` dependency (direct dependency on python, not a marker) for python and all related interpreters (python, cpython, pypy, graalpy)
* `Requires-Dist` dependencies which contain local tags, direct URL's, or pre-release tags are invalid, and cause reroll to stop processing the entire repodata entry
* An extra flag `allow_pre` is passed into the wheel_dependencies function. If true, then pre-release tags (dev, devN, alpha, beta, rc and their short-forms) are permitted, and are passed through as a valid dependency specifier

# Calculating the conda specifiers for python
Since wheels were built and installed in the pypi ecosystem, we generally want the conda equivalent to match the real-world environments that pypi would have created.

For the python dependency, this means that the wheel filename (including the python version AND the ABI), `Requires-Python` all feed into what actually would have been installable. Our conda algorithm will try to combine these sources in a way that keeps our matching environments similar in spirit to wheels.

Our algorithm for converting wheel information into conda `depends` is:
1. Determine if the python/abi combo requires an exact minor version of python, or a floor of python, following the [wheel_filename.md](./wheel_filename.md#python-tag) table
2. If the wheel metada contains `Requires-Python`, intersect this range with the filename range. If the intersection is solvable, tighten the range to the intersection of both values. If the intersection is not solvable, the repodata will not be generated, return and emit a log warning
3. Generate a conda `depends` for python following the below rules for various cases

## Wheel is pure python (interpreter and ABI independent)
This is the easy case, just copy the tightened version rangne as a matchspec into depends, e.g. `python >= 3.8`
If the wheel requires an exact minor version, or any funky tightening, don't normalize the range any further. Just use the tightened range as-is.

## Wheel is a compiled wheel for a specific CPython GIL, minor version less than 3.13

Although conda-forge emits python_abi packages all the way down to 2.x, Anaconda's main channel does not emit below 3.10, so we need to fallback to the old way of describing dependencies. Additionally, the python_abi builds between 3.10-3.12 are missing the _cp3XX build tag, making targeting annoying. So, we will not use python_abi below 3.13. 

A consequence is this will allow e.g. `pypy` to install, but as per the decisions, that's acceptable and congruent with how it works on main teday.

1. Emit a python dependency for that minor version, e.g. `"python >=3.7,<3.8.0a0"` - Whatever the incoming python range is should be fine to use as-is
2. Don't emit an ABI tag, and don't do extra validation the ABI tag matches - this is the responsibility of the filename parsing layer (aka `check_interpreter_abi`)

## Wheel is a compiled wheel for a specific CPython GIL, minor version >= 3.13

For these wheels, we can additionall emit the correct python_abi to constrain solves to just CPython. Per filename restrictions, we only support CPython-based wheels, so we can simplify the logic here:

1. Emit a python dependency for that minor version, e.g. `"python >=3.7,<3.8.0a0"` - Whatever the incoming python range is should be fine to use as-is
2. Take the ABI tag, and from that version e.g. `cp313`, derive the `python_abi` Matchspec combining a version plus build string e.g. `"python_abi 3.13.* *_cp313"`

## Wheel is a compiled wheel for a specific CPython free threaded version
All free threaded builds are >= 3.13, so this is just a small modification to the normal GIL case.
1. Emit a python dependency for that minor version, e.g. `"python >=3.7,<3.8.0a0"` - Whatever the incoming python range is should be fine to use as-is
2. Take the ABI tag, and from that version e.g. `cp313`, derive the `python_abi` Matchspec combining a version plus build string e.g. `"python_abi 3.13.* *_cp313t"` (note the `t` at the end)

## Wheel is a compiled wheel for a CPython floor, and abi3 or abi3t
This is an error. The [wheel_filename](./wheel_filename#abi-explosion) docs specifically solve this problem by exploding out the value of python to a specific version, because the dependencies cannot be expressed in conda otherwise. If the dependency generation code sees `abi3` or `abi3t` it should error out as this is reroll's bug, not a caller bug or wheel issue

# How conda repodata expresses dependencies
Traditional conda repodata (v1, predating wheels) expresses repodata dependencies via two keys, `constrains`, and `depends`.
Both of these keys are arrays of conda [MatchSpec (CEP29)](https://conda.org/learn/ceps/cep-0029/)

The difference between these arrays is that depends specifies what dependencies a package has, just like pypi. `constrains` on the other hand, maps to `run_constrained` and says "don't install these packages, but if they do happen to be installed, they must be this version".
Historically, one of the main use cases for this in the conda ecosystem has been the lack of `extras`. If you had a package like `fastapi[cli]` then on the pypi side that would translate into two separate dependencies - one for fastapi, one for when the user wants the extra `cli` things.

Since conda didn't previously support `extras` natively, some conda recipes put these values in run-constrained today. That was an imperfect match, but it said "if you happen to install these extra packages, make sure they meet this version".

`constrains` was [removed](https://github.com/conda/ceps/pull/145/changes/549b023241d751cfec578712080734f1ec6291d3) from the proposal after community feedback. and is not part of consideration.

Note: Although the specific example for constrains was `!=`, this doesn't really have anything to do with constrains. != is a perfectly valid MatchSpec syntax, and can belong in depends just fine

## How conda recipes usually handle python dependencies
The python version has always gone as a normal dependency. If it was a pure python wheel, it might have been either `python 3.6*` or `"python >=3.7,<3.8.0a0"`

If something depended on a specific interpreter, then more hardcoding of the build string was used, like this: `"python 3.8.* *_cpython` (or pypy etc). However, once the `python-abi` package was introduced, then most new recipes started relying on that. Here's a modern numpy example:

```
"python >=3.13,<3.14.0a0",
"python_abi 3.13.* *_cp313"
```

which pins both the version of python to a range, and a separate abi build tag match to enforce `*_cp313` based ABI's

# Determining the python environment to compare wheel validity against
Both pip and uv have mechanisms to determine which version of python to target. This takes place _before_ even evaluating wheels, so the python target is always known before checking to see if a wheel is capable of installing into that environment.

## Pip behavior
pip itself is just a python package. The $path variable is just a shell script with `#!<path-to-python>` and then it executes `from pip._internal.cli.main import main`. `python -m pip` does something similar (but then the user has explicit control over which python path to use). Your pip and python executables should point to the same thing, but if your $PATH is messed up, they could technically point to different things. If that happens, the version of python used is whatever is running the pip code, not what's on your $PATH

Pip technically _can_ install to a different target such as
```sh
pip3 install --python-version 38 --platform manylinux2014_x86_64 --implementation cp --abi cp38 \
      --only-binary=:all: --no-deps --target ./out six
```
to create a cross-platform environment, but that is an edge case (and also fraught if you need to install any sdists, needing the correct toolchain and everything)

Regardless, this means that `pip` already knows what version of python to target, since it installs packages into the current environment.
If pip is running in a venv with `python 3.9`, then any wheel candidate it might evaluate further must have a filename compatible with 3.9 before even digging into its contents

## uv behavior
Uv is a rust binary, so it's not inherently tied to a version of python. uv has the ability to download python versions from the internet (unless configured otherwise), so it's not constrained to just the system python. Thus, uv has extra logic ahead of time to determine what the base python version is before trying to install wheels into it. From their [python version docs](https://docs.astral.sh/uv/concepts/python-versions/#python-version-files)

we can see that the following algorithm happens:
1. Passing the `--python` CLI variable or the `UV_PYTHON` env variable overrides the python choice
2. If there is a local `.python-version` file then that will be honored
3. If there is a global `.python-version` file that will be honored
4. If uv is working in a project directory, it will honor the `Requires-Python` in that pyproject.toml
5. If there is no other signal, then the python interpreter could be from the current venv in the shell, a conda environment, or the system python (depending)

In a lot of ways, the uv mechanics don't really matter too much for us when it comes to the main question of "Does this wheel install?". Similarly to pip, by the time uv is ready to install a wheel, the version of python to target against that wheel is known.

Note that `uv.lock` isn't really a part of this decision - uv.lock is about which versions of the packages to use, not necessarily the python version (as best as I can tell).

# Determining if a wheel can be installed into a python environment
Now that the python environment is always known, both installers first do a sanity check against the wheel filename, and early exit if the filename is not compatible. Otherwise, the `Requires-Python` is consulted, and finally the dependencies are checked for validity.

## pip behavior
Pip first needs to decide whether to download a wheel, and then afterwards to decide if a wheel in its cache is worth digging further into.

The pypi [JSON API (PEP 691)](https://peps.python.org/pep-0691/) provides pip with a list of packages, which contain both a filename and a `requires-python` argument which pypi derives from the wheel metadata.

Here's what pip does with this information:
1. Parses the filename and determines if the python/abi tags are compatible with the environment. If not, skip the wheel
2. Check if `requires-python` via the API call (which is transitively from the wheel METADATA file) is compatible. If not, skip the wheel
3. Download the wheel (or just the metadata if the pypi index server supports [PEP-658](https://peps.python.org/pep-0658/))
4. Once a wheel is downloaded, the `Requires-Dist` section from the wheel's metadata is parsed and compatibility with the environment is further parsed. `Requires-Dist` might contain markers for python, but that is separate, and only for its dependencies. There is no `python` package in pypi.org, so `Requires-Dist: python>3.9` would fail to resolve for example.
5. If there are dependencies that need to be installed, but are not yet in the environment then these are recursively installed. Once all dependencies are satisfied, then the current wheel can be installed into the environment

## uv behavior
uv largely behaves in a similar way to pip in terms of checking an individual wheel validity. uv does have a lock file, but that only tells uv which wheels to use, not whether the wheel is compatible with the environment. Once a wheel is selected, uv goes through the same algorithm to ensure a wheel is permissable within the environment

# Parsing wheel filename requirements
This is fully described [wheel_filename.md](./wheel_filename.md#python-tag), but in short the python version depends on both the python tag as well as the abi tag.

# Handling Requires-Dist for python versions
On the pypi side, technically a Requires-Dist requirement of `python` is not spec-rejecting. However, pypi won't let someone name squat the `python` package, so this is moot. However, on the conda side, you _can_ download python, so if a wheel specified python as a dependency in the wrong place, this would otherwise map cleanly. We should solve this by removing any interpreter from the requirements list. This includes: `python`, `cpython`, `pypy`, and `graalpy`. None of these are valid pypy packages.


# Simple dependency conversion
The main format of a simple dependency is name-operator-version. If the operator is missing, it is identical to name == version.
Generally speaking, names will go through the name converter, operators will go into Matchspec as-is to do the conversion, and the version needs to be thought through

Dependency names are converted from pypi names to conda names using the same conversion mechanism as the main filename. Failures to convert stops repodata generation with a log output.

## Operator conversion
The operator can be passed as-is. Conda's matchspec understands PEP-440 style strings. There are only two specific examples worth calling out

First, the `~=` syntax historically was not well-supported via conda. This was properly fixed in [Conda 4.9](https://docs.conda.io/projects/conda/en/latest/release-notes.html#id307)
So, although traditional conda recipes might have exanded `~=` into >= and < ranges, there is no need for our code to do so.

Secondly, triple equals `===` means different things for PyPI vs Conda. For pypi, triple equals is almost like an escape hatch. It means "do an exact string comparison, don't worry about any details about the format". This is to handle things like local tags, url's or other oddities.
Conda, on the other hand doesn't have any notion of an exact string match. `==` or `no operator` are prefix-based matches. So, only the beginning has to match, and the rest can diverge.

The `===` is very rare in pypi, so in some regards it doesn't matter too much what we do with it. However, by default conda's matchspec will parse `===` and treat it as `==`, so we will continue observing the same behavior and emit `==`.


## Version conversion
Pip supports more tweaks on the version than conda natively supports. This includes:
* epochs - `package 1!1.0`
* local tags - `package 1.0.0+awesome`
* direct URL's - `package @ https://example.com`
* Pre-releases - `package 1.0.0rc1`

Epochs are allowed. They are supported by matchspec, and are properly parsed by rattler and other clients.

Some of these we can dismiss out of hand. Dependencies with direct URL's are to be reject out-of-hand. It shouldn't occur and it's natural to emit an log item and stop processing the wheel when this happens. Presumably the author is deliberately trying to access another registry,
but that wouldn't know anything about conda and also it's a security risk. Rejecting the entire wheel, rather than silently stripping the URL brings visibility to the problem.


Local tags are by-design not intended to be uploaded to a repository. PyPI will not allow a package with a local tag to be uploaded. That also means that if a package has a `Requires-Dist: package 1.0.0+awesome`, this would never be able to resolve from a wheel on PyPI.
Therefore, we can also reject local tags.

### Pre-release
Pre-releases are different, in that they are valid specifications in both pypi and conda. The problem is that pip will not install pre-releases without `--pre` or a specific requirement for an exact pre-release (alpha/beta/rc) version. So `pip install mypackage=1.0.0` will not pick up `package 1.0.0rc1` even if present in the registry.

Conda doesn't have this support, but the pre-release would be a lower-priority match. So, given a conda version of `1.0.0` and `1.0.0.rc2` with a specifier match of `mypackage==1` or `mypackage==1.0.0` it would prefer the non-rc candidate. The only time an RC candidate would be picked up would be when the production version does not exist. Unfortunately... the whole point of a pre-release is exactly that (to release something before the final version exists). The conda solution is to add labels (which essentially act as micro-channels). So the default label would have the main release, and a `dev` label would have the prerelease.

Even in this label case, the label is not part of the dependency specification. There's no conda way to specify `mypackage/label/dev::mypackage==1.0.0.rc2` in order to get an rc package.

Therefore, putting all this together leads to a strong reason to reject alpha suffixes in releases. It would probably be okay to strip them, under the assumption that the code in a final release trumps a release candidate. However, for the sake of clarity, clear failure cases (rather than hidden changes), it's better to initially reject such dependencies, see if it actually occurs, and adjust from there.

To mimic the concept of labels, a repodata author might intend for a certain channel to contain -alpha, -beta, -rc packages in it. In this case an `allow_pre` flag can toggle this behavior
One final note is that `allow_pre` will apply equally to both package versions, as well as version dependencies. This keeps things consistent (channels can either have pre-release packages and rely on other pre-release packages, or they can't)

### Post-release
Post releases are fine, however, because they are intended to take priority over an existing wheel. So, any pypi dependency like `mypackage 1.0.0.post1` is accepted, (and the package with that release would also be accepted by the filename command)
