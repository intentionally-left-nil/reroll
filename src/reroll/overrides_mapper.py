"""A hand-maintained table of PyPI -> conda name corrections.

Entries here are real-world cases where the combined mapper chain (grayskull,
conda-lock, parselmouth) cannot agree on a high confidence conda name

Incorrect conda mappings are out of scope (at least for now). Incorrect mappings
should be fixed in the upstream packages
"""

from __future__ import annotations

from reroll.name_mapping import NameMapper, static_mapper

_TABLE = {
    "onnxruntime": "onnxruntime",
    # conda-forge dispatches GPU via build variant, not a separate name.
    "onnxruntime-gpu": "onnxruntime",
    "modal": "modal-client",
    "pyqtwebengine": "pyqtwebengine",
    "dspy": "dspy",
    "scikit-learn-intelex": "scikit-learn-intelex",
    "mdahole2": "mdahole2",
    "mathicsscript": "mathics3-frontend-cli",
    "pylibiio": "pylibiio",
    "pyplaid": "plaid",
    "simhash-py": "simhash-py",
    "grip-nulling": "grip-nulling",
    "functools32": "functools32",
    "pyqtchart": "pyqtchart",
}


def overrides_mapper() -> NameMapper:
    """Build a `NameMapper` from `_TABLE`.

    A hit returns a `Winner`, ending the chain immediately -- same as any
    other static override table (`reroll.name_mapping.static_mapper`),
    attributed to `"overrides_mapper"` rather than the generic
    `static_mapper` default. A miss returns `candidates` unchanged.
    """
    return static_mapper(_TABLE, mapper_name="overrides_mapper")
