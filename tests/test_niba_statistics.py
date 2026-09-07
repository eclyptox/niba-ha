"""Tests for turning Niba's hourly readings into recorder statistics."""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import pairwise

from custom_components.niba.api import parse_consumption_daily
from custom_components.niba.statistics import (
    METER_TZ,
    build_statistics,
    statistic_ids,
)


def _day(date: str, hours: list[tuple[str, float, float]]) -> dict:
    return {
        "date": date,
        "total_active_value": sum(h[1] for h in hours),
        "total_out_active_value": sum(h[2] for h in hours),
        "hour_measurements": [
            {
                "hour": hour,
                "active_value": active,
                "out_active_value": out,
                "reactive_value": 0.0,
                "unit": "kWh",
            }
            for hour, active, out in hours
        ],
    }


def test_statistic_ids_are_namespaced_per_cups() -> None:
    consumption, export = statistic_ids("ES0021000011349260ME")

    assert consumption == "niba:es0021000011349260me_energy_consumption"
    assert export == "niba:es0021000011349260me_energy_export"


def test_hourly_rows_carry_a_running_sum() -> None:
    days = parse_consumption_daily(
        [_day("2026-08-01", [("00:00:00", 1.5, 0.0), ("01:00:00", 2.5, 0.5)])]
    )

    consumption, export, con_sum, exp_sum = build_statistics(days, 0.0, 0.0)

    assert [row["state"] for row in consumption] == [1.5, 2.5]
    assert [row["sum"] for row in consumption] == [1.5, 4.0]
    assert [row["sum"] for row in export] == [0.0, 0.5]
    assert con_sum == 4.0
    assert exp_sum == 0.5


def test_running_sum_continues_from_a_previous_import() -> None:
    days = parse_consumption_daily([_day("2026-08-02", [("00:00:00", 1.0, 0.25)])])

    consumption, export, _, _ = build_statistics(days, 100.0, 50.0)

    assert consumption[0]["sum"] == 101.0
    assert export[0]["sum"] == 50.25


def test_slots_are_anchored_to_local_meter_time() -> None:
    """August is CEST (UTC+2), so 00:00 local is 22:00 UTC the day before."""

    days = parse_consumption_daily([_day("2026-08-01", [("00:00:00", 1.0, 0.0)])])

    consumption, _, _, _ = build_statistics(days, 0.0, 0.0)

    assert consumption[0]["start"] == datetime(2026, 7, 31, 22, 0, tzinfo=UTC)


def test_winter_slots_use_the_winter_offset() -> None:
    """January is CET (UTC+1)."""

    days = parse_consumption_daily([_day("2026-01-15", [("00:00:00", 1.0, 0.0)])])

    consumption, _, _, _ = build_statistics(days, 0.0, 0.0)

    assert consumption[0]["start"] == datetime(2026, 1, 14, 23, 0, tzinfo=UTC)


def test_spring_forward_day_has_no_gap_in_the_sum() -> None:
    """2026-03-29 comes back with 23 readings; every one must be kept."""

    hours = [(f"{hour:02d}:00:00", 1.0, 0.0) for hour in range(24) if hour != 2]
    days = parse_consumption_daily([_day("2026-03-29", hours)])

    consumption, _, con_sum, _ = build_statistics(days, 0.0, 0.0)

    assert len(consumption) == 23
    assert con_sum == 23.0
    assert len({row["start"] for row in consumption}) == 23


def test_fall_back_day_keeps_all_twenty_five_readings() -> None:
    """The October DST day sends 25 readings; none may be dropped."""

    hours = [(f"{h:02d}:00:00", 1.0, 0.0) for h in range(24)]
    hours.append(("02:00:00", 1.0, 0.0))  # the repeated local hour
    days = parse_consumption_daily([_day("2026-10-25", hours)])

    consumption, _, con_sum, _ = build_statistics(days, 0.0, 0.0)

    assert len(consumption) == 25
    assert con_sum == 25.0
    assert len({row["start"] for row in consumption}) == 25


def test_hourly_rows_always_add_up_to_the_daily_total() -> None:
    """The invariant that matters: no energy invented, none lost."""

    for date_, count in (("2026-03-29", 23), ("2026-06-15", 24), ("2026-10-25", 25)):
        hours = [(f"{i:02d}:30:00", 0.5, 0.25) for i in range(count)]
        days = parse_consumption_daily([_day(date_, hours)])

        consumption, _, con_sum, exp_sum = build_statistics(days, 0.0, 0.0)

        assert len(consumption) == count, date_
        assert con_sum == days[0].total_active_value, date_
        assert exp_sum == days[0].total_out_active_value, date_


def test_slots_stay_consecutive_across_a_dst_change() -> None:
    """Slots are one UTC hour apart even on the day a local hour vanishes."""

    hours = [(f"{h:02d}:00:00", 1.0, 0.0) for h in range(24) if h != 1]
    days = parse_consumption_daily([_day("2026-03-29", hours)])

    consumption, _, _, _ = build_statistics(days, 0.0, 0.0)
    starts = [row["start"] for row in consumption]

    gaps = {(b - a).total_seconds() for a, b in pairwise(starts)}
    assert gaps == {3600.0}


