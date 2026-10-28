"""Endpoint contract tests: validation, caching, error envelope."""

from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from api.exceptions import ProviderError

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def valid_payload(**overrides):
    payload = {
        "origin": {"lat": 39.7392, "lon": -104.9903, "label": "Denver, Colorado"},
        "destination": {"lat": 39.0997, "lon": -94.5786, "label": "Kansas City, Missouri"},
        "departureAt": (timezone.now() + timedelta(hours=2)).isoformat(),
        "loadLbs": 34_000,
        "intervalMiles": 25,
    }
    payload.update(overrides)
    return payload


FAKE_RESULT = {
    "recommendedRouteId": "r1",
    "routes": [],
    "heatmap": {"hours": [], "cells": []},
    "notices": [],
}


@override_settings(CACHES=LOCMEM)
class PlanValidationTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def test_missing_fields_are_reported_per_field(self):
        response = self.client.post("/api/plan/", {"origin": {"lat": 1, "lon": 1}}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "invalid_request")
        self.assertIn("destination", response.data["error"]["fields"])

    def test_unsupported_interval_is_rejected(self):
        response = self.client.post("/api/plan/", valid_payload(intervalMiles=15), format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("intervalMiles", response.data["error"]["fields"])

    def test_negative_load_is_rejected(self):
        response = self.client.post("/api/plan/", valid_payload(loadLbs=-10), format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("loadLbs", response.data["error"]["fields"])

    def test_past_departure_is_rejected(self):
        past = (timezone.now() - timedelta(days=1)).isoformat()
        response = self.client.post("/api/plan/", valid_payload(departureAt=past), format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("departureAt", response.data["error"]["fields"])

    def test_departure_beyond_the_forecast_window_is_rejected(self):
        far = (timezone.now() + timedelta(days=30)).isoformat()
        response = self.client.post("/api/plan/", valid_payload(departureAt=far), format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("departureAt", response.data["error"]["fields"])

    def test_naive_departure_is_rejected(self):
        naive = (timezone.now() + timedelta(hours=2)).replace(tzinfo=None).isoformat()
        response = self.client.post("/api/plan/", valid_payload(departureAt=naive), format="json")
        self.assertEqual(response.status_code, 400)

    def test_identical_endpoints_are_rejected(self):
        payload = valid_payload(destination={"lat": 39.7392, "lon": -104.9903})
        response = self.client.post("/api/plan/", payload, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("destination", response.data["error"]["fields"])

    def test_oversized_route_is_rejected(self):
        payload = valid_payload(origin={"lat": 30, "lon": -120}, destination={"lat": 30, "lon": 0})
        response = self.client.post("/api/plan/", payload, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("destination", response.data["error"]["fields"])

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get("/api/plan/").status_code, 405)


@override_settings(CACHES=LOCMEM)
class PlanResponseTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    @mock.patch("api.views.services.plan_trip", return_value=FAKE_RESULT)
    def test_successful_plan_returns_provider_result(self, plan_trip):
        response = self.client.post("/api/plan/", valid_payload(), format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["recommendedRouteId"], "r1")
        plan_trip.assert_called_once()

    @mock.patch("api.views.services.plan_trip", return_value=FAKE_RESULT)
    def test_repeat_requests_use_the_cache(self, plan_trip):
        self.client.post("/api/plan/", valid_payload(), format="json")
        self.client.post("/api/plan/", valid_payload(), format="json")
        self.assertEqual(plan_trip.call_count, 1)

    @mock.patch("api.views.services.plan_trip", return_value=FAKE_RESULT)
    def test_different_interval_is_a_different_plan(self, plan_trip):
        self.client.post("/api/plan/", valid_payload(intervalMiles=10), format="json")
        self.client.post("/api/plan/", valid_payload(intervalMiles=50), format="json")
        self.assertEqual(plan_trip.call_count, 2)

    @mock.patch("api.views.services.plan_trip")
    def test_routing_outage_is_reported_with_retry_after(self, plan_trip):
        plan_trip.side_effect = ProviderError(
            "Routing quota reached. Please try again later.",
            status=503,
            provider="routing",
            retry_after=42,
        )
        response = self.client.post("/api/plan/", valid_payload(), format="json")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["error"]["code"], "routing_unavailable")
        self.assertEqual(response["Retry-After"], "42")

    @mock.patch("api.views.services.plan_trip")
    def test_unexpected_errors_do_not_leak_internals(self, plan_trip):
        plan_trip.side_effect = RuntimeError("database password is hunter2")
        response = self.client.post("/api/plan/", valid_payload(), format="json")
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("hunter2", response.content.decode())
        self.assertEqual(response.data["error"]["code"], "internal_error")


class HealthTests(SimpleTestCase):
    def test_health(self):
        response = APIClient().get("/api/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


@override_settings(CACHES=LOCMEM)
class GeocodeTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    @mock.patch("api.views.services.geocode", return_value=[{"label": "Denver, Colorado"}])
    def test_query_is_forwarded(self, geocode):
        response = APIClient().get("/api/geocode/?q=denver")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["label"], "Denver, Colorado")
        geocode.assert_called_once_with("denver")

    @mock.patch("api.views.services.geocode")
    def test_geocoding_outage_is_reported(self, geocode):
        geocode.side_effect = ProviderError("Place search is unavailable.", provider="geocoding")
        response = APIClient().get("/api/geocode/?q=denver")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.data["error"]["code"], "geocoding_unavailable")
