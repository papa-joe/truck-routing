"""HTTP layer. Validation, caching and response shaping only."""

from __future__ import annotations

import hashlib

from django.conf import settings
from django.core.cache import cache
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from . import services
from .serializers import PlanRequestSerializer


@api_view(["GET"])
def health(_request: Request) -> Response:
    return Response({"status": "ok"})


@api_view(["GET"])
def geocode(request: Request) -> Response:
    query = request.query_params.get("q", "")
    return Response({"results": services.geocode(query)})


@api_view(["POST"])
def plan(request: Request) -> Response:
    serializer = PlanRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    payload = serializer.validated_data

    origin = [payload["origin"]["lon"], payload["origin"]["lat"]]
    destination = [payload["destination"]["lon"], payload["destination"]["lat"]]

    key = _cache_key(origin, destination, payload)
    cached = cache.get(key)
    if cached is not None:
        return Response(cached)

    result = services.plan_trip(
        origin=origin,
        destination=destination,
        departure=payload["departureAt"],
        load_lbs=float(payload["loadLbs"]),
        interval_miles=int(payload["intervalMiles"]),
    )
    cache.set(key, result, settings.PLAN_CACHE_SECONDS)
    return Response(result)


def _cache_key(origin: list[float], destination: list[float], payload: dict) -> str:
    """Repeat views must not spend the routing quota.

    Coordinates are snapped to ~11 m, load to 1,000 lb and departure to the
    hour: identical enough that a cached plan is the same journey.
    """
    raw = "|".join(
        [
            f"{origin[0]:.4f},{origin[1]:.4f}",
            f"{destination[0]:.4f},{destination[1]:.4f}",
            payload["departureAt"].isoformat(timespec="hours"),
            f"{round(float(payload['loadLbs']) / 1000)}k",
            str(payload["intervalMiles"]),
        ]
    )
    return "plan:" + hashlib.sha256(raw.encode()).hexdigest()[:32]
