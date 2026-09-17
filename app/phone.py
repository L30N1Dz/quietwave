"""E.164 normalisation.

Deliberately small: this app talks to a handful of known North American
numbers, so a full libphonenumber dependency would be dead weight. Anything
already in E.164 is passed through untouched, so international numbers still
work as long as the user types the + themselves.
"""

from __future__ import annotations

import re

_DIGITS = re.compile(r"\d+")
_E164 = re.compile(r"^\+[1-9]\d{6,14}$")


class InvalidNumber(ValueError):
    """Raised when a string cannot be read as a phone number."""


def normalise(raw: str, default_country_code: str = "1") -> str:
    """Return `raw` as E.164, e.g. '(555) 123-4567' -> '+15551234567'.

    Raises InvalidNumber if the input cannot plausibly be a phone number.
    """
    if raw is None:
        raise InvalidNumber("no number given")
    value = raw.strip()
    if not value:
        raise InvalidNumber("no number given")

    # Already E.164 -- trust it, minus incidental spacing.
    compact = re.sub(r"[\s\-().]", "", value)
    if compact.startswith("+"):
        if not _E164.match(compact):
            raise InvalidNumber(f"{raw!r} is not a valid E.164 number")
        return compact

    digits = "".join(_DIGITS.findall(compact))
    if not digits:
        raise InvalidNumber(f"{raw!r} contains no digits")

    cc = default_country_code.lstrip("+")

    # A bare NANP 10-digit number, or 11 digits already led by the country code.
    if len(digits) == 10:
        candidate = f"+{cc}{digits}"
    elif len(digits) == 11 and digits.startswith(cc):
        candidate = f"+{digits}"
    elif digits.startswith("011"):  # NANP international dialling prefix
        candidate = f"+{digits[3:]}"
    else:
        candidate = f"+{digits}"

    if not _E164.match(candidate):
        raise InvalidNumber(f"{raw!r} is not a valid phone number")
    return candidate


def pretty(e164: str) -> str:
    """Human-readable form for display: '+15551234567' -> '(555) 123-4567'."""
    if not e164 or not e164.startswith("+"):
        return e164 or ""
    digits = e164[1:]
    if len(digits) == 11 and digits.startswith("1"):
        d = digits[1:]
        return f"({d[0:3]}) {d[3:6]}-{d[6:10]}"
    return e164
