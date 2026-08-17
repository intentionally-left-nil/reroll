# Decisions
* Dependency generation is intended to be compatible between both conda-forge and main. Features (like python_abi support) are only used in scenarios where both channels support it
* The conda ecosystem doesn't prevent ABI breakages of e.g. pypy before `python_abi` was introduced, so the wheel work will not try to prevent ABI breakages when `python_abi` is unsupported
* Expand out abi3 and abi3t into multiple repodata entries, rather than relying on `python-gil`
* Reroll must silently strip out (but not error) any `Requires-Dist: Python ...` dependency (direct dependency on python, not a marker) for python and all related interpreters (python, cpython, pypy, graalpy)
* `Requires-Dist` dependencies which contain local tags, direct URL's, or pre-release tags are invalid. Any invalid tags raise `UnconvertableRequirementError`.
* Provides-Extra is completely ignored. Extras for a package are determined by accumulating and normalizing the extra names from `Requires-Dist` only
* All dependency calculations must follow the [Calculating Extras](#calculating-extras) algorithm, including base dependencies (which would have extras="")
* Dependencies generated for an extra may include dependencies that also apply to the base depends. These duplicates will be removed as a post-processing step once all dependencies have been calculated
* If a noarch package contains arch-specific dependency markers, a noarch wheel record will not be emitted. Instead, a wheel record for every supported subdir (`osx-64`, `osx-arm64`, `win-64`, `win-arm64`, `linux-64`, `linux-aarch64`) will be emitted in its place. Attempts to preserve noarch wheels using virtual packages (__win, etc) run into complex challenges with `!=` and `platform_machine` -> `__archspec` conversion
* See [matchspec.md](./matchspec.md) for decisions about converting a pypi name/operator/version/extra/marker into a MatchSpec


# Calculating the conda specifiers for python
Since wheels were built and installed in the pypi ecosystem, we generally want the conda equivalent to match the real-world environments that pypi would have created.

For the python dependency, this means that the wheel filename (including the python version AND the ABI), `Requires-Python` all feed into what actually would have been installable. Our conda algorithm will try to combine these sources in a way that keeps our matching environments similar in spirit to wheels.

Our algorithm for converting wheel information into conda `depends` is:
1. Determine if the python/abi combo requires an exact minor version of python, or a floor of python, following the [wheel_filename.md](./wheel_filename.md#python-tag) table
2. If the wheel metada contains `Requires-Python`, intersect this specifier (if feasible) with the filename range. If the intersection is solvable, tighten the range to the intersection of both values. See [Determining the python version dependency](#Determining-the-python-version-dependency) for specific details
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
1. Apply the [python_full_version / implementation_version reduction algorithm](./matchspec.md#reducing-python_full_version--implementation_version-to-python_version) to determine whether `python_full_version` and/or `implementation_version` should be removed from the base environment
2. Add the appropriate `extra` value to the base environment, depending on what extra is being targeted (see [Calculating Extras](#calculating-extras))
3. Use markerpry to evaluate the marker. The return is called a `partial_evaluation` (even though the eval might be true/false)
4. If `partial_evaluation` evaluates to true, add the simple dependency/version without any markers
5. If `partial_evaluation` evaluates to false, return (no dependency should be added)
6. If the environment type is noarch, check to see if any of the following keys exist: `platform_system`, `platform_machine`, `sys_platform`, `os_name`. If so, the repodata must be emitted as arch-specific instead. The individual parsing should stop, and control reverted back to the overall algorithm to switch to per-arch packages.
7. Otherwise, check for any unpermitted keys in the tree, besides these: `python_version`, `python_full_version`, `implementation_version`. If any unpermitted keys are present, the dependency cannot be generated and the wheel should not be emitted.
8. Convert the remaining tree to a matchspec string, per [matchspec.md's Marker to matchspec conversion](./matchspec.md#marker-to-matchspec-conversion)

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
platform_machine: `x86_64`, `AMD64`, `arm64`, `aarch64`, or `ARM64` (see [matchspec.md's platform_machine](./matchspec.md#platform_machine) table)
sys_platform: `linux`, `darwin`, or `win32`
os_name: `posix` or `nt`

## Wheel is pure python (interpreter and ABI independent)
This is the easy case, just copy the tightened version rangne as a matchspec into depends, e.g. `python>=3.8`
If the wheel requires an exact minor version, or any funky tightening, don't normalize the range any further. Just use the tightened range as-is. That is about the range's meaning, not its spelling - an exact minor arrives as `==3.13.*` and is emitted as the equivalent range `python>=3.13,<3.14.0a0`, not the fuzzy `=3.13` form from [matchspec.md's Operator conversion](./matchspec.md#operator-conversion), so that the `0a0` upper bound excludes 3.14 pre-releases.

## Wheel is a compiled wheel for a specific CPython GIL, minor version less than 3.10

Although conda-forge emits python_abi packages all the way down to 2.x, Anaconda's main channel does not emit below 3.10, so we need to fallback to the old way of describing dependencies. Additionally, free threaded builds don't exist before 3.13, so we will not emit python_abi below that point (and should ignore this invalid combo to begin with)

A consequence is this will allow e.g. `pypy` to install, but as per the decisions, that's acceptable and congruent with how it works on main today.

1. Emit a python dependency for that minor version, e.g. `"python >=3.7,<3.8.0a0"` - Whatever the incoming python range is should be fine to use as-is
2. Don't emit an ABI tag, and don't do extra validation the ABI tag matches - this is the responsibility of the filename parsing layer (aka `check_interpreter_abi`)

## Wheel is a compiled wheel for a specific CPython GIL, minor version >= 3.10

For these wheels, we can additionall emit the correct python_abi to constrain solves to just CPython. Per filename restrictions, we only support CPython-based wheels, so we can simplify the logic here:

1. Emit a python dependency for that minor version, e.g. `"python >=3.7,<3.8.0a0"` - Whatever the incoming python range is should be fine to use as-is
2. Take the ABI tag, and from that version e.g. `cp313`, derive the `python_abi` Matchspec combining a version plus build string e.g. `"python_abi 3.13.* *_cp313"`

## Wheel targets CPython but no abi
`cp37-none-any` would be an example package. Since the version of python must be that minor version (and not a floor), we can treat this like `cp37-cp37` with respect to determining the python range.
1. Use the python version as the ABI tag (e.g. `cp37`)
2. Follow the existing rules for handling a paired cPython/ABI (e.g. `cp37-cp37`)


## Wheel is a compiled wheel for a specific CPython free threaded version
All free threaded builds are >= 3.13, so this is just a small modification to the normal GIL case.
1. Emit a python dependency for that minor version, e.g. `"python >=3.7,<3.8.0a0"` - Whatever the incoming python range is should be fine to use as-is
2. Take the ABI tag, and from that version e.g. `cp313t`, derive the `python_abi` Matchspec combining a version plus build string e.g. `"python_abi 3.13.* *_cp313t"` (note the `t` at the end)

## Wheel is a compiled wheel for a CPython floor, and abi3 or abi3t
This is an error. The [wheel_filename](./wheel_filename.md#abi-explosion) docs specifically solve this problem by exploding out the value of python to a specific version, because the dependencies cannot be expressed in conda otherwise. If the dependency generation code sees `abi3` or `abi3t` it should error out as this is reroll's bug, not a caller bug or wheel issue

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

# Determining the python version dependency
The python version is an intersection of the requirements from the filename (either an exact major.minor, or a major.minor floor), and `Requires-Python`. `Requires-Python` can be any arbitrary SpecifierSet, but typically it is a simple range evaluator like `>=3.11`

On the PEP side, we can correctly express the combined dependency with converting the filename requirement into a specifier (either >= major.minor or ~= major.minor), and then combining that with Requires-Python with a comma. So `py311-none-any` with `Requires-Python: !=3.11.2,>=3.10,<4` becomes `>=3.11,!=3.11.2,>=3.10,<4` and then it is up to the matchspec code to convert this to a proper matchspec equivalent later.

However, the _common_ case for Requires-Python is to have a simple SpecifierSet which produces a simple contiguous range (perhaps unbounded on one end), such as `Requires-Python: >=3.11`. Although we could produce the combined specifier of `>=3.11,>=3.11` (again with `py11-none-any` as the hypothetical), this is redundant and also makes the specifier visually unpleasing to read. Additionally, we want to detect simple cases where the Requires-Python range is wholly disjoint with the filename specifier, so we can raise `PythonRangeMismatchError`

Therefore, we can first have an algorithm for "Simplified Requires-Python" specifiers which match a reduced grammar (accounting for the vast majority of in-the-wild wheels), perform range-caluclation and simplification logic, and emit a unified python dependency (or raise if the range is disjoint). Other wheels which do not have a Simplified Requires-Python are parsed with the generic combining-logic above, and will never raise a `PythonRangeMismatchError`

## Simplified Requires-Python requirements
A simplified Requires-Python Specifier must fit into one of the following categories
* A single `>=`, `<`, or `~=` operator (e.g. `>=3.11`)
* An `==` clause where the value is major.minor, (e.g. `==3.11`, but not `==3.11.2`)
* An `==` clause where the value is major.* or major.minor.* (e.g. `3.*` or `==3.11.*`)
* A half-open range, formed by a clause with ONE comma, where one expression is `<` and the other expression is `>=`.

Items which cannot be trivially reduced to a half-open range are out of scope for a simplified Requires-Python. It's important to note that `Requires-Python: >=3.10,<3.7` is a Simplified Requires-Python half-open range. It should proceed through the rest of the simplified algorithm, at which it will reject for being a disjoint range, and `PythonRangeMismatchError` will be thrown

## Simplified Requires-Python algorithm
Once a specifier is confirmed to be a simplified specifier, perform the following algorithm:

1. Generate the corresponding half-open range for the specifier
    1. Convert bare inequality operators into a half-open range: `>=3.11` becomes `[3.11, None)`, `<3.4` becomes `[None, 3.4)`
    2. Convert ~= into a bounded half-open range. For example: `~=3.11.2` becomes `[3.11.2, 3.12)` 
    3. Convert a simple compound clause to the corresponding half-open range: `>=3.11,<3.14.2` becomes `[3.11, 3.14.2)` Note that these are PEP ranges, nothing to do with matchspec. Matchspec boundary checking (e.g. <3.11.0a0) happens much later when transforming to conda
    4. Treat `== major.minor` or `==major.minor.*` as `>=major.minor,<major.minor+1` and perform the simple compound clause transformation
    6. Treat `==major.*` as `>= major`, and output `[major.0, None)`
2. Generate the corresponding half-open range for the filename version
    1. An exact minor gets converted into major/minor, major/minor+1: `3.13` becomes `[3.13, 3.14)`
    2. A minor floor has None as the upper range: `3.13` (floor) becomes `[3.13, None)`
3. Determine the combined lower floor by taking `max(python_requires[0], filename_requires[0])`  where None always loses
4. Determine the combined upper floor by taking `min(python_requires[1], filename_requires[1])` where None always loses
5. Determine if the combined range is disjoint, where lower >= upper. If so, raise `PythonRangeMismatchError` (lower == upper is still disjoint since the range is half-open)
6. Use the combined range as necessary, for example converting back to a PEP specifier `>=3.11,<3.13` or `>=3.11` as appropriate

Note that future downstream parts of the codebase may need to know whether a range is restricted to a particular minor version. The combined-range is appropriate for those decisions when it exists. It is up to other callers to decide what to do when the computed python version did not go through the simplifying algorithm.

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
The main format of a simple dependency is name-operator-version. Dependency names are converted from pypi names to conda names using the same conversion mechanism as the main filename. Failures to convert raise `UnresolvedCondaNameError`.

See [matchspec.md](./matchspec.md) for how the operator and version parts of a dependency convert to MatchSpec syntax.

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

See [matchspec.md's Extras](./matchspec.md#extras) for how an extra name is normalized, and how `Requires-Dist: fastapi[all]` becomes a MatchSpec.

# Conditional markers
See [matchspec.md's Conditional markers](./matchspec.md#conditional-markers) for what each environment marker variable means, and [matchspec.md's Marker to matchspec conversion](./matchspec.md#marker-to-matchspec-conversion) for which ones can convert to a MatchSpec. This section covers how reroll computes each variable's value.

Reroll supports subdir-specific repodata, and we can use this information (along with other known restrictions like the python version) to clean up the environment markers before converting them to conda.

## python_version
We can take our algorithm to constrain the wheel's python version (see [Calculating the conda specifiers for python](#calculating-the-conda-specifiers-for-python)), and check to see if that range applies exactly to a minor range. If the check matches, then we can set `python_version` to the specific minor version

## platform_system
This can be determined by the subdir the repodata is targeting.

## platform_python_implementation / implementation_name
Since reroll only supports CPython, we can hardcode both of these knowing the version (`CPython` and `cpython` respectively, per the base environments above)

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
