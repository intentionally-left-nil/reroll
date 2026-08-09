"""Map a wheel's license metadata to a canonical SPDX expression."""

from __future__ import annotations

from packaging.licenses import InvalidLicenseExpression, canonicalize_license_expression

from reroll.wheel_metadata import WheelMetadata


def convert_license(metadata: WheelMetadata) -> str | None:
    """The SPDX license expression for repodata.json's `license` field, or
    `None` when nothing unambiguous can be determined.

    Precedence: `metadata.license_expression` (already validated) verbatim;
    else the single SPDX id shared by `metadata.license_classifiers`; else
    `metadata.license` if it parses as a valid SPDX expression. Ambiguous or
    unparseable input yields `None` rather than a guess.
    """
    if metadata.license_expression is not None:
        return metadata.license_expression
    from_classifiers = _license_from_classifiers(metadata.license_classifiers)
    if from_classifiers is not None:
        return from_classifiers
    return _license_from_legacy_text(metadata.license)


_OSI_APPROVED_CLASSIFIER_PREFIX = "License :: OSI Approved :: "

_SPDX_ID_BY_CLASSIFIER_SUFFIX = {
    "MIT License": "mit",
    "Apache Software License": "apache-2.0",
    "GNU General Public License v2 (GPLv2)": "gpl-2.0-only",
    "GNU General Public License v2 or later (GPLv2+)": "gpl-2.0-or-later",
    "GNU General Public License v3 (GPLv3)": "gpl-3.0-only",
    "GNU General Public License v3 or later (GPLv3+)": "gpl-3.0-or-later",
    "GNU Lesser General Public License v2 (LGPLv2)": "lgpl-2.0-only",
    "GNU Lesser General Public License v2 or later (LGPLv2+)": "lgpl-2.0-or-later",
    "GNU Lesser General Public License v3 (LGPLv3)": "lgpl-3.0-only",
    "GNU Lesser General Public License v3 or later (LGPLv3+)": "lgpl-3.0-or-later",
    "GNU Affero General Public License v3": "agpl-3.0-only",
    "GNU Affero General Public License v3 or later (AGPLv3+)": "agpl-3.0-or-later",
    "Mozilla Public License 1.0 (MPL)": "mpl-1.0",
    "Mozilla Public License 1.1 (MPL 1.1)": "mpl-1.1",
    "Mozilla Public License 2.0 (MPL 2.0)": "mpl-2.0",
    "Eclipse Public License 1.0 (EPL-1.0)": "epl-1.0",
    "Eclipse Public License 2.0 (EPL-2.0)": "epl-2.0",
    "European Union Public Licence 1.0 (EUPL 1.0)": "eupl-1.0",
    "European Union Public Licence 1.1 (EUPL 1.1)": "eupl-1.1",
    "European Union Public Licence 1.2 (EUPL 1.2)": "eupl-1.2",
    "Python Software Foundation License": "psf-2.0",
    "ISC License (ISCL)": "isc",
    "zlib/libpng License": "zlib",
    "Python License (CNRI Python License)": "cnri-python",
}
"""Canonical trove classifier suffixes (after `_OSI_APPROVED_CLASSIFIER_PREFIX`)
with a single unambiguous SPDX equivalent, keyed to that id in whatever
case `packaging.licenses` accepts -- `_SPDX_FROM_CLASSIFIER` below runs each
through `canonicalize_license_expression` to get its exact canonical form.

Deliberately excludes classifiers that cover more than one SPDX license (e.g.
`BSD License`, `Artistic License`) or that have no SPDX equivalent at all
(e.g. `Public Domain`, `Other/Proprietary License`) -- those fall through as
unmapped rather than risk guessing.
"""

_SPDX_FROM_CLASSIFIER = {
    _OSI_APPROVED_CLASSIFIER_PREFIX + suffix: str(canonicalize_license_expression(spdx_id))
    for suffix, spdx_id in _SPDX_ID_BY_CLASSIFIER_SUFFIX.items()
}
"""Full classifier string -> its canonical SPDX id."""


def _license_from_classifiers(classifiers: tuple[str, ...]) -> str | None:
    """The single SPDX id shared by every mappable entry in `classifiers`,
    or `None` when none map or the mapped ones disagree. Unmapped entries
    are skipped rather than counted as ambiguity.
    """
    spdx_ids = {
        _SPDX_FROM_CLASSIFIER[classifier]
        for classifier in classifiers
        if classifier in _SPDX_FROM_CLASSIFIER
    }
    if len(spdx_ids) == 1:
        return next(iter(spdx_ids))
    return None


def _license_from_legacy_text(license: str | None) -> str | None:
    """`license` canonicalized as a strict SPDX expression, or `None` when
    it is absent or fails SPDX validation.
    """
    if license is None:
        return None
    try:
        return str(canonicalize_license_expression(license))
    except InvalidLicenseExpression:
        return None
