"""OpenRouteService client for HGV routing.

Why ORS: the public Mapbox Directions profiles are car, walking and cycling
only, so they cannot honour truck weight and height restrictions. The
``driving-hgv`` profile can, and it is the reason this project routes trucks
rather than cars.

Two constraints discovered against the live API drive the design here:

1. ``alternative_routes`` is rejected for routes longer than 100 km, so it can
   never be used for real trips. Alternatives are produced by re-routing with
   a different preference or through a waypoint, then de-duplicated.
2. The free key allows 200 requests per day, so candidate generation is
   capped at a handful of calls and each call is retried at most once.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from typing import Any

import requests
from django.conf import settings

from .exceptions import ProviderError
from .geo import (
    METERS_PER_MILE,
    Coordinate,
    cumulative_miles,
    haversine_miles,
    offset_waypoint,
    overlap_fraction,
)

logger = logging.getLogger(__name__)

PROFILE = "driving-hgv"
DUPLICATE_OVERLAP_THRESHOLD = 0.9
#: Offsets are picked beside the route, then snapped by ORS to the nearest
#: truck-accessible road. Without ``radiuses`` a waypoint in open country fails
#: with "no routable point within 350m", which is the default snapping radius.
VIA_ATTEMPTS: tuple[tuple[float, float], ...] = ((20.0, 0.55), (-20.0, 0.55))
VIA_SNAP_RADIUS_METERS = 25_000

#: ORS error codes worth one retry. 2009 is "route could not be found", which
#: was observed to fail once and succeed immediately afterwards.
RETRYABLE_CODES = {2009}


@dataclass(frozen=True)
class RouteCandidate:
    id: str
    label: str
    geometry: list[Coordinate]
    distance_miles: float
    duration_minutes: float
    #: (miles from origin, elapsed minutes) pairs from the step table.
    progress: list[tuple[float, float]] = field(default_factory=list)

    def elapsed_minutes_at(self, miles: float) -> float:
        """Interpolate travel time at a distance along the route."""
        if not self.progress:
            return 0.0
        if miles <= self.progress[0][0]:
            return self.progress[0][1]
        if miles >= self.progress[-1][0]:
            return self.progress[-1][1]

        low, high = 0, len(self.progress) - 1
        while low + 1 < high:
            mid = (low + high) // 2
            if self.progress[mid][0] <= miles:
                low = mid
            else:
                high = mid

        start_miles, start_minutes = self.progress[low]
        end_miles, end_minutes = self.progress[high]
        span = end_miles - start_miles
        ratio = (miles - start_miles) / span if span > 0 else 0.0
        return start_minutes + ratio * (end_minutes - start_minutes)


def build_candidates(
    origin: Coordinate, destination: Coordinate, max_routes: int = 3
) -> list[RouteCandidate]:
    """Produce up to ``max_routes`` distinct routes, best-effort but honest.

    Fewer routes are returned when the corridor genuinely has no distinct
    alternatives. Nothing is duplicated to hit the target count.
    """
    base = _route([origin, destination])
    candidates: list[RouteCandidate] = [replace(base, id="r1", label="Fastest")]

    if max_routes < 2:
        return candidates

    # The toll-free variant and both detours are independent of each other, so
    # they are requested together. Worst case is four routing calls per plan.
    waypoints = [
        offset_waypoint(base.geometry, fraction, lateral) for lateral, fraction in VIA_ATTEMPTS
    ]
    with ThreadPoolExecutor(max_workers=1 + len(waypoints)) as pool:
        toll_future = pool.submit(_try_route, [origin, destination], avoid_features=["tollways"])
        detour_futures = [
            pool.submit(
                _try_route,
                [origin, waypoint, destination],
                radiuses=[-1, VIA_SNAP_RADIUS_METERS, -1],
            )
            for waypoint in waypoints
        ]
        proposals = [toll_future] + detour_futures
        results = [future.result() for future in proposals]

    for index, result in enumerate(results):
        if len(candidates) >= max_routes:
            break
        if not result or not _is_distinct(result, candidates):
            continue
        label = "Avoids tolls" if index == 0 else "Alternate corridor"
        candidates.append(replace(result, id=f"r{len(candidates) + 1}", label=label))

    return candidates[:max_routes]


def _is_distinct(candidate: RouteCandidate, kept: Sequence[RouteCandidate]) -> bool:
    return all(
        overlap_fraction(candidate.geometry, other.geometry) < DUPLICATE_OVERLAP_THRESHOLD
        for other in kept
    )


def _try_route(
    coordinates: list[Coordinate],
    *,
    avoid_features: list[str] | None = None,
    radiuses: list[int] | None = None,
) -> RouteCandidate | None:
    """Route, tolerating the failures that are normal for waypoint detours."""
    try:
        return _route(coordinates, avoid_features=avoid_features, radiuses=radiuses)
    except ProviderError as exc:
        logger.info("Alternate route candidate skipped: %s", exc.message)
        return None


def _route(
    coordinates: list[Coordinate],
    *,
    avoid_features: list[str] | None = None,
    radiuses: list[int] | None = None,
) -> RouteCandidate:
    payload: dict[str, Any] = {
        "coordinates": coordinates,
        "instructions": True,
        "language": "en",
    }
    if avoid_features:
        payload["options"] = {"avoid_features": avoid_features}
    if radiuses:
        payload["radiuses"] = radiuses

    response = _post_with_retry("/v2/directions/driving-hgv/geojson", payload)
    return _parse_route(response)


def _post_with_retry(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not settings.ORS_API_KEY:
        raise ProviderError(
            "Routing is not configured on this server.",
            status=500,
            provider="routing",
        )

    url = f"{settings.ORS_BASE_URL.rstrip('/')}{path}"
    headers = {
        "Authorization": settings.ORS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/geo+json, application/json",
    }

    last_error: ProviderError | None = None
    for _ in (1, 2):
        try:
            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=(5, settings.PROVIDER_TIMEOUT_SECONDS),
            )
        except requests.Timeout:
            last_error = ProviderError(
                "The routing service did not respond in time. Please try again.",
                provider="routing",
            )
            continue
        except requests.RequestException:
            logger.warning("Routing request failed", exc_info=True)
            last_error = ProviderError(
                "Could not reach the routing service. Please try again.",
                provider="routing",
            )
            continue

        if response.status_code in (401, 403):
            raise ProviderError(
                "Routing is temporarily unavailable because the provider rejected our credentials.",
                status=502,
                provider="routing",
            )
        if response.status_code == 429:
            raise ProviderError(
                "Routing quota reached. Please try again later.",
                status=503,
                provider="routing",
                retry_after=_retry_after_seconds(response),
            )
        if response.status_code >= 500:
            last_error = ProviderError(
                "The routing service is having trouble. Please try again.",
                provider="routing",
            )
            continue
        if response.status_code >= 400:
            raise _client_error(response)

        return response.json()

    raise last_error or ProviderError("Routing failed. Please try again.", provider="routing")


def _retry_after_seconds(response: requests.Response) -> int | None:
    reset = response.headers.get("x-ratelimit-reset")
    if reset and reset.isdigit():
        import time

        return max(0, int(reset) - int(time.time()))
    return None


def _client_error(response: requests.Response) -> ProviderError:
    code: int | None = None
    message = ""
    try:
        body = response.json()
        error = body.get("error", {})
        code = error.get("code")
        message = error.get("message", "")
    except ValueError:
        pass

    lowered = message.lower()
    if "routable point" in lowered:
        return ProviderError(
            "One of the waypoints is too far from a truck-accessible road. "
            "Try a nearby town or highway junction.",
            status=422,
            provider="routing",
        )
    if "could not be found" in lowered:
        return ProviderError(
            "No truck route could be found between those locations.",
            status=422,
            provider="routing",
        )
    logger.info("Routing rejected the request (code=%s)", code)
    return ProviderError(
        "The routing service could not calculate a route for this trip.",
        status=422,
        provider="routing",
    )


def _parse_route(payload: dict[str, Any]) -> RouteCandidate:
    features = payload.get("features") or []
    if not features:
        raise ProviderError("The routing service returned no route.", provider="routing")

    feature = features[0]
    geometry = [[float(lon), float(lat)] for lon, lat in feature["geometry"]["coordinates"]]
    summary = feature["properties"]["summary"]
    distance_miles = float(summary["distance"]) / METERS_PER_MILE
    duration_minutes = float(summary["duration"]) / 60.0

    return RouteCandidate(
        id="route",
        label="Fastest",
        geometry=geometry,
        distance_miles=round(distance_miles, 2),
        duration_minutes=round(duration_minutes, 1),
        progress=_build_progress(
            geometry, feature["properties"].get("segments") or [], duration_minutes, distance_miles
        ),
    )


def _build_progress(
    geometry: list[Coordinate],
    segments: list[dict[str, Any]],
    duration_minutes: float,
    distance_miles: float,
) -> list[tuple[float, float]]:
    """Map each step boundary onto (miles, elapsed minutes).

    ORS exposes ``way_points`` per step: indices into the route geometry. That
    lets checkpoint ETAs follow the real speed profile (towns are slow,
    interstates are fast) instead of assuming constant speed.
    """
    cumulative = cumulative_miles(geometry)
    progress: list[tuple[float, float]] = [(0.0, 0.0)]
    elapsed = 0.0
    previous_index = 0
    usable = True

    for segment in segments:
        for step in segment.get("steps") or []:
            way_points = step.get("way_points") or []
            if len(way_points) != 2:
                usable = False
                break
            start_index, end_index = int(way_points[0]), int(way_points[1])
            if end_index >= len(cumulative) or start_index < previous_index:
                usable = False
                break
            previous_index = end_index
            elapsed += float(step.get("duration") or 0.0) / 60.0
            progress.append((cumulative[end_index], elapsed))
        if not usable:
            break

    fits = (
        usable
        and len(progress) > 2
        and progress[-1][0] > 0
        and abs(progress[-1][0] - distance_miles) < max(1.0, distance_miles * 0.02)
    )
    if not fits:
        # Fall back to constant speed rather than emitting a broken curve.
        return [(0.0, 0.0), (distance_miles, duration_minutes)]

    progress[-1] = (distance_miles, duration_minutes)
    cleaned: list[tuple[float, float]] = []
    for miles, minutes in progress:
        if cleaned and miles <= cleaned[-1][0]:
            continue
        cleaned.append((miles, minutes))
    return cleaned or [(0.0, 0.0), (distance_miles, duration_minutes)]


def straight_line_miles(origin: Coordinate, destination: Coordinate) -> float:
    return haversine_miles(origin, destination)