def test_days_are_ordered_chronologically_whatever_niba_sends() -> None:
    """Niba answers newest first; sums must still accumulate forward in time."""

    days = parse_consumption_daily(
        [
            _day("2026-08-03", [("00:00:00", 3.0, 0.0)]),
            _day("2026-08-01", [("00:00:00", 1.0, 0.0)]),
            _day("2026-08-02", [("00:00:00", 2.0, 0.0)]),
        ]
    )

    assert [d.date for d in days] == ["2026-08-01", "2026-08-02", "2026-08-03"]

    consumption, _, _, _ = build_statistics(days, 0.0, 0.0)

    assert [row["sum"] for row in consumption] == [1.0, 3.0, 6.0]


def test_missing_values_count_as_zero_without_breaking_the_sum() -> None:
    days = parse_consumption_daily(
        [
            {
                "date": "2026-08-01",
                "hour_measurements": [
                    {"hour": "00:00:00"},
                    {"hour": "01:00:00", "active_value": 2.0},
                ],
            }
        ]
    )

    consumption, _, con_sum, _ = build_statistics(days, 0.0, 0.0)

    assert [row["sum"] for row in consumption] == [0.0, 2.0]
    assert con_sum == 2.0


def test_a_day_with_an_unparseable_date_is_skipped() -> None:
    days = parse_consumption_daily(
        [{"date": "not-a-date", "hour_measurements": [{"hour": "00:00:00"}]}]
    )

    consumption, _, con_sum, _ = build_statistics(days, 0.0, 0.0)

    assert consumption == []
    assert con_sum == 0.0


def test_a_day_without_hours_produces_nothing() -> None:
    days = parse_consumption_daily([{"date": "2026-08-01"}])

    consumption, export, con_sum, exp_sum = build_statistics(days, 7.0, 3.0)

    assert consumption == []
    assert export == []
    assert (con_sum, exp_sum) == (7.0, 3.0)


def test_meter_timezone_is_the_spanish_one() -> None:
    assert METER_TZ.key == "Europe/Madrid"


async def test_import_writes_statistics_the_recorder_can_read_back(
    recorder_mock: None, hass
) -> None:
    """End-to-end: the rows must land in the recorder and read back correctly."""

    from homeassistant.components.recorder.statistics import statistics_during_period
    from pytest_homeassistant_custom_component.components.recorder.common import (
        async_wait_recording_done,
    )

    from custom_components.niba.statistics import async_import_statistics

    class _Client:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        async def get_consumption_daily(self, cups, date_start, date_end):
            self.calls.append((cups, date_start, date_end))
            return parse_consumption_daily(
                [
                    _day(
                        "2026-08-01",
                        [("00:00:00", 1.5, 0.25), ("01:00:00", 2.5, 0.75)],
                    )
                ]
            )

    cups = "ES0021000011349260ME"
    client = _Client()

    await async_import_statistics(hass, client, cups)
    await async_wait_recording_done(hass)

    consumption_id, export_id = statistic_ids(cups)
    stats = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2026, 7, 31, 0, 0, tzinfo=UTC),
        datetime(2026, 8, 2, 0, 0, tzinfo=UTC),
        {consumption_id, export_id},
        "hour",
        None,
        {"sum", "state"},
    )

    assert [row["sum"] for row in stats[consumption_id]] == [1.5, 4.0]
    assert [row["sum"] for row in stats[export_id]] == [0.25, 1.0]
    # First run backfills, so it asks for a wide window.
    assert client.calls[0][0] == cups
    assert (client.calls[0][2] - client.calls[0][1]).days > 300


async def test_second_import_resumes_instead_of_restarting_the_sum(
    recorder_mock: None, hass
) -> None:
    """A re-import must continue the running sum, not restart or double it."""

    from datetime import date

    from homeassistant.components.recorder.statistics import statistics_during_period
    from pytest_homeassistant_custom_component.components.recorder.common import (
        async_wait_recording_done,
    )

    from custom_components.niba.statistics import async_import_statistics

    cups = "ES0021000011349260ME"

    # Behaves like the API: returns every stored day inside the window.
    store = {
        "2026-08-01": [("00:00:00", 1.0, 0.0), ("01:00:00", 1.0, 0.0)],
    }

    class _Client:
        def __init__(self) -> None:
            self.windows: list[tuple[date, date]] = []

        async def get_consumption_daily(self, cups, date_start, date_end):
            self.windows.append((date_start, date_end))
            return parse_consumption_daily(
                [
                    _day(day, hours)
                    for day, hours in store.items()
                    if date_start.isoformat() <= day <= date_end.isoformat()
                ]
            )

    client = _Client()
    await async_import_statistics(hass, client, cups)
    await async_wait_recording_done(hass)

    # A new day shows up on the next run.
    store["2026-08-02"] = [("00:00:00", 1.0, 0.0)]
    await async_import_statistics(hass, client, cups)
    await async_wait_recording_done(hass)

    consumption_id, _ = statistic_ids(cups)
    stats = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2026, 7, 31, 0, 0, tzinfo=UTC),
        datetime(2026, 8, 3, 0, 0, tzinfo=UTC),
        {consumption_id},
        "hour",
        None,
        {"sum"},
    )

    assert [row["sum"] for row in stats[consumption_id]] == [1.0, 2.0, 3.0]
    # The second window is far narrower: it resumes from what is already
    # stored instead of walking the whole contract again.
    first = (client.windows[0][1] - client.windows[0][0]).days
    second = (client.windows[1][1] - client.windows[1][0]).days
    assert first > 300
    assert second < first / 5
