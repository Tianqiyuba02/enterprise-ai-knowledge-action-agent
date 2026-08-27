from datetime import date, timedelta
from pathlib import Path

import pytest

from app.agent.relative_weekday import (
    WEEKDAY_NAMES,
    dates_match_resolved_weekdays,
    parse_next_weekdays,
    resolve_next_weekday,
    resolve_request_next_weekdays,
    trusted_relative_weekday_context,
)
from app.knowledge.clock import MelbourneClock

_PRODUCTION_PATHS = (
    Path("src/app/agent/relative_weekday.py"),
    Path("src/app/agent/service.py"),
    Path("src/app/agent/client.py"),
)


def test_grammar_recognizes_all_seven_weekdays_case_insensitively() -> None:
    message = (
        "Please prepare next Monday, then NEXT tuesday, next Wednesday, "
        "next Thursday, next Friday, next Saturday, and next Sunday."
    )

    assert parse_next_weekdays(message) == WEEKDAY_NAMES


def test_grammar_accepts_reasonable_punctuation_without_open_ended_dates() -> None:
    assert parse_next_weekdays("Book next Friday.") == ("friday",)
    assert parse_next_weekdays("Book next Friday!") == ("friday",)
    assert parse_next_weekdays('Use "next Monday" please.') == ("monday",)
    assert parse_next_weekdays("next   Thursday") == ("thursday",)


@pytest.mark.parametrize(
    "phrase",
    [
        "sometime next week",
        "end of next month",
        "after the holidays",
        "next week Friday",
        "Friday next",
        "next Fridayish",
        "the following Friday",
    ],
)
def test_unsupported_natural_language_dates_are_not_resolved(phrase: str) -> None:
    today = date(2024, 1, 3)

    assert parse_next_weekdays(phrase) == ()
    assert resolve_request_next_weekdays(phrase, today) == ()
    assert trusted_relative_weekday_context(()) == ""
    assert dates_match_resolved_weekdays(today, today, ()) is True


@pytest.mark.parametrize("weekday_index, weekday", list(enumerate(WEEKDAY_NAMES)))
def test_today_before_target_weekday_resolves_next_occurrence(
    weekday_index: int, weekday: str
) -> None:
    today = date(2024, 1, 1) + timedelta(days=(weekday_index - 1) % 7)

    resolved = resolve_next_weekday(today, weekday)

    assert today.weekday() == (weekday_index - 1) % 7
    assert resolved == today + timedelta(days=1)
    assert resolved.weekday() == weekday_index
    assert resolved > today


@pytest.mark.parametrize("weekday_index, weekday", list(enumerate(WEEKDAY_NAMES)))
def test_today_equals_target_weekday_resolves_seven_days_later(
    weekday_index: int, weekday: str
) -> None:
    today = date(2024, 1, 1) + timedelta(days=weekday_index)

    resolved = resolve_next_weekday(today, weekday)

    assert today.weekday() == weekday_index
    assert resolved == today + timedelta(days=7)
    assert resolved.weekday() == weekday_index


def test_week_rollover_uses_the_following_week() -> None:
    saturday = date(2024, 1, 6)
    sunday = date(2024, 1, 7)

    assert saturday.weekday() == 5
    assert resolve_next_weekday(saturday, "monday") == date(2024, 1, 8)
    assert resolve_next_weekday(saturday, "sunday") == date(2024, 1, 7)
    assert resolve_next_weekday(sunday, "monday") == date(2024, 1, 8)
    assert resolve_next_weekday(sunday, "saturday") == date(2024, 1, 13)


def test_resolver_uses_the_supplied_trusted_melbourne_date_not_the_system_clock() -> None:
    trusted_today = date(2024, 3, 13)
    assert trusted_today.weekday() == 2
    system_today = MelbourneClock().today()

    resolved = resolve_request_next_weekdays("Prepare leave for next Friday.", trusted_today)

    assert resolved == (("friday", date(2024, 3, 15)),)
    assert resolved[0][1] != system_today
    assert trusted_relative_weekday_context(resolved) == (
        "Trusted relative-weekday resolution for this request:\n- next friday = 2024-03-15"
    )


def test_compatible_and_incompatible_iso_dates_are_checked_against_resolutions() -> None:
    resolutions = resolve_request_next_weekdays(
        "Prepare leave for next Wednesday.", date(2024, 1, 1)
    )

    assert dates_match_resolved_weekdays(date(2024, 1, 3), date(2024, 1, 3), resolutions)
    assert not dates_match_resolved_weekdays(date(2024, 1, 10), date(2024, 1, 10), resolutions)


def test_production_resolver_has_no_hard_coded_friday_or_calendar_dates() -> None:
    combined = "\n".join(path.read_text() for path in _PRODUCTION_PATHS)

    assert "2026-08-28" not in combined
    assert "dev_agent_prepare_next_friday" not in combined
    assert WEEKDAY_NAMES == (
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    )
    assert resolve_next_weekday.__code__.co_consts is not None
    friday_delta = resolve_next_weekday(date(2024, 1, 3), "friday")
    monday_delta = resolve_next_weekday(date(2024, 1, 3), "monday")
    assert (friday_delta - date(2024, 1, 3)).days == 2
    assert (monday_delta - date(2024, 1, 3)).days == 5
