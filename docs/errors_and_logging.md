# Error categories

Failures with reroll to generate a wheel fall into the following distinct categories
1. Reroll scope constraints
2. Invalid wheel data
3. Unconvertable wheels
4. Runtime issues (downloading data, caches)

It is important to group these types together, because these broad categories guide callers for how to handle such errors.

## Reroll scope constraints
Some wheels are completely valid, but reroll has chosen not to support them at the present moment. Reroll is aimed at modern usage, and sometimes deliberately choosing to not support older use cases significantly lowers the development and test cost.

The primary reroll scope constraints are:
* The wheel must work for python >= 3.4
* The wheel must support CPython (not pypy or other interpreters)

If callers receive a failure from this bucket, the only real recourse is to open a bug and improve the reroll source code to enable this support.

## Invalid wheel data
The wheel input includes both the filename, as well as the METADATA. If either of these two fields cannot be parsed according to reroll's criteria, an error is returned.

This category is slightly mushy because what is valid is not always clear. First off, the spec for filenames and metadata has changed over the years. So a wheel could have had valid METADATA when it was uploaded, but for that wheel to be out-of-spec. For METADATA specifically, there's also a Metadata-Version.

For metadata specifically, reroll attempts to follow uv's `LenientRequirement` mechanism to patch up an invalid requirement for common formatting issues or older specs. So, for wheel metadata "if uv can read it" is roughly an inspiration (but not a reality) for the bar reroll is trying to keep.

Some other examples have to do with filenames. As far as I can tell, for example, `cp3-none` and `cp3-abi3` are valid python/abi tag combos according to the PEP specs. However, cp3 support was removed from pip at one point.


If callers receive an error in the invalid wheel category, they should know that it's generally the wheel's fault. If callers have their own logic, they can provide their own errata fixups to solve for the case and bring the wheel into compliance

## Unconvertable wheels
PEP's, CEP's, pip, uv, and conda don't neatly map to one another. Reroll's default behavior is to produce a semantically equivalent conda repodata for a given wheel. If it cannot, reroll generally does not make assumptions, or produce a less-strict requirement. Instead, it returns an Unconvertable wheel category error. This gives callers immediate visibility into problems, rather than silently eating errors. Callers can take part in the parsing and error-recovery chain in order to manipulate the outcome if desired.

## Runtime issues
Reroll relies on downloading files from the internet, loading cache files from disk, and parsing locally manipulated databases. If these operations fail, a Runtime Issue category error is raised. These errors should generally stop batch processing of jobs until the underlying host environment is stable

# Logging
Because reroll is meant to run at scale for many wheels, we want to provide callers the flexibility of what is logged, in order to balance the log volume with the amount of information needed.

Reroll will use the standard logger mechanisms to allow callers easy control over the log levels and outputs.

Debug - aimed at reroll developers to identify and fix bugs
Info - Used to report codepaths, fixups, and other non-standard paths that a wheel took to be converted. For example, which dependencies had names converted from pypi to conda (and from which data source). Additionally, Reroll scope contstraint category errors are emitted at this level. Any wheel failure gets an info-level message explaining why a wheel was skipped
Warning - These are issues that affect a smaller subset of wheels that callers might be more inclined to provide errata for. Invalid wheel data and Unconvertable wheels are logged at this level - with verbose details at higher log levels
Error - Used for runtime errors or other systemic failures. General failure to parse wheels are not emitted at this level

In addition to the global reroll logger, reroll will also have category-level loggers which roll up into the global logger. That way callers can individually tweak log levels for different categories e.g. `logging.getLogger("reroll.unconvertable").setLevel(logging.INFO)`, redirect to a specific file or more.

# Errors
All errors follow a hierarchy of RerollError -> RerollScopeError/RerollInvalidWheelError/RerollUnconvertableError/RerollRuntimeError -> underlying error.

Instead of swallowing errors and emitting default values (skip a dependency, skip a wheel etc), it is expected that Reroll emits errors to the caller. Callers can catch RerollError (all errors), or they can catch e.g. just RerollScopeError and let the rest pass through.
