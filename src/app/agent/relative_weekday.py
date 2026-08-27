"""Request-scoped next-weekday grammar resolved from the trusted Melbourne date."""

import re
from datetime import date, timedelta
from typing import Final

WEEKDAY_NAMES: Final = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

RelativeWeekdayResolution = tuple[str, date]

_WEEKDAY_INDEX: Final = {name: index for index, name in enumerate(WEEKDAY_NAMES)}
_NEXT_WEEKDAY = re.compile(
    r"\bnext\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)


def parse_next_weekdays(message: str) -> tuple[str, ...]:
    """Return first-seen supported weekday names from the product grammar."""

    found: list[str] = []
    for match in _NEXT_WEEKDAY.finditer(message):
        name = match.group(1).lower()
        if name not in found:
            found.append(name)
    return tuple(found)


def resolve_next_weekday(trusted_today: date, weekday: str) -> date:
    """Return the first occurrence of weekday strictly after trusted_today."""

    target = _WEEKDAY_INDEX[weekday]
    delta = (target - trusted_today.weekday()) % 7
    if delta == 0:
        delta = 7
    return trusted_today + timedelta(days=delta)


def resolve_request_next_weekdays(
    message: str,
    trusted_today: date,
) -> tuple[RelativeWeekdayResolution, ...]:
    return tuple(
        (name, resolve_next_weekday(trusted_today, name)) for name in parse_next_weekdays(message)
    )


def trusted_relative_weekday_context(
    resolutions: tuple[RelativeWeekdayResolution, ...],
) -> str:
    if not resolutions:
        return ""
    lines = ["Trusted relative-weekday resolution for this request:"]
    lines.extend(f"- next {name} = {resolved.isoformat()}" for name, resolved in resolutions)
    return "\n".join(lines)


def dates_match_resolved_weekdays(
    start_date: date,
    end_date: date,
    resolutions: tuple[RelativeWeekdayResolution, ...],
) -> bool:
    if not resolutions:
        return True
    allowed = {resolved for _name, resolved in resolutions}
    return start_date in allowed and end_date in allowed
