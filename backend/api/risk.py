"""Weather risk classification, route aggregation and route ranking.

Pure functions: no I/O, no Django imports, no provider knowledge. Everything
here is deterministic and directly testable.

Boundary convention
-------------------
The spec writes its bands as ``25-34``, ``0.10-0.25``, ``<25``, ``>1.00`` and
``>=55``. Those are read as:

* Bands are lower-inclusive and upper-exclusive: ``25 <= wind < 35`` is
  Moderate, ``45 <= wind < 55`` is Severe.
* Wind's last band is written ``>= 55``, so exactly 55 mph is No Travel.
* Rain and snow's last bands are written ``0.50-1.00`` / ``>1.00`` and
  ``2.0-3.0`` / ``>3.0``, so exactly 1.00 in/hr of rain or 3.0 in/hr of snow
  is still Severe; only a strictly greater rate is No Travel.
* Load rules use ``> 30,000 lb`` and ``> 40,000 lb`` in the spec, so exactly
  30,000 lb and exactly 40,000 lb stay at the wind-derived severity.

Missing data
------------
A checkpoint whose conditions cannot all be classified is ``unknown``. Unknown
never counts as safe: it is reported as its own mileage bucket and scored 1.5
on the 0-4 risk scale, which places it above Moderate (1) and below High (2).
A route with gaps therefore cannot look clean on average risk, and because
missing data cannot be planned around it is treated as slightly worse than a
known Moderate forecast -- but never inflated to Severe. A confirmed No Travel
verdict does not depend on the missing variable, so it is preserved.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, order=False)
class Severity:
    """A risk class with an ordering rank and a numeric score."""

    name: str
    rank: int
    score: float

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return self.name


LOW = Severity("low", 0, 0.0)
MODERATE = Severity("moderate", 1, 1.0)
HIGH = Severity("high", 2, 2.0)
SEVERE = Severity("severe", 3, 3.0)
NO_TRAVEL = Severity("no_travel", 4, 4.0)
UNKNOWN = Severity("unknown", 9, 1.5)

#: The page renders these in order, so keep it stable.
ALL_SEVERITIES: tuple[Severity, ...] = (LOW, MODERATE, HIGH, SEVERE, NO_TRAVEL, UNKNOWN)

BY_NAME = {severity.name: severity for severity in ALL_SEVERITIES}


# --- Thresholds ------------------------------------------------------------

WIND_BANDS = (25.0, 35.0, 45.0, 55.0)
RAIN_BANDS = (0.10, 0.25, 0.50, 1.00)
SNOW_BANDS = (0.5, 1.0, 2.0, 3.0)

LOAD_NO_TRAVEL_WIND_MPH = 55.0
LOAD_HEAVY_LBS = 30_000.0
LOAD_VERY_HEAVY_LBS = 40_000.0


def wind_severity(mph: float | None) -> Severity:
    """Classify sustained wind. Exactly 55 mph is No Travel."""
    if mph is None:
        return UNKNOWN
    if mph < WIND_BANDS[0]:
        return LOW
    if mph < WIND_BANDS[1]:
        return MODERATE
    if mph < WIND_BANDS[2]:
        return HIGH
    if mph < WIND_BANDS[3]:
        return SEVERE
    return NO_TRAVEL


def rain_severity(inches_per_hour: float | None) -> Severity:
    """Classify rain rate. Exactly 1.00 in/hr is Severe; > 1.00 is No Travel."""
    if inches_per_hour is None:
        return UNKNOWN
    if inches_per_hour < RAIN_BANDS[0]:
        return LOW
    if inches_per_hour < RAIN_BANDS[1]:
        return MODERATE
    if inches_per_hour < RAIN_BANDS[2]:
        return HIGH
    if inches_per_hour <= RAIN_BANDS[3]:
        return SEVERE
    return NO_TRAVEL


def snow_severity(inches_per_hour: float | None) -> Severity:
    """Classify snowfall rate. Exactly 3.0 in/hr is Severe; > 3.0 is No Travel."""
    if inches_per_hour is None:
        return UNKNOWN
    if inches_per_hour < SNOW_BANDS[0]:
        return LOW
    if inches_per_hour < SNOW_BANDS[1]:
        return MODERATE
    if inches_per_hour < SNOW_BANDS[2]:
        return HIGH
    if inches_per_hour <= SNOW_BANDS[3]:
        return SEVERE
    return NO_TRAVEL


# --- Load rules ------------------------------------------------------------


def apply_load_rules(severity: Severity, wind_mph: float | None, load_lbs: float) -> Severity:
    """Raise severity for heavy loads. Load rules can only make things worse.

    * ``wind >= 55`` -> No Travel for any load
    * ``45 <= wind < 55`` and load > 30,000 lb -> No Travel
    * ``35 <= wind < 45`` and load > 40,000 lb -> Severe
    """
    if wind_mph is None:
        return severity
    if wind_mph >= LOAD_NO_TRAVEL_WIND_MPH:
        return NO_TRAVEL
    if wind_mph >= WIND_BANDS[2] and load_lbs > LOAD_HEAVY_LBS:
        return NO_TRAVEL
    if wind_mph >= WIND_BANDS[1] and load_lbs > LOAD_VERY_HEAVY_LBS:
        return SEVERE
    return severity


# --- Checkpoint classification ---------------------------------------------


def classify_checkpoint(
    wind_mph: float | None,
    rain_in_hr: float | None,
    snow_in_hr: float | None,
    load_lbs: float,
) -> Severity:
    """Worst of the three conditions, then the load rules."""
    wind = wind_severity(wind_mph)
    rain = rain_severity(rain_in_hr)
    snow = snow_severity(snow_in_hr)
    conditions = (wind, rain, snow)

    # A confirmed No Travel never depends on the condition we are missing.
    if any(condition is NO_TRAVEL for condition in conditions):
        return NO_TRAVEL
    if any(condition is UNKNOWN for condition in conditions):
        return UNKNOWN

    worst = max(conditions, key=lambda severity: severity.rank)
    return apply_load_rules(worst, wind_mph, load_lbs)


# --- Aggregation -----------------------------------------------------------


@dataclass(frozen=True)
class Leg:
    """A stretch of route that carries the risk of the checkpoint before it."""

    miles: float
    severity: Severity


@dataclass(frozen=True)
class RiskSummary:
    total_miles: float
    low_miles: float
    moderate_miles: float
    high_miles: float
    severe_miles: float
    no_travel_miles: float
    unknown_miles: float
    avg_risk: float

    @property
    def unknown_fraction(self) -> float:
        return self.unknown_miles / self.total_miles if self.total_miles else 0.0

    @property
    def has_no_travel(self) -> bool:
        return self.no_travel_miles > 0

    def ranking_key(self, duration_minutes: float) -> tuple[float, float, float, float]:
        """SCOPE.md priority order, exactly, with no weighting.

        1. Fewest severe-risk miles (No Travel miles count as severe)
        2. Fewest high-risk miles
        3. Lowest average risk
        4. Shortest travel time
        """
        return (
            round(self.severe_miles, 3),
            round(self.high_miles, 3),
            round(self.avg_risk, 4),
            round(duration_minutes, 3),
        )


def summarize(legs: Iterable[Leg]) -> RiskSummary:
    """Aggregate legs into mile buckets and an average risk score.

    ``severe_miles`` includes ``no_travel_miles``: a mile of No Travel weather
    is at least as bad as a mile of Severe weather, so a route cannot win by
    having its dangerous miles classified in the stricter bucket.
    """
    total = low = moderate = high = severe = no_travel = unknown = 0.0
    weighted_score = 0.0

    for leg in legs:
        miles = max(0.0, float(leg.miles))
        if miles == 0:
            continue
        total += miles
        weighted_score += leg.severity.score * miles
        if leg.severity is NO_TRAVEL:
            no_travel += miles
            severe += miles
        elif leg.severity is SEVERE:
            severe += miles
        elif leg.severity is HIGH:
            high += miles
        elif leg.severity is MODERATE:
            moderate += miles
        elif leg.severity is LOW:
            low += miles
        else:
            unknown += miles

    return RiskSummary(
        total_miles=round(total, 3),
        low_miles=round(low, 3),
        moderate_miles=round(moderate, 3),
        high_miles=round(high, 3),
        severe_miles=round(severe, 3),
        no_travel_miles=round(no_travel, 3),
        unknown_miles=round(unknown, 3),
        avg_risk=round(weighted_score / total, 4) if total else 0.0,
    )


# --- Ranking ---------------------------------------------------------------


@dataclass(frozen=True)
class Rankable:
    """Minimal shape the ranker needs; keeps it independent of the API layer."""

    id: str
    summary: RiskSummary
    duration_minutes: float


def rank_routes(routes: Sequence[Rankable]) -> list[Rankable]:
    """Return routes best-first using the scope's exact tie-break order."""
    return sorted(
        routes,
        key=lambda route: (*route.summary.ranking_key(route.duration_minutes), route.id),
    )
