"""Trip planning orchestration.

Ties the providers and the risk engine together. HTTP concerns stay in
``views.py``; provider details stay in ``ors.py`` / ``weather.py``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from django.conf import settings

from . import ors, photon, risk, weather
from .geo import (
    Coordinate,
    choose_interval,
    corridor_points,
    cumulative_miles,
    haversine_miles,
    sample_checkpoints,
)

logger = logging.getLogger(__name__)

#: Frontend wire values for heatmap cells, kept stable and compact.
SEVERITY_INDEX = {
    risk.LOW.name: 0,
    risk.MODERATE.name: 1,
    risk.HIGH.name: 2,
    risk.SEVERE.name: 3,
    risk.NO_TRAVEL.name: 4,
    risk.UNKNOWN.name: 5,
}

UNKNOWN_FRACTION_WARNING = 0.2


def plan_trip(
    *,
    origin: Coordinate,
    destination: Coordinate,
    departure: datetime,
    load_lbs: float,
    interval_miles: int,
) -> dict[str, Any]:
    candidates = ors.build_candidates(origin, destination)
    if not candidates:
        raise ors.ProviderError("No truck route could be found between those locations.")

    prepared = [_prepare_route(candidate, interval_miles) for candidate in candidates]
    _attach_weather(prepared, departure, load_lbs)

    for route in prepared:
        route["summary"] = risk.summarize(route.pop("legs"))
        route["warnings"] = _route_warnings(route)

    ranking_input = [
        risk.Rankable(
            id=route["id"],
            summary=route["summary"],
            duration_minutes=route["durationMinutes"],
        )
        for route in prepared
    ]
    ordered = risk.rank_routes(ranking_input)

    by_id = {route["id"]: route for route in prepared}
    for position, rankable in enumerate(ordered, start=1):
        by_id[rankable.id]["rank"] = position
        by_id[rankable.id]["isRecommended"] = position == 1
    routes = [by_id[rankable.id] for rankable in ordered]

    recommended = routes[0]
    heatmap = _build_heatmap(recommended, departure, load_lbs)

    notices: list[str] = []
    if len(routes) < 3:
        notices.append(
            f"Only {len(routes)} distinct truck route"
            f"{'' if len(routes) == 1 else 's'} could be generated for this corridor."
        )
    if any(route["isCoarsened"] for route in routes):
        used = routes[0]["intervalMiles"]
        limit = settings.MAX_CHECKPOINTS_PER_ROUTE
        notices.append(
            f"Weather was sampled every {used} miles instead of {interval_miles} "
            f"to keep the response within the {limit}-checkpoint limit."
        )
        for route in routes:
            route["intervalMiles"] = used

    return {
        "origin": {"lat": origin[1], "lon": origin[0]},
        "destination": {"lat": destination[1], "lon": destination[0]},
        "departureAt": _iso(departure),
        "loadLbs": load_lbs,
        "intervalMiles": routes[0]["intervalMiles"],
        "recommendedRouteId": recommended["id"],
        "routes": [_public_route(route) for route in routes],
        "heatmap": heatmap,
        "notices": notices,
        "attribution": [
            "Routing: openrouteservice (HeiGIT) · driving-hgv profile",
            "Weather: Open-Meteo (CC BY 4.0)",
            "Map data: OpenStreetMap contributors · Tiles: OpenFreeMap",
        ],
    }


def _public_route(route: dict[str, Any]) -> dict[str, Any]:
    """Strip internal bookkeeping before the response leaves the server."""
    return {
        "id": route["id"],
        "label": route["label"],
        "rank": route["rank"],
        "isRecommended": route["isRecommended"],
        "distanceMiles": route["distanceMiles"],
        "durationMinutes": route["durationMinutes"],
        "arrivalAt": route["arrivalAt"],
        "intervalMiles": route["intervalMiles"],
        "geometry": route["geometry"],
        "checkpoints": route["checkpoints"],
        "summary": _summary_dict(route["summary"]),
        "warnings": route["warnings"],
    }


def _summary_dict(summary: risk.RiskSummary) -> dict[str, float]:
    return {
        "totalMiles": summary.total_miles,
        "lowMiles": summary.low_miles,
        "moderateMiles": summary.moderate_miles,
        "highMiles": summary.high_miles,
        "severeMiles": summary.severe_miles,
        "noTravelMiles": summary.no_travel_miles,
        "unknownMiles": summary.unknown_miles,
        "avgRisk": summary.avg_risk,
    }


def _prepare_route(candidate: ors.RouteCandidate, interval_miles: int) -> dict[str, Any]:
    cumulative = cumulative_miles(candidate.geometry)
    total = cumulative[-1]
    effective_interval, coarsened = choose_interval(
        total, interval_miles, settings.MAX_CHECKPOINTS_PER_ROUTE
    )

    checkpoints = sample_checkpoints(candidate.geometry, effective_interval, cumulative)

    return {
        "id": candidate.id,
        "label": candidate.label,
        "geometry": candidate.geometry,
        "distanceMiles": round(total, 2),
        "durationMinutes": candidate.duration_minutes,
        "arrivalAt": None,
        "intervalMiles": effective_interval,
        "isCoarsened": coarsened,
        "cumulative": cumulative,
        "route": candidate,
        "checkpoints": checkpoints,
        "legs": [],
        "rank": 0,
        "isRecommended": False,
        "score": None,
        "warnings": [],
    }


def _attach_weather(prepared: list[dict[str, Any]], departure: datetime, load_lbs: float) -> None:
    """Fetch once for all three routes, then classify each checkpoint."""
    points: list[Coordinate] = []
    for route in prepared:
        points.extend(checkpoint.position for checkpoint in route["checkpoints"])

    longest = max(route["durationMinutes"] for route in prepared)
    span_days = int(longest // (60 * 24)) + 2
    days = min(weather.MAX_FORECAST_DAYS, span_days)
    series = weather.fetch_series(points, departure.date(), days)

    for route in prepared:
        checkpoints = route["checkpoints"]
        payload: list[dict[str, Any]] = []
        legs: list[risk.Leg] = []

        for checkpoint in checkpoints:
            elapsed = route["route"].elapsed_minutes_at(checkpoint.offset_miles)
            eta = departure + timedelta(minutes=elapsed)
            conditions = weather.conditions_at(
                series.get((round(checkpoint.position[1], 3), round(checkpoint.position[0], 3))),
                eta,
            )
            has_data = conditions is not None
            severity = risk.classify_checkpoint(
                conditions.wind_mph if conditions else None,
                conditions.rain_in_hr if conditions else None,
                conditions.snow_in_hr if conditions else None,
                load_lbs,
            )
            payload.append(
                {
                    "distanceMiles": round(checkpoint.offset_miles, 1),
                    "position": [
                        round(checkpoint.position[0], 5),
                        round(checkpoint.position[1], 5),
                    ],
                    "eta": _iso(eta),
                    "elapsedMinutes": round(elapsed, 1),
                    "windMph": _round(conditions.wind_mph if conditions else None, 1),
                    "rainInHr": _round(conditions.rain_in_hr if conditions else None, 3),
                    "snowInHr": _round(conditions.snow_in_hr if conditions else None, 3),
                    "severity": severity.name,
                    "hasData": has_data,
                }
            )
            legs.append(risk.Leg(miles=0.0, severity=severity))

        # A checkpoint governs the stretch from itself to the next checkpoint.
        for index in range(len(checkpoints) - 1):
            miles = checkpoints[index + 1].offset_miles - checkpoints[index].offset_miles
            legs[index] = risk.Leg(miles=miles, severity=legs[index].severity)
        if len(checkpoints) == 1:
            legs = [risk.Leg(miles=route["distanceMiles"], severity=legs[0].severity)]

        route["checkpoints"] = payload
        route["legs"] = legs
        route["arrivalAt"] = _iso(departure + timedelta(minutes=route["durationMinutes"]))


def _route_warnings(route: dict[str, Any]) -> list[str]:
    summary: risk.RiskSummary = route["summary"]
    warnings: list[str] = []

    if summary.has_no_travel:
        count = sum(1 for cp in route["checkpoints"] if cp["severity"] == risk.NO_TRAVEL.name)
        warnings.append(
            f"{count} checkpoint{'' if count == 1 else 's'} exceed safe travel limits "
            f"({summary.no_travel_miles:.0f} mi)."
        )
    if summary.unknown_fraction > UNKNOWN_FRACTION_WARNING:
        warnings.append(
            f"{summary.unknown_fraction:.0%} of this route has no forecast data; "
            "unknown conditions are never treated as safe."
        )
    return warnings


def _build_heatmap(route: dict[str, Any], departure: datetime, load_lbs: float) -> dict[str, Any]:
    """Weather risk for the corridor over the next 48 hours.

    Deliberately independent of the departure time: the slider shows the
    forecast at a chosen hour, which is a different question from "what will
    the truck meet at 4pm".
    """
    now = datetime.now(UTC)
    window = weather.hourly_window(now, settings.HEATMAP_HOURS)
    corridor = corridor_points(
        route["geometry"],
        settings.HEATMAP_SAMPLE_MILES,
        tuple(settings.HEATMAP_LATERAL_OFFSETS_MILES),
        route["cumulative"],
    )

    start_date = window[0].date()
    days = (window[-1].date() - start_date).days + 2
    series = weather.fetch_series(corridor, start_date, days)

    cells: list[dict[str, Any]] = []
    for position in corridor:
        point_series = series.get((round(position[1], 3), round(position[0], 3)))
        values: list[int] = []
        for hour in window:
            conditions = weather.conditions_at(point_series, hour)
            severity = risk.classify_checkpoint(
                conditions.wind_mph if conditions else None,
                conditions.rain_in_hr if conditions else None,
                conditions.snow_in_hr if conditions else None,
                load_lbs,
            )
            values.append(SEVERITY_INDEX[severity.name])
        cells.append(
            {
                "position": [round(position[0], 4), round(position[1], 4)],
                "values": values,
            }
        )

    trip_window = _trip_window(departure, route["durationMinutes"], window[0])
    return {
        "hours": [_iso(hour) for hour in window],
        "cells": cells,
        "tripWindow": trip_window,
        "generatedAt": _iso(now),
        "sampleMiles": settings.HEATMAP_SAMPLE_MILES,
    }


def _trip_window(departure: datetime, duration_minutes: float, first_hour: datetime) -> dict | None:
    start = (departure - first_hour).total_seconds() / 3600
    end = start + duration_minutes / 60
    if end < 0 or start > 48:
        return None
    return {"startHour": round(max(0.0, start), 2), "endHour": round(min(48.0, end), 2)}


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(float(value), digits)


def geocode(query: str) -> list[dict[str, Any]]:
    return photon.search(query)


def straight_miles(origin: Coordinate, destination: Coordinate) -> float:
    return haversine_miles(origin, destination)
