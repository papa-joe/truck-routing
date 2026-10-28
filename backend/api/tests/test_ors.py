"""Routing client tests: parsing, ETA curve, retries and candidate de-duplication."""

from unittest import mock

import requests
from django.test import SimpleTestCase, override_settings

from api import ors
from api.exceptions import ProviderError


def coordinates_north(lon: float, count: int = 11) -> list[list[float]]:
    return [[lon, 39.0 + index / 10] for index in range(count)]


def feature(coords: list[list[float]], duration_s: float, steps: int = 2) -> dict:
    segment_steps = []
    chunk = (len(coords) - 1) // steps
    for index in range(steps):
        start = index * chunk
        end = len(coords) - 1 if index == steps - 1 else (index + 1) * chunk
        segment_steps.append(
            {
                "duration": duration_s / steps,
                "distance": 1000.0,
                "way_points": [start, end],
            }
        )
    return {
        "features": [
            {
                "geometry": {"coordinates": coords},
                "properties": {
                    "summary": {"distance": 20000.0, "duration": duration_s},
                    "segments": [{"steps": segment_steps}],
                },
            }
        ]
    }


class ParseRouteTests(SimpleTestCase):
    def test_summary_is_converted_to_miles_and_minutes(self):
        route = ors._parse_route(feature(coordinates_north(0.0), 1800))
        self.assertAlmostEqual(route.distance_miles, 20000 / 1609.344, places=1)
        self.assertAlmostEqual(route.duration_minutes, 30.0, places=1)

    def test_progress_is_monotonic_and_ends_at_the_destination(self):
        route = ors._parse_route(feature(coordinates_north(0.0), 1800))
        miles = [point[0] for point in route.progress]
        minutes = [point[1] for point in route.progress]
        self.assertEqual(miles, sorted(miles))
        self.assertEqual(minutes, sorted(minutes))
        self.assertAlmostEqual(route.progress[-1][1], route.duration_minutes, places=1)

    def test_missing_steps_falls_back_to_constant_speed(self):
        payload = feature(coordinates_north(0.0), 1800)
        payload["features"][0]["properties"]["segments"] = []
        route = ors._parse_route(payload)
        self.assertEqual(len(route.progress), 2)
        self.assertAlmostEqual(route.progress[-1][0], route.distance_miles, places=1)

    def test_empty_payload_is_an_error(self):
        with self.assertRaises(ProviderError):
            ors._parse_route({"features": []})

    def test_elapsed_minutes_interpolates_between_steps(self):
        route = ors.RouteCandidate(
            id="x",
            label="Fastest",
            geometry=[[0.0, 0.0]],
            distance_miles=100.0,
            duration_minutes=120.0,
            progress=[(0.0, 0.0), (50.0, 30.0), (100.0, 120.0)],
        )
        self.assertAlmostEqual(route.elapsed_minutes_at(0), 0.0)
        self.assertAlmostEqual(route.elapsed_minutes_at(50), 30.0)
        self.assertAlmostEqual(route.elapsed_minutes_at(25), 15.0)
        self.assertAlmostEqual(route.elapsed_minutes_at(75), 75.0)
        self.assertAlmostEqual(route.elapsed_minutes_at(999), 120.0)

    def test_elapsed_minutes_without_progress_is_zero(self):
        route = ors.RouteCandidate(
            id="x", label="Fastest", geometry=[], distance_miles=1, duration_minutes=1
        )
        self.assertEqual(route.elapsed_minutes_at(0.5), 0.0)


