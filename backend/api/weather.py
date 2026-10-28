"""Open-Meteo hourly forecast client.

One request can cover many coordinates, which is what makes per-checkpoint
sampling affordable. Values are requested in the units the risk table uses
(mph, inches) so no conversion is hidden in the scoring code -- including
snowfall, which Open-Meteo switches to inches when ``precipitation_unit=inch``
is set. That was verified against the live API rather than assumed.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import requests
from django.conf import settings
from django.core.cache import cache

from .exceptions import ProviderError
from .geo import Coordinate

logger = logging.getLogger(__name__)

CHUNK_SIZE = 50
MAX_FORECAST_DAYS = 16
MAX_WORKERS = 6
HOURLY_VARIABLES = ("wind_speed_10m", "rain", "snowfall")


@dataclass(frozen=True)
class Conditions:
    wind_mph: float | None
    rain_in_hr: float | None
    snow_in_hr: float | None


#: hour -> conditions, for one coordinate
Series = dict[datetime, Conditions]


def fetch_series(
    points: Iterable[Coordinate], start: date, days: int
) -> dict[tuple[float, float], Series]:
    """Fetch hourly conditions for every point, cached per coordinate chunk.

    Points are rounded to three decimals (~110 m) both to keep the cache
    usable across nearby checkpoints and to keep request URLs short.
    """
    rounded = _unique_points(points)
    if not rounded:
        return {}

    days = max(1, min(days, MAX_FORECAST_DAYS))
    chunks = list(_chunked(rounded, CHUNK_SIZE))
    if not chunks:
        return {}

    if len(chunks) == 1:
        return _fetch_chunk(chunks[0], start, days)

    # Chunks are independent, and sequential fetches dominate plan latency.
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(chunks))) as pool:
        results = list(pool.map(lambda chunk: _fetch_chunk(chunk, start, days), chunks))

    merged: dict[tuple[float, float], Series] = {}
    for result in results:
        merged.update(result)
    return merged


def _unique_points(points: Iterable[Coordinate]) -> list[tuple[float, float]]:
    seen: dict[tuple[float, float], None] = {}
    for lon, lat in points:
        seen[(round(lat, 3), round(lon, 3))] = None
    return list(seen)


def _chunked(items: list[tuple[float, float]], size: int) -> Iterable[list[tuple[float, float]]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _fetch_chunk(
    chunk: list[tuple[float, float]], start: date, days: int
) -> dict[tuple[float, float], Series]:
    end = start + timedelta(days=days)
    key = _cache_key(chunk, start, end)
    cached = cache.get(key)
    if cached is not None:
        return cached

    params = {
        "latitude": ",".join(f"{lat:.3f}" for lat, _ in chunk),
        "longitude": ",".join(f"{lon:.3f}" for _, lon in chunk),
        "hourly": ",".join(HOURLY_VARIABLES),
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "UTC",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }

    payload = _request(params)
    parsed = _parse(payload, chunk)
    cache.set(key, parsed, settings.WEATHER_CACHE_SECONDS)
    return parsed


def _cache_key(chunk: list[tuple[float, float]], start: date, end: date) -> str:
    digest = hashlib.sha256(
        ";".join(f"{lat:.3f},{lon:.3f}" for lat, lon in chunk).encode()
    ).hexdigest()[:24]
    return f"wx:{start.isoformat()}:{end.isoformat()}:{digest}"


def _request(params: dict[str, str]) -> Any:
    url = f"{settings.OPEN_METEO_BASE_URL.rstrip('/')}/v1/forecast"
    last_error: ProviderError | None = None

    for _ in (1, 2):
        try:
            response = requests.get(
                url, params=params, timeout=(5, settings.PROVIDER_TIMEOUT_SECONDS)
            )
        except requests.RequestException:
            logger.warning("Weather request failed", exc_info=True)
            last_error = ProviderError(
                "Could not reach the weather service. Please try again.",
                provider="weather",
            )
            continue

        if response.status_code == 429:
            raise ProviderError(
                "Weather quota reached. Please try again later.",
                status=503,
                provider="weather",
                retry_after=60,
            )
        if response.status_code >= 500:
            last_error = ProviderError(
                "The weather service is having trouble. Please try again.",
                provider="weather",
            )
            continue
        if response.status_code >= 400:
            logger.info("Weather request rejected with %s", response.status_code)
            raise ProviderError(
                "The weather service could not supply a forecast for this route.",
                status=502,
                provider="weather",
            )

        return response.json()

    raise last_error or ProviderError("Weather lookup failed.", provider="weather")


def _parse(payload: Any, chunk: list[tuple[float, float]]) -> dict[tuple[float, float], Series]:
    """Open-Meteo returns a list for multiple coordinates and an object for one."""
    entries = payload if isinstance(payload, list) else [payload]
    result: dict[tuple[float, float], Series] = {}

    for index, entry in enumerate(entries):
        if index >= len(chunk):
            break
        key = chunk[index]
        hourly = entry.get("hourly") or {}
        times = hourly.get("time") or []
        wind = hourly.get("wind_speed_10m") or []
        rain = hourly.get("rain") or []
        snow = hourly.get("snowfall") or []

        series: Series = {}
        for position, stamp in enumerate(times):
            moment = _to_hour(stamp)
            if moment is None:
                continue
            series[moment] = Conditions(
                wind_mph=_value_at(wind, position),
                rain_in_hr=_value_at(rain, position),
                snow_in_hr=_value_at(snow, position),
            )
        result[key] = series

    return result


def _value_at(values: list[Any], index: int) -> float | None:
    if index >= len(values):
        return None
    value = values[index]
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_hour(stamp: str) -> datetime | None:
    try:
        return datetime.fromisoformat(stamp).replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def floor_to_hour(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def conditions_at(series: Series | None, moment: datetime) -> Conditions | None:
    if not series:
        return None
    return series.get(floor_to_hour(moment))


def hourly_window(start: datetime, hours: int) -> list[datetime]:
    """Consecutive whole hours beginning at ``start``, inclusive."""
    first = floor_to_hour(start)
    return [first + timedelta(hours=offset) for offset in range(hours)]
