"""Import Niba's hourly readings as Home Assistant long-term statistics.

Niba publishes hourly grid and export readings per day, going back to the
start of the contract. Feeding those into the recorder as *external*
statistics gives the Energy Dashboard real measured history instead of the
running total the sensors can offer, and it backfills the past on first run.

The statistics are external (``niba:...``), so they are not tied to an
entity and survive the entity being renamed or removed.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
import logging
from zoneinfo import ZoneInfo

from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.components.recorder.util import get_instance
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant

from .api import DailyConsumption, NibaApiClient, NibaApiError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Spanish supply points are metered in local time, and the DST days come back
# with 23 or 25 readings accordingly.
METER_TZ = ZoneInfo("Europe/Madrid")

# The contract history is finite; this only bounds the very first import.
MAX_BACKFILL_DAYS = 730

# Niba revises recent readings, so always re-request a few days and let the
# recorder overwrite them.
RESTATEMENT_DAYS = 3


def statistic_ids(cups: str) -> tuple[str, str]:
    """Return the (grid consumption, grid export) statistic ids for a CUPS."""

    slug = cups.lower()
    return f"{DOMAIN}:{slug}_energy_consumption", f"{DOMAIN}:{slug}_energy_export"


def _metadata(statistic_id: str, name: str) -> StatisticMetaData:
    return StatisticMetaData(
        has_mean=False,
        has_sum=True,
        name=name,
        source=DOMAIN,
        statistic_id=statistic_id,
        unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    )


def _day_start(day: str) -> datetime | None:
    """Return the UTC instant local midnight falls on for a Niba date."""

    try:
        parsed = date.fromisoformat(day)
    except ValueError:
        return None
    return datetime.combine(parsed, time.min, tzinfo=METER_TZ).astimezone(UTC)


def _slots(day: DailyConsumption) -> list[tuple[datetime, float, float]]:
    """Lay a day's readings on consecutive hourly slots from local midnight.

    The ``hour`` labels cannot be trusted across a DST change: on the spring
    day Niba omits ``01:00`` and sends ``02:00``, and taking both labels at
    face value lands ``02:00`` and ``03:00`` on the same UTC hour — one
    reading silently overwrites the other. Walking forward in UTC from local
    midnight instead keeps 23-, 24- and 25-reading days all intact.
    """

    start = _day_start(day.date) if day.date else None
    if start is None:
        return []

    readings = sorted(
        (m for m in day.hours if m.hour is not None), key=lambda m: m.hour or ""
    )
    return [
        (
            start + timedelta(hours=index),
            measurement.active_value or 0.0,
            measurement.out_active_value or 0.0,
        )
        for index, measurement in enumerate(readings)
    ]


def build_statistics(
    days: tuple[DailyConsumption, ...],
    consumption_sum: float,
    export_sum: float,
) -> tuple[list[StatisticData], list[StatisticData], float, float]:
    """Turn daily payloads into hourly statistics, continuing running sums.

    Returns the consumption rows, the export rows and the sums they end on.
    See ``_slots`` for why the hour labels are not used directly.
    """

    consumption: dict[datetime, float] = {}
    export: dict[datetime, float] = {}

    for day in days:
        for start, active, out in _slots(day):
            consumption[start] = active
            export[start] = out

    consumption_rows: list[StatisticData] = []
    for start in sorted(consumption):
        consumption_sum += consumption[start]
        consumption_rows.append(
            StatisticData(start=start, state=consumption[start], sum=consumption_sum)
        )

    export_rows: list[StatisticData] = []
    for start in sorted(export):
        export_sum += export[start]
        export_rows.append(
            StatisticData(start=start, state=export[start], sum=export_sum)
        )

    return consumption_rows, export_rows, consumption_sum, export_sum


async def _last_stat(
    hass: HomeAssistant, statistic_id: str
) -> tuple[datetime | None, float]:
    """Return the last recorded slot and running sum for a statistic."""

    stats = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, statistic_id, True, {"sum"}
    )
    rows = stats.get(statistic_id) if stats else None
    if not rows:
        return None, 0.0
    row = rows[0]
    start = row.get("start")
    if isinstance(start, int | float):
        start = datetime.fromtimestamp(start, UTC)
    return start, float(row.get("sum") or 0.0)


async def async_import_statistics(
    hass: HomeAssistant, client: NibaApiClient, cups: str
) -> None:
    """Import any hourly readings Niba has that the recorder does not."""

    consumption_id, export_id = statistic_ids(cups)
    last_start, consumption_sum = await _last_stat(hass, consumption_id)
    _, export_sum = await _last_stat(hass, export_id)

    today = datetime.now(METER_TZ).date()
    if last_start is None:
        date_start = today - timedelta(days=MAX_BACKFILL_DAYS)
    else:
        # Re-request the tail: Niba restates recent readings.
        date_start = last_start.astimezone(METER_TZ).date() - timedelta(
            days=RESTATEMENT_DAYS
        )
        consumption_sum, export_sum = await _sums_before(
            hass, consumption_id, export_id, date_start
        )

    try:
        days = await client.get_consumption_daily(cups, date_start, today)
    except NibaApiError as err:
        _LOGGER.debug("Skipping statistics import: %s", err)
        return

    if not days:
        return

    consumption_rows, export_rows, _, _ = build_statistics(
        days, consumption_sum, export_sum
    )
    if consumption_rows:
        async_add_external_statistics(
            hass,
            _metadata(consumption_id, f"Niba {cups} consumo de red"),
            consumption_rows,
        )
    if export_rows:
        async_add_external_statistics(
            hass,
            _metadata(export_id, f"Niba {cups} vertido a red"),
            export_rows,
        )
    _LOGGER.debug(
        "Imported %s hourly statistics from %s", len(consumption_rows), date_start
    )


async def _sums_before(
    hass: HomeAssistant,
    consumption_id: str,
    export_id: str,
    date_start: date,
) -> tuple[float, float]:
    """Return the running sums just before ``date_start``.

    Rewriting a slot replaces its sum, so the re-requested tail has to
    continue from the sum held by the last slot we are *not* rewriting.
    """

    from homeassistant.components.recorder.statistics import statistics_during_period

    boundary = datetime.combine(date_start, datetime.min.time()).replace(
        tzinfo=METER_TZ
    )
    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        boundary - timedelta(hours=2),
        boundary,
        {consumption_id, export_id},
        "hour",
        None,
        {"sum"},
    )

    def tail(statistic_id: str) -> float:
        rows = stats.get(statistic_id) or []
        return float(rows[-1].get("sum") or 0.0) if rows else 0.0

    return tail(consumption_id), tail(export_id)