@override_settings(ORS_API_KEY="test-key", ORS_BASE_URL="https://example.test")
class RequestTests(SimpleTestCase):
    def _response(self, status: int, payload: dict, headers: dict | None = None):
        response = mock.Mock()
        response.status_code = status
        response.headers = headers or {}
        response.json.return_value = payload
        return response

    @mock.patch("api.ors.requests.post")
    def test_uses_the_hgv_profile(self, post):
        post.return_value = self._response(200, feature(coordinates_north(0.0), 600))
        ors._route([[0.0, 39.0], [0.0, 40.0]])
        url = post.call_args.args[0]
        self.assertIn("/v2/directions/driving-hgv/geojson", url)

    @mock.patch("api.ors.requests.post")
    def test_avoid_features_are_nested_under_options(self, post):
        post.return_value = self._response(200, feature(coordinates_north(0.0), 600))
        ors._route([[0.0, 39.0], [0.0, 40.0]], avoid_features=["tollways"])
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["options"], {"avoid_features": ["tollways"]})

    @mock.patch("api.ors.requests.post")
    def test_rate_limit_becomes_a_retryable_provider_error(self, post):
        post.return_value = self._response(429, {}, {"x-ratelimit-reset": "0"})
        with self.assertRaises(ProviderError) as caught:
            ors._route([[0.0, 39.0], [0.0, 40.0]])
        self.assertEqual(caught.exception.status, 503)
        self.assertIsNotNone(caught.exception.retry_after)

    @mock.patch("api.ors.requests.post")
    def test_server_errors_are_retried_once(self, post):
        post.side_effect = [
            self._response(500, {}),
            self._response(200, feature(coordinates_north(0.0), 600)),
        ]
        route = ors._route([[0.0, 39.0], [0.0, 40.0]])
        self.assertEqual(post.call_count, 2)
        self.assertGreater(route.distance_miles, 0)

    @mock.patch("api.ors.requests.post")
    def test_persistent_server_errors_raise(self, post):
        post.return_value = self._response(500, {})
        with self.assertRaises(ProviderError):
            ors._route([[0.0, 39.0], [0.0, 40.0]])
        self.assertEqual(post.call_count, 2)

    @mock.patch("api.ors.requests.post")
    def test_unroutable_waypoint_gives_a_helpful_message(self, post):
        post.return_value = self._response(
            404,
            {
                "error": {
                    "code": 2010,
                    "message": "Could not find routable point within a radius of 350.0 meters",
                }
            },
        )
        with self.assertRaises(ProviderError) as caught:
            ors._route([[0.0, 39.0], [0.0, 40.0]])
        self.assertEqual(caught.exception.status, 422)
        self.assertIn("truck-accessible road", caught.exception.message)

    @mock.patch("api.ors.requests.post")
    def test_network_failure_is_retried_then_reported(self, post):
        post.side_effect = requests.ConnectionError("boom")
        with self.assertRaises(ProviderError):
            ors._route([[0.0, 39.0], [0.0, 40.0]])
        self.assertEqual(post.call_count, 2)


@override_settings(ORS_API_KEY="test-key")
class CandidateTests(SimpleTestCase):
    def _candidate(self, route_id: str, lon: float) -> ors.RouteCandidate:
        return ors.RouteCandidate(
            id=route_id,
            label="Fastest",
            geometry=coordinates_north(lon),
            distance_miles=100.0,
            duration_minutes=120.0,
            progress=[(0.0, 0.0), (100.0, 120.0)],
        )

    def test_three_distinct_candidates_are_returned(self):
        base = self._candidate("base", 0.0)
        toll_free = self._candidate("toll", 0.6)
        detour = self._candidate("detour", -0.6)

        def fake_route(coordinates, avoid_features=None, radiuses=None):
            if len(coordinates) == 3:
                return detour
            return toll_free if avoid_features else base

        with mock.patch("api.ors._route", side_effect=fake_route):
            candidates = ors.build_candidates([0.0, 39.0], [0.0, 40.0])

        self.assertEqual([candidate.id for candidate in candidates], ["r1", "r2", "r3"])
        self.assertEqual(candidates[0].label, "Fastest")
        self.assertEqual(candidates[1].label, "Avoids tolls")

    def test_duplicate_toll_route_is_discarded(self):
        base = self._candidate("base", 0.0)
        identical = self._candidate("identical", 0.0)
        detour = self._candidate("detour", -0.6)

        def fake_route(coordinates, avoid_features=None, radiuses=None):
            if len(coordinates) == 3:
                return detour
            return identical if avoid_features else base

        with mock.patch("api.ors._route", side_effect=fake_route):
            candidates = ors.build_candidates([0.0, 39.0], [0.0, 40.0])

        self.assertEqual(len(candidates), 2)
        self.assertNotEqual(candidates[1].label, "Avoids tolls")

    def test_detour_waypoints_are_snapped_with_a_radius(self):
        base = self._candidate("base", 0.0)
        detour = self._candidate("detour", -0.6)
        seen: list[list[int] | None] = []

        def fake_route(coordinates, avoid_features=None, radiuses=None):
            seen.append(radiuses)
            return detour if len(coordinates) == 3 else base

        with mock.patch("api.ors._route", side_effect=fake_route):
            ors.build_candidates([0.0, 39.0], [0.0, 40.0])

        self.assertIn([-1, ors.VIA_SNAP_RADIUS_METERS, -1], seen)

    def test_no_duplicates_are_invented_when_detours_fail(self):
        base = self._candidate("base", 0.0)

        def fake_route(coordinates, avoid_features=None, radiuses=None):
            if len(coordinates) == 3:
                raise ProviderError("no routable point", status=422)
            return base

        with mock.patch("api.ors._route", side_effect=fake_route):
            candidates = ors.build_candidates([0.0, 39.0], [0.0, 40.0])

        self.assertEqual(len(candidates), 1)
