"""Photon geocoding client (place search and type-ahead).

Photon is used rather than Nominatim because Nominatim's usage policy
forbids type-ahead search. Queries are proxied and cached server-side so the
browser never talks to a third party and repeat keystrokes cost nothing.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

import requests
from django.conf import settings
from django.core.cache import cache

from .exceptions import ProviderError

logger = logging.getLogger(__name__)

RESULT_LIMIT = 6
MIN_QUERY_LENGTH = 2


class Place(dict):
    """Serialised place, shaped for the frontend."""


def search(query: str, limit: int = RESULT_LIMIT) -> list[dict[str, Any]]:
    cleaned = " ".join(query.split())
    if len(cleaned) < MIN_QUERY_LENGTH:
        return []

    key = "geo:" + hashlib.sha256(f"{cleaned.lower()}:{limit}".encode()).hexdigest()[:24]
    cached = cache.get(key)
    if cached is not None:
        return cached

    payload = _request({"q": cleaned, "limit": limit, "lang": "en"})
    places = _parse(payload)
    cache.set(key, places, settings.GEOCODE_CACHE_SECONDS)
    return places


def _request(params: dict[str, Any]) -> dict[str, Any]:
    url = f"{settings.PHOTON_BASE_URL.rstrip('/')}/api"
    try:
        response = requests.get(
            url,
            params=params,
            headers={"User-Agent": "spotter-truck-routing/1.0"},
            timeout=(5, settings.PROVIDER_TIMEOUT_SECONDS),
        )
    except requests.RequestException as error:
        logger.warning("Geocoding request failed", exc_info=True)
        raise ProviderError(
            "Could not reach the place search service. Please try again.",
            provider="geocoding",
        ) from error

    if response.status_code == 429:
        raise ProviderError(
            "Place search is rate limited right now. Please try again shortly.",
            status=503,
            provider="geocoding",
            retry_after=10,
        )
    if response.status_code >= 400:
        logger.info("Geocoding request rejected with %s", response.status_code)
        raise ProviderError("Place search could not be completed.", provider="geocoding")

    return response.json()


def _parse(payload: dict[str, Any]) -> list[dict[str, Any]]:
    places: list[dict[str, Any]] = []
    seen: set[str] = set()

    for feature in payload.get("features") or []:
        coordinates = (feature.get("geometry") or {}).get("coordinates") or []
        if len(coordinates) < 2:
            continue
        properties = feature.get("properties") or {}
        label = _label(properties)
        if not label or label in seen:
            continue
        seen.add(label)
        places.append(
            {
                "label": label,
                "lat": round(float(coordinates[1]), 5),
                "lon": round(float(coordinates[0]), 5),
                "kind": properties.get("osm_value") or properties.get("type") or "place",
            }
        )

    return places


def _label(properties: dict[str, Any]) -> str:
    name = properties.get("name") or properties.get("street")
    locality = properties.get("city") or properties.get("district") or properties.get("county")
    region = properties.get("state") or properties.get("country")

    parts = [part for part in (name, locality, region) if part]
    deduped: list[str] = []
    for part in parts:
        if part not in deduped:
            deduped.append(part)
    return ", ".join(deduped)
