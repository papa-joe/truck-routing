"""Geometry helpers: distance along a polyline, checkpoint sampling, corridors.

Coordinates are ``[lon, lat]`` pairs to match GeoJSON and the routing API.
All distances are statute miles.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

EARTH_RADIUS_MILES = 3958.8
METERS_PER_MILE = 1609.344

Coordinate = list[float]


def haversine_miles(a: Coordinate, b: Coordinate) -> float:
    lon1, lat1 = math.radians(a[0]), math.radians(a[1])
    lon2, lat2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(min(1.0, math.sqrt(h)))


def cumulative_miles(coordinates: list[Coordinate]) -> list[float]:
    """Running distance from the first point, one entry per coordinate."""
    cumulative = [0.0]
    for previous, current in zip(coordinates, coordinates[1:], strict=False):
        cumulative.append(cumulative[-1] + haversine_miles(previous, current))
    return cumulative


def point_at_miles(
    coordinates: list[Coordinate], cumulative: list[float], target: float
) -> Coordinate:
    """Interpolate the position that is ``target`` miles from the start."""
    if not coordinates:
        raise ValueError("route has no coordinates")
    if target <= 0:
        return list(coordinates[0])
    if target >= cumulative[-1]:
        return list(coordinates[-1])

    low, high = 0, len(cumulative) - 1
    while low + 1 < high:
        mid = (low + high) // 2
        if cumulative[mid] <= target:
            low = mid
        else:
            high = mid

    span = cumulative[high] - cumulative[low]
    ratio = (target - cumulative[low]) / span if span > 0 else 0.0
    start, end = coordinates[low], coordinates[high]
    return [start[0] + ratio * (end[0] - start[0]), start[1] + ratio * (end[1] - start[1])]


@dataclass(frozen=True)
class Checkpoint:
    offset_miles: float
    position: Coordinate


def sample_checkpoints(
    coordinates: list[Coordinate],
    interval_miles: float,
    cumulative: list[float] | None = None,
) -> list[Checkpoint]:
    """Origin, every ``interval_miles``, then the destination.

    A checkpoint marks the start of the stretch it governs, so the final
    checkpoint is only added when the last interval boundary does not already
    land on the destination.
    """
    if interval_miles <= 0:
        raise ValueError("interval must be positive")
    cumulative = cumulative or cumulative_miles(coordinates)
    total = cumulative[-1]

    offsets = [0.0]
    step = float(interval_miles)
    while step < total:
        offsets.append(round(step, 6))
        step += interval_miles
    if abs(offsets[-1] - total) > 1e-6:
        offsets.append(round(total, 6))

    return [
        Checkpoint(offset_miles=offset, position=point_at_miles(coordinates, cumulative, offset))
        for offset in offsets
    ]


def checkpoint_count(total_miles: float, interval_miles: float) -> int:
    count = int(total_miles // interval_miles) + 1
    if abs((count - 1) * interval_miles - total_miles) > 1e-6:
        count += 1
    return max(1, count)


def choose_interval(
    total_miles: float, requested_miles: int, max_checkpoints: int
) -> tuple[int, bool]:
    """Coarsen the sampling interval if the requested one would blow up.

    Returns ``(interval_used, was_coarsened)``. The user's chosen interval is
    honoured whenever it fits, so this only fires on very long trips.
    """
    for candidate in _interval_ladder(requested_miles):
        if checkpoint_count(total_miles, candidate) <= max_checkpoints:
            return candidate, candidate != requested_miles
    return _interval_ladder(requested_miles)[-1], True


def _interval_ladder(requested_miles: int) -> list[int]:
    ladder = [10, 25, 50]
    start = ladder.index(requested_miles) if requested_miles in ladder else 0
    return ladder[start:] or [requested_miles]


def corridor_points(
    coordinates: list[Coordinate],
    step_miles: float,
    lateral_offsets_miles: tuple[float, ...],
    cumulative: list[float] | None = None,
) -> list[Coordinate]:
    """Grid along the route, offset perpendicular on both sides.

    Used for the weather heatmap: samples enough of the corridor to read as a
    band rather than a single line, without exploding the weather request
    count.
    """
    cumulative = cumulative or cumulative_miles(coordinates)
    total = cumulative[-1]
    if total <= 0:
        return [list(coordinates[0])]

    offsets: list[float] = []
    position = 0.0
    while position <= total:
        offsets.append(position)
        position += step_miles
    if offsets[-1] != total:
        offsets.append(total)

    points: list[Coordinate] = []
    for offset in offsets:
        centre = point_at_miles(coordinates, cumulative, offset)
        points.append(centre)
        if lateral_offsets_miles:
            perpendicular = _perpendicular_at(coordinates, cumulative, offset)
            for lateral in lateral_offsets_miles:
                if lateral == 0:
                    continue
                points.append(_shift(centre, perpendicular, lateral))
    return _dedupe(points)


def _perpendicular_at(
    coordinates: list[Coordinate], cumulative: list[float], offset_miles: float
) -> tuple[float, float]:
    total = cumulative[-1]
    back = min(2.0, offset_miles, total / 2)
    ahead = min(2.0, total - offset_miles, total / 2)
    start = point_at_miles(coordinates, cumulative, max(0.0, offset_miles - back))
    end = point_at_miles(coordinates, cumulative, min(total, offset_miles + ahead))

    dx, dy = end[0] - start[0], end[1] - start[1]
    norm = math.hypot(dx, dy)
    if norm == 0:
        return (0.0, 1.0)
    return (-dy / norm, dx / norm)


def _shift(point: Coordinate, direction: tuple[float, float], miles: float) -> Coordinate:
    miles_per_degree_lat = 69.0
    miles_per_degree_lon = 69.0 * max(0.15, math.cos(math.radians(point[1])))
    return [
        point[0] + direction[0] * miles / miles_per_degree_lon,
        point[1] + direction[1] * miles / miles_per_degree_lat,
    ]


def _dedupe(points: list[Coordinate], precision: int = 3) -> list[Coordinate]:
    seen: set[tuple[float, float]] = set()
    unique: list[Coordinate] = []
    for point in points:
        key = (round(point[0], precision), round(point[1], precision))
        if key in seen:
            continue
        seen.add(key)
        unique.append([round(point[0], 4), round(point[1], 4)])
    return unique


def offset_waypoint(
    coordinates: list[Coordinate],
    fraction: float,
    lateral_miles: float,
    cumulative: list[float] | None = None,
) -> Coordinate:
    """A point beside the route, used to force an alternate corridor."""
    cumulative = cumulative or cumulative_miles(coordinates)
    offset = cumulative[-1] * min(max(fraction, 0.0), 1.0)
    centre = point_at_miles(coordinates, cumulative, offset)
    direction = _perpendicular_at(coordinates, cumulative, offset)
    return _shift(centre, direction, lateral_miles)


def overlap_fraction(
    a: list[Coordinate], b: list[Coordinate], tolerance_miles: float = 1.0
) -> float:
    """Share of route ``a`` that runs within ``tolerance_miles`` of route ``b``.

    Used to reject duplicate alternatives. Coarse by design: geometry is
    thinned and bucketed on a grid, so this is cheap and stable enough for a
    dedupe decision.
    """
    if not a or not b:
        return 0.0

    cell = 0.05
    buckets: dict[tuple[int, int], list[Coordinate]] = {}
    for lon, lat in b:
        buckets.setdefault((round(lon / cell), round(lat / cell)), []).append((lon, lat))

    def is_near(lon: float, lat: float) -> bool:
        cx, cy = round(lon / cell), round(lat / cell)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for other_lon, other_lat in buckets.get((cx + dx, cy + dy), ()):
                    miles_lat = (lat - other_lat) * 69.0
                    miles_lon = (lon - other_lon) * 69.0 * math.cos(math.radians(lat))
                    if miles_lat**2 + miles_lon**2 < tolerance_miles**2:
                        return True
        return False

    step = max(1, len(a) // 400)
    sample = a[::step]
    return sum(1 for point in sample if is_near(point[0], point[1])) / len(sample)
