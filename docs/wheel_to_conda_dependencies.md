# Decisions
* Dependency generation is intended to be compatible between both conda-forge and main. Features (like python_abi support) are only used in scenarios where both channels support it
* The conda ecosystem doesn't prevent ABI breakages of e.g. pypy before `python_abi` was introduced, so the wheel work will not try to prevent ABI breakages when `python_abi` is unsupported
* Expand out abi3 and abi3t into multiple repodata entries, rather than relying on `python-gil`
* Reroll must silently strip out (but not error) any `Requires-Dist: Python ...` dependency (direct dependency on python, not a marker) for python and all related interpreters (python, cpython, pypy, graalpy)
* `Requires-Dist` dependencies which contain local tags, direct URL's, or pre-release tags are invalid. Any invalid tags should cause the code to not emit a wheel record, and the error should be logged.
* An extra flag `allow_pre` is passed into the wheel_dependencies function. If true, then pre-release tags (dev, devN, alpha, beta, rc and their short-forms) are permitted, and are passed through as a valid dependency specifier
* Provides-Extra is completely ignored. Extras for a package are determined by accumulating and normalizing the extra names from `Requires-Dist` only
* All dependency calculations must follow the [Calculating Extras](#calculating-extras) algorithm, including base dependencies (which would have extras="")
* Dependencies generated for an extra may include dependencies that also apply to the base depends. These duplicates will be removed as a post-processing step once all dependencies have been calculated
* If a noarch package contains arch-specific dependency markers, a noarch wheel record will not be emitted. Instead, a wheel record for every supported subdir (`osx-64`, `osx-arm64`, `win-64`, `win-arm64`, `linux-64`, `linux-aarch64`) will be emitted in its place. Attempts to preserve noarch wheels using virtual packages (__win, etc) run into complex challenges with `!=` and `platform_machine` -> `__archspec` conversion
* Per CEP-29, ~= is deprecrecated. Any instance of  ~= should be expanded to the appropriate range specifier


# Calculating the conda specifiers for python
Since wheels were built and installed in the pypi ecosystem, we generally want the conda equivalent to match the real-world environments that pypi would have created.

For the python dependency, this means that the wheel filename (including the python version AND the ABI), `Requires-Python` all feed into what actually would have been installable. Our conda algorithm will try to combine these sources in a way that keeps our matching environments similar in spirit to wheels.

Our algorithm for converting wheel information into conda `depends` is:
1. Determine if the python/abi combo requires an exact minor version of python, or a floor of python, following the [wheel_filename.md](./wheel_filename.md#python-tag) table
2. If the wheel metada contains `Requires-Python`, intersect this range with the filename range. If the intersection is solvable, tighten the range to the intersection of both values. If the intersection is not solvable, the repodata will not be generated, return and emit a log warning
3. Generate a conda `depends` for python following the below rules for various cases

# Calculating extras
1. For all Requires-Dist entries, parse the markers for `extra == <name>` regardless of the conditional evaluation it might be a part of.
2. Take all of the found markers, normalize them with canonicalize_name, and then deduplicate them into a set(). These are your candidate extras. Any reference to `extra_name` beyond this point refers to an item in this normalized set.
3. Do not cross-validate this set with `Provides-Extra`
4. Run the [Calculating conditional dependencies](#calculating-conditional-dependencies) algorithm, with the corresponding extra name
5. For each remaining dependency, further refine it by calling `tighten_ranges` on the marker
6. Next, convert every remaining dependency to a Matchspec. This Matchspec may be conditional (contains a `when` specifier) if the marker evaluation was not complete
7. If extras was "", add the dependency and its matchspec to the `depends` output, otherwise add it to the corresponding `extra_name` key in the extras output.

# Calculating conditional dependencies
1. Parse the marker for references to `python_full_version` and `implementation_version`. If either is referenced with `==`, `!=`, `in`, or `not in` anywhere in the marker, the corresponding environment variable must be REMOVED from the base environment
2. Add the appropriate `extra` value to the base environment, depending on what extra is being targeted (see [Calculating Extras](#calculating-extras))
3. Use markerpry to evaluate the marker. The return is called a `partial_evaluation` (even though the eval might be true/false)
4. If `python_full_version` is still in the base environment (i.e. step 1 did not remove it), re-calculate setting python_full_version to `3.XX.100` and compare the tree structure with the previous result. If they are not identical, you must remove python_full_version from the base environment, and run the evaluation a third time. This third evaluation becomes the new `partial_evaluation`. Apply the same logic to `implementation_version`
5. If `partial_evaluation` evaluates to true, add the simple dependency/version without any markers
6. If `partial_evaluation` evaluates to false, return (no dependency should be added)
7. If the environment type is noarch, check to see if any of the following keys exist: `platform_system`, `platform_machine`, `sys_platform`, `os_name`. If so, the repodata must be emitted as arch-specific instead. The individual parsing should stop, and control reverted back to the overall algorithm to switch to per-arch packages.
8. Otherwise, check for any unpermitted keys in the tree, besides these: `python_version`, `python_full_version`, `implementation_version`. If any unpermitted keys are present, the dependency cannot be generated and the wheel should not be emitted.
9. Convert the remaining tree to a matchspec string

## Noarch base environment
python_version: Set if applicable (e.g. `3.14`)
python_full_version: Set if applicable (e.g. `3.14.0`) (also do a second environment check with `3.14.100`)
platform_python_implementation: `CPython`
implementation_name: `cpython`
implementation_version: same as python_full_version

## Arch-specific base environment
python_version: Set if applicable (e.g. `3.14`)
python_full_version: Set if applicable (e.g. `3.14.0`) (also do a second environment check with `3.14.100`)
platform_python_implementation: `CPython`
implementation_name: `cpython`
implementation_version: same as python_full_version
platform_system: `Linux`, `Darwin`, or `Windows`
platform_machine: `x86_64`, `AMD64`, `arm64`, `aarch64`, or `ARM64` (see the [platform_machine](#platform_machine) table)
sys_platform: `linux`, `darwin`, or `win32`
os_name: `posix` or `nt`

## Wheel is pure python (interpreter and ABI independent)
This is the easy case, just copy the tightened version rangne as a matchspec into depends, e.g. `python>=3.8`
If the wheel requires an exact minor version, or any funky tightening, don't normalize the range any further. Just use the tightened range as-is. That is about the range's meaning, not its spelling - an exact minor arrives as `==3.13.*` and is emitted as the equivalent range `python>=3.13,<3.14.0a0`, not the fuzzy `=3.13` form from [Operator conversion](#operator-conversion), so that the `0a0` upper bound excludes 3.14 pre-releases.

## Wheel is a compiled wheel for a specific CPython GIL, minor version less than 3.10

Although conda-forge emits python_abi packages all the way down to 2.x, Anaconda's main channel does not emit below 3.10, so we need to fallback to the old way of describing dependencies. Additionally, free threaded builds don't exist before 3.13, so we will not emit python_abi below that point (and should ignore this invalid combo to begin with)

A consequence is this will allow e.g. `pypy` to install, but as per the decisions, that's acceptable and congruent with how it works on main today.

1. Emit a python dependency for that minor version, e.g. `"python >=3.7,<3.8.0a0"` - Whatever the incoming python range is should be fine to use as-is
2. Don't emit an ABI tag, and don't do extra validation the ABI tag matches - this is the responsibility of the filename parsing layer (aka `check_interpreter_abi`)

## Wheel is a compiled wheel for a specific CPython GIL, minor version >= 3.10

For these wheels, we can additionall emit the correct python_abi to constrain solves to just CPython. Per filename restrictions, we only support CPython-based wheels, so we can simplify the logic here:

1. Emit a python dependency for that minor version, e.g. `"python >=3.7,<3.8.0a0"` - Whatever the incoming python range is should be fine to use as-is
2. Take the ABI tag, and from that version e.g. `cp313`, derive the `python_abi` Matchspec combining a version plus build string e.g. `"python_abi 3.13.* *_cp313"`

## Wheel is a compiled wheel for a specific CPython free threaded version
All free threaded builds are >= 3.13, so this is just a small modification to the normal GIL case.
1. Emit a python dependency for that minor version, e.g. `"python >=3.7,<3.8.0a0"` - Whatever the incoming python range is should be fine to use as-is
2. Take the ABI tag, and from that version e.g. `cp313t`, derive the `python_abi` Matchspec combining a version plus build string e.g. `"python_abi 3.13.* *_cp313t"` (note the `t` at the end)

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

If something depended on a specific interpreter, then more hardcoding of the build string was used, like this: `"python 3.8.* *_cpython` (or pypy etc). However, once the `python_abi` package was introduced, then most new recipes started relying on that. Here's a modern numpy example:

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
The main format of a simple dependency is name-operator-version. We can look at each of these parts in isolation in order to understand how to evaluate and convert them.

Dependency names are converted from pypi names to conda names using the same conversion mechanism as the main filename. Failures to convert stops repodata generation with a log output.

## Operator conversion
Most operators can be passed as-is - conda's matchspec covers the same comparison operators PEP-440 does (`==`, `!=`, `<`, `<=`, `>`, `>=`, `~=`). Three cases need more care: `~=`, PEP-440 prefix matching, and `===`

First, the `~=` syntax historically was not well-supported via conda. This was properly fixed in [Conda 4.9](https://docs.conda.io/projects/conda/en/latest/release-notes.html#id307), however the syntax is considered deprecated in CEP-29
> Semver-like comparisons, expressed as a version literal prefixed by the ~= string, MUST be interpreted as greater than or equal to the version literal while also matching a fuzzy equality test for the version literal sans its last segment (e.g. ~=0.5.3 expands to >=0.5.3,0.5.*). This operator is considered deprecated, and its expanded alternative SHOULD be used instead
Therefore, the correct conversion of `~=3.13.2` is (`>=3.13.2,<3.14.0a0`). The `0a0` suffix check is important because otherwise 3.14 pre-releases would be considered part of the range.

Next, `==` with a glob is a grey area: CEP-29's rationale says mixing globs with the other operators is disallowed. To avoid confusion, prefix specifiers should be rewritten to the canonical fuzzy form, so `==X.Y.*` becomes `=X.Y`. Prefix *exclusion* needs no rewrite, so `!=X.Y.*` passes through unchanged.

Lastly, triple equals `===` means different things for PyPI vs Conda. For pypi, triple equals is almost like an escape hatch. It means "do an exact string comparison, don't worry about any details about the format". This is to handle things like local tags, url's or other oddities.

The `===` is very rare in pypi, so in some regards it doesn't matter too much what we do with it. However, by default conda's matchspec will parse `===` and treat it as `==`, so we will continue observing the same behavior and emit `==`.

## Version conversion
Pip supports more tweaks on the version than conda natively supports. This includes:
* local tags - `package 1.0.0+awesome`
* direct URL's - `package @ https://example.com`
* Pre-releases - `package 1.0.0rc1`

In addition, the Conda version syntax, specified by [CEP-33](https://conda.org/learn/ceps/cep-0033) is different than PEP-440. For example: `1.0.0rc1` for pypi is most closely translated as `1.0.0.rc1` (note the period) for conda. Another example is that `1.0-1` PEP440 shorthand for `1.0.post1`.

Some of these we can dismiss out of hand. Dependencies with direct URL's are to be reject out-of-hand. It shouldn't occur and it's natural to emit an log item and stop processing the wheel when this happens. Presumably the author is deliberately trying to access another registry,
but that wouldn't know anything about conda and also it's a security risk. Rejecting the entire wheel, rather than silently stripping the URL brings visibility to the problem.

Local tags are by-design not intended to be uploaded to a repository. PyPI will not allow a package with a local tag to be uploaded. That also means that if a package has a `Requires-Dist: package 1.0.0+awesome`, this would never be able to resolve from a wheel on PyPI.
Therefore, we can also reject local tags.

### Version conversion algorithm
1. start with a packaging.version.Version object.
2. If the object contains local tags, remove it.
3. Append the epoch (if it exists)
4. Append the release segments, joined by a `.`
5. If there is a pre-release, emit `.{letter}{num}` from v.pre
6. If there is a post-release, emit `.post{v.post}`
7. If there is a dev release, emit `.dev{v.dev}`

### Pre-release
Pre-releases are different, in that they are valid specifications in both pypi and conda. The problem is that pip will not install pre-releases without `--pre` or a specific requirement for an exact pre-release (alpha/beta/rc) version. So `pip install mypackage==1.0.0` will not pick up `package 1.0.0rc1` even if present in the registry.

Conda doesn't have this support, but the pre-release would be a lower-priority match. So, given a conda version of `1.0.0` and `1.0.0.rc2` with a specifier match of `mypackage==1` or `mypackage==1.0.0` it would prefer the non-rc candidate. The only time an RC candidate would be picked up would be when the production version does not exist. Unfortunately... the whole point of a pre-release is exactly that (to release something before the final version exists). The conda solution is to add labels (which essentially act as micro-channels). So the default label would have the main release, and a `dev` label would have the prerelease.

Even in this label case, the label is not part of the dependency specification. There's no conda way to specify `mypackage/label/dev::mypackage==1.0.0.rc2` in order to get an rc package.

Therefore, putting all this together leads to a strong reason to reject alpha suffixes in releases. It would probably be okay to strip them, under the assumption that the code in a final release trumps a release candidate. However, for the sake of clarity, clear failure cases (rather than hidden changes), it's better to initially reject such dependencies, see if it actually occurs, and adjust from there.

To mimic the concept of labels, a repodata author might intend for a certain channel to contain -alpha, -beta, -rc packages in it. In this case an `allow_pre` flag can toggle this behavior
One final note is that `allow_pre` will apply equally to both package versions, as well as version dependencies. This keeps things consistent (channels can either have pre-release packages and rely on other pre-release packages, or they can't)

### Post-release
Post releases are fine, however, because they are intended to take priority over an existing wheel. So, any pypi dependency like `mypackage 1.0.0.post1` is accepted, (and the package with that release would also be accepted by the filename command)

# Dealing with extras
Extras provide the way for one package to indicate "I have a second set of dependencies", and for another package to say, I depend on the first package with its additional dependencies.

For example, with FastAPI:
```
Metadata-Version: 2.1
Name: fastapi
Version: 0.115.0
Requires-Python: >=3.8
Requires-Dist: starlette (<0.39.0,>=0.37.2)
Requires-Dist: pydantic (!=1.8,!=1.8.1,!=2.0.0,!=2.0.1,!=2.1.0,<3.0.0,>=1.7.4)
Requires-Dist: typing-extensions (>=4.8.0)
Requires-Dist: importlib-metadata (>=1.0) ; python_version < "3.10"
Provides-Extra: standard
Requires-Dist: fastapi-cli[standard] (>=0.0.5) ; extra == "standard"
Requires-Dist: httpx (>=0.23.0) ; extra == "standard"
Requires-Dist: jinja2 (>=2.11.2) ; extra == "standard"
Requires-Dist: python-multipart (>=0.0.7) ; extra == "standard"
Requires-Dist: uvicorn[standard] (>=0.12.0) ; extra == "standard"
Provides-Extra: all
Requires-Dist: httpx (>=0.23.0) ; extra == "all"
Requires-Dist: orjson (>=3.2.1) ; extra == "all"
```

this provides for two extras - `standard`, and `all`

Extras are somewhat underspecced. First, the names of specs were originally not normalized. They were normalized for later wheels such that multiple `Provides-Extra` e.g. for `ALL` and `all` would raise an error at packaging time, leading to the following guidance in the [core metadata](https://packaging.python.org/en/latest/specifications/core-metadata/)

> When writing data for older metadata versions, names MUST be normalized following the same rules used for the Name: field when performing comparisons. Tools writing metadata MUST raise an error if two Provides-Extra: entries would clash after being normalized.

Also underspecified is what happens when `Provides-Extra` is not present. The same core spec says
> It is legal to specify Provides-Extra: without referencing it in any Requires-Dist:.

However the spec is silent on the other way around. Is it legal to have a Requires-Dist that has an extra, without the Provides-Extra?
pip and uv differ in their behavior here. Pip will silently refuse to install any extra dependencies if the corresponding `Provides-Extra` for that key is missing. Uv, on the other hand, will allow it, but emit a warning

Since Provides-Extra only guards against a typo - tools can definitively determine the extra names just by aggregating the `Requires-Dist` entries, we will treat `Provides-Extra` as superfluous and ignore it.

## Extras name normalization
`Provides-Extra` must match the following specifier: `^[a-z0-9]+(-[a-z0-9]+)*$`. In the pypi ecosystem, this is normalized by applying the same normalization `re.sub(r"[-_.]+", "-", name).lower()` mechanism used for other names.
The conda CEP for extras has a slightly different regex: `[a-z0-9_.+-]{1,64}`. It doesn't restrict the starting character, it allows `_`, but it also restricts the length to 64 characters. We can therefore create a compliant extras normalizer by running: `canonicalize_name` and then rejecting any extra > 64 characters.

## Requiring a dependency with an extra
On the flip side, what happens if another package includes `Requires-Dist: fastapi[all]` in their requirements?
This needs to be translated into the conda-equivalent matchspec `fastapi[extras=[all]]`. However, matchspec does not accept `fastapi[all]` (since the [] notation is already overloaded in conda-land). So, instead, the extra markers need to be extracted, f'extras=[','.join(extracted)]' needs to be called (after normalizing the names of course)

# Conditional markers
[Environment markers](https://peps.python.org/pep-0508) allow for installation of a dependency to be dependent on certain properties of the system during install time. Previously this was a source of headaches trying to automatically convert pip dependencies into conda dependencies. However, conda now supports conditional dependencies via [CEP-43](https://conda.org/learn/ceps/cep-0043)

Reroll supports subdir-specific repodata, and we can use this information (along with other known restrictions like the python version) to clean up the environment markers before converting them to conda.

Let's talk about some of the environment marker variables and how to calculate them, then we can use them in base environments:

## python_version
This is the major.minor (not patch) version (`3.13`, not `3.13.X`). This means that we can take our algorithm to constrain the wheel's python version, and check to see if that range applies exactly to a minor range. If the check matches, then we can set `python_version` to the specific minor version

## python_full_version
This is the same value as python_version, but also including the patch (3.13.0). Many actual uses of this implementation for wheels are not patch-specific, but should be using python_version instead. Perhaps the legitimate case would be if something were specifically targeting a beta patch level like `3.13.2b`

Assuming that the known version of python is restricted to a specific minor version, ordered comparisons (`>=`, `>`, `<`, `<=`) can get acceptable coverage by checking two values (3.minor.0), and (3.minor.100). If the resolution is the same in both cases, we can essentially treat this as `python_version`, and otherwise we can know not to emulate this value.

Equality and containment (`==`, `!=`, `in`, `not in`) cannot be satisfied with point-checks. It's too much work to enumerate all possibilities of patches, and to test every single one, so we will just not try to reduce this requirement.

Thus a fully comprehensive check of python_full_version is as follows:

Given a known python_version 3.XX:
1. Parse the tree for `python_full_version` used with (`==`, `!=` or `in/not in`) operator. If it exists, do not use python_full_version
2. Try solving for `python_full_version` set to `3.XX.0`
3. If `python_full_version` is still set after step 1, perform another solve with `3.XX.100` - If the tree is identical, then use it. Otherwise, do not set `python_full_version`

## platform_system
Corresponds to [platform.system()](https://docs.python.org/3/library/platform.html#platform.system)
> Returns the system/OS name, such as 'Linux', 'Darwin', 'Java', 'Windows'. An empty string is returned if the value cannot be determined.

This can be determined by the subdir the repodata is targeting.

## sys_platform
A close relative of platform_system. [sys.platform](https://docs.python.org/3/library/sys.html#sys.platform)
For reroll, the keys here are:
`linux`, `darwin`, `win32` (case-sensitive). There are other values but reroll doesn't support those platforms right now.

## os_name
[os.name](https://docs.python.org/3/library/os.html#os.name) can either be `posix` (linux and mac), or `nt` (windows)


## platform_machine
the docs [platform.machine](https://docs.python.org/3/library/platform.html#platform.machine) don't give a concrete list
but we can provide the known values that we care about as: 
| Value | Description |
|---|---|
| `x86_64` | macOS Intel or Linux 64 bit |
| `arm64` | macOS Apple Silicon (M1/M2/M3/etc.) |
| `aarch64` | Linux 64-bit ARM |
| `AMD64` | Windows 64-bit x86/AMD64 |
| `ARM64` | Windows 64-bit ARM |

Platform machine is especially complicated because there isn't an exact virtual package that maps this. We can kind of use `__archspec` but that value is not specific about operating systems, but rather CPU features. So supported archspecs of `x86_64` and `aarch64` can restrict the CPU architecture, but they can't restrict the operating system.

## platform_python_implementation
This field corresponds to the interpreter. Since reroll only supports CPython, we can hardcode this knowing the version

## implementation_name
Also another name to be hardcoded as `cpython` (lowercase)

## implementation version
This has a complicated algorithm derived from [sys.implementation](https://docs.python.org/3/library/sys.html#sys.implementation)
However, for CPython, this value is always equal to the same value as python_full_version, even for beta or free threading variants
Thus, whatever applies to python_full_version applies to implementation_version

## platform_release
Returns the os release (like 10 for Windows 10). There's no mapping for this value, but it is not widely used in the pypi ecosystem.

## platform_version
This is the full build string, and also is not something that we can map here

# Marker to matchspec conversion
There is no existing tooling to take a python marker and convert it to a matchspec-compatible string. We can use py-rattler to confirm the string is valid once the transformation is complete. Thus, we need to define the mapping of marker variables ourselves. Note: This section covers the matchspec conversion as an isolated question. Due to the difficulties discovered here and otherwise, the solution of mapping e.g. `platform_system` to matchspec was dropped. The full table is presented for completeness, but some pieces need not be implemented due to other restrictions preventing the marker key from being present as input

| marker key | matchspec |
|---|---|
| `python_version == "X.Y"` | `python>=X.Y.0a0,<X.(Y+1).0a0` |
| `python_version != "X.Y"` | `python!=X.Y.*` |
| `python_version >= "X.Y"` | `python>=X.Y.0a0` |
| `python_version > "X.Y"` | `python>=X.(Y+1).0a0` |
| `python_version <= "X.Y"` | `python<X.(Y+1).0a0` |
| `python_version < "X.Y"` | `python<X.Y.0a0` |
| `python_full_version` / `implementation_version` `== "X.Y.Z"` | `python==X.Y.Z` |
| `python_full_version` / `implementation_version` `!= "X.Y.Z"` | `python!=X.Y.Z` |
| `python_full_version` / `implementation_version` `<op> "X.Y.Z"` (`>=`, `<=`, `>`, `<`) | `python<op>X.Y.Z` (passthrough) |
| `platform_system == "Linux"` / `sys_platform == "linux"` | `__linux` |
| `platform_system == "Darwin"` / `sys_platform == "darwin"` | `__osx` |
| `platform_system == "Windows"` / `sys_platform == "win32"` | `__win` |
| `os_name == "posix"` | `__unix` |
| `os_name == "nt"` | `__win` |

`python_version` is truncated to major.minor, but conda compares the full `python` version, so the ordered comparisons cannot be passed through: `python_version <= "3.9"` as `python<=3.9` would exclude 3.9.1, and `python_version > "3.9"` as `python>3.9` would wrongly include it. Every boundary is anchored at `.0a0` so that a pre-release of the boundary minor lands on the same side of the comparison that the marker would put it on (`python_version` of `3.9.0rc1` is `3.9`).


## Dealing with != with matchspec and virtual packages
Consider the two pypi markers: `os_name == "nt"` and `os_name != "nt"`
For the first case, the equivalent matchspec is `__win` (just a bare win, no qualifier).
What about the negation expression? `not __win` is not valid matchspec syntax. `__win != ???` doesn't work, either. First off, a virtual package doesn't exactly have a public version or build, but even otherwise this is the wrong question. `os_name != nt` means accept any OS besides windows. However, `__win != 1.0` (or similar) means any version of `__win` where that "package" has the version of anything besides 1.0.

What we really want is some virtual package for __os_name, so we could write a matchspec `__os_name != 'win'`
Since this doesn't exist, and bare negation (`not __win`, `!__win`) is not part of matchspec, we need a different workaround.

If we changed our mind and decided in the future to allow `os_name` as a matchspec target, this is an algorithm we could apply:
Convert `platform_system != Linux` into `__osx or __win`. This can be accomplished by having a mapping table of `platform_system` values to their corresponding virtual package names. Then, when `!= is seen` take all keys except for the one being negated, and expand the node into a collection of OR nodes with the other mapped values.

For `os_name` the pattern is the same except the mapping would be `{"posix": "__unix", "nt": "__win"}`

## Dealing with in and not in
Instead of a range, pypi markers can use in/not in semantics, such as `Requires-Dist: packageA ; python_version in "3.8 3.9 3.10"`, 
This can be solved via the same expansion concept as virtual packages above.
`packageA[when="python[version='>=3.8.0a0,<3.9.0a0|>=3.9.0a0,<3.10.0a0|>=3.10.0a0,<3.11.0a0']"]` becomes the expansion for the positive case (each member expanded per the `python_version == "X.Y"` row above), and for the negation
`Requires-Dist: packageA ; python_version not in "3.8 3.9 3.10"` turns into `packageA[when="python[version='!=3.8.*,!=3.9.*,!=3.10.*']"]`

This means we can perform this expansion, with a known univers set for `os_name`, `platform_system`, `sys_platform`, and `platform_machine`

There's still one more edge case. A valid marker can reverse the condition: `Requires-Dist: packageA ; "3.8 3.9 3.10" in python_version` For simplicity we will reject this case, and the matchspec conversion code will emit an error if it sees the "reversed" in operator



# Splitting base dependencies from extras
Conda explicitly requires splitting out dependencies into plain dependencies and extra dependencies, keyed per name. This is not a straightforward translation because extras are just another type of marker. So you can condition a dependency being part of an extra in nested boolean logic.

Markerpry determines if a dependency is allowed, given a set of environments. However, a dependency could be installed because it was part of the extra, or because it was part of the base requirements.

Concretely, given:
`Requires-Dist: packageA ; python_version < 3.9 and extra == cli`
`Requires-Dist: packageB (>=1.2.3);`

If we pass in `extra = cli` in the environment, markerpry will reduce the dependencies to
`Requires-Dist: packageA ; python_version < 3.9`
`Requires-Dist: packageB (>=1.2.3);`

and if we set `extra=''`, then markerpry will reduce the dependency to 
`Requires-Dist: packageB (>=1.2.3);`

As we can see, `packageB` is in both the base dependencies as well as `cli`.

A simple algorithm comes to mind: Solve for the base dependencies first, and then remove those dependencies from consideration when trying out `extras`. However, this simple algorithm fails with the following scenario:
`Requires-Dist: packageA (>=1.2)`
`Requires-Dist: packageA (>=2.0) ; extra == major_bump`

Besides cases with the same package name, here's a fun scenario:
`Requires-Dist: packageA ; python_version < 3.9 and extra != cli`

`uv`'s behavior is to solve the markers twice - once for the base environment, and once for the extra environment. It then unions the requirements, so in this case packageA would get installed for the base environment. If the union produces conflicting requirements, then the solver will fail

The wheel repodata CEP proposes unioning the base dependencies and the selected extra dependencies. So, there is no solver harm to including the packages twice. It is just a matter of neatness (and smaller repodata).

Thus, we can get away with a post-processing step which de-duplicates extra packages by exact string matching of the matchspec. If two matchspecs are equivalent but not str-output identical, it's not a correctness issue since the union of both fields will end up reducing to the same requirement. The only downside is parsing markers more than potentially needed. However, with the repeated-package-name scenario, it's a non-trivial optimization. It's better to err on the side of correctness, as opposed to a small recalculating branch that otherwise could be optimized

# Repeated dependency names
Requires-Dist can have multiple entries of the same dependency name. Additionally, since we are performing a mapping to conda names, even dependencies with different pypi names could have the same conda package name (in a scenario where one conda package satisfies different pypi names). To decide what the correct behavior is, we need to determine if the package managers take the intersection of dependencies, or if dependencies shadow/overwrite a previous dependency with the same name.

Luckily, all package managers behave the same here. uv, pip, and rattler all take the intersection of dependencies, regardless of whether the dependency shares the same name. For reroll, this is ideal since we don't need to worry about edge cases of emitting multiple dependencies with the same name. It will go to the solver, and the solver require satisfying all constraints.
