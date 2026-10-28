"""End-to-end orchestration tests with the providers stubbed out.

These cover the seam that unit tests miss: checkpoint ETAs deciding which
forecast hour is read, that severity flowing into mileage buckets, and the
ranked order that reaches the client.
"""

from datetime import UTC, datetime, timedelta
from unittest import mock

from django.test import SimpleTestCase, override_settings

from api import ors, risk, services, weather

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

DEPARTURE = datetime(2026, 5, 1, 6, 0, tzinfo=UTC)

SETTINGS = {
    "CACHES": LOCMEM,
    "MAX_CHECKPOINTS_PER_ROUTE": 400,
    "HEATMAP_HOURS": 49,
    "HEATMAP_SAMPLE_MILES": 6,
    "HEATMAP_LATERAL_OFFSETS_MILES": (0,),
    "MAX_ROUTE_MILES": 3500,
}


def candidate(route_id: str, lon: float, duration_minutes: float) -> ors.RouteCandidate:
    geometry = [[lon, 39.0 + index * 0.44] for index in range(5)]
    return ors.RouteCandidate(
        id=route_id,
        label="Fastest",
        geometry=geometry,
        distance_miles=121.6,
        duration_minutes=duration_minutes,
        progress=[
            (0.0, 0.0),
            (30.4, duration_minutes * 0.25),
            (60.8, duration_minutes * 0.5),
            (91.2, duration_minutes * 0.75),
            (121.6, duration_minutes),
        ],
    )


def hourly_series(conditions: weather.Conditions, hours: int = 96) -> weather.Series:
    start = DEPARTURE.replace(minute=0, second=0, microsecond=0) - timedelta(hours=6)
    return {start + timedelta(hours=offset): conditions for offset in range(hours)}


def fake_weather_fetch(points, start, days):
    """Route 1 runs through bad weather; route 2 stays clear."""
    result = {}
    for lon, lat in points:
        if lon < -99:
            if lat >= 40.0:
                conditions = weather.Conditions(wind_mph=62.0, rain_in_hr=0.0, snow_in_hr=0.0)
            elif lat >= 39.5:
                conditions = weather.Conditions(wind_mph=40.0, rain_in_hr=0.0, snow_in_hr=0.0)
            else:
                conditions = weather.Conditions(wind_mph=5.0, rain_in_hr=0.0, snow_in_hr=0.0)
        else:
            conditions = weather.Conditions(wind_mph=4.0, rain_in_hr=0.0, snow_in_hr=0.0)
        result[(round(lat, 3), round(lon, 3))] = hourly_series(conditions)
    return result


@override_settings(**SETTINGS)
class PlanTripTests(SimpleTestCase):
    def _plan(self, candidates, load_lbs=45_000, interval=50):
        with (
            mock.patch("api.services.ors.build_candidates", return_value=candidates),
            mock.patch("api.services.weather.fetch_series", side_effect=fake_weather_fetch),
        ):
            return services.plan_trip(
                origin=[-100.0, 39.0],
                destination=[-100.0, 40.76],
                departure=DEPARTURE,
                load_lbs=load_lbs,
                interval_miles=interval,
            )

    def test_checkpoint_severity_follows_the_forecast_at_its_eta(self):
        plan = self._plan([candidate("r1", -100.0, 120.0)])
        route = plan["routes"][0]
        severities = [checkpoint["severity"] for checkpoint in route["checkpoints"]]

        self.assertEqual(severities[0], "low")
        self.assertEqual(severities[1], "severe")  # 40 mph with a 45,000 lb load
        self.assertEqual(severities[2], "no_travel")
        self.assertEqual(severities[3], "no_travel")

    def test_severity_becomes_mileage_buckets(self):
        plan = self._plan([candidate("r1", -100.0, 120.0)])
        summary = plan["routes"][0]["summary"]

        self.assertAlmostEqual(summary["lowMiles"], 50.0, delta=2.0)
        self.assertAlmostEqual(summary["noTravelMiles"], 21.6, delta=2.0)
        # Severe miles include the no-travel stretch by design.
        self.assertAlmostEqual(
            summary["severeMiles"],
            summary["noTravelMiles"] + 50.0,
            delta=2.0,
        )
        self.assertAlmostEqual(summary["totalMiles"], 121.6, delta=0.5)

    def test_do_not_travel_route_is_not_recommended_even_when_faster(self):
        fast_and_dangerous = candidate("r1", -100.0, 120.0)
        slow_and_clear = candidate("r2", -95.0, 300.0)

        plan = self._plan([fast_and_dangerous, slow_and_clear])

        self.assertEqual(plan["recommendedRouteId"], "r2")
        self.assertEqual([route["id"] for route in plan["routes"]], ["r2", "r1"])
        self.assertEqual(plan["routes"][0]["rank"], 1)
        self.assertFalse(plan["routes"][1]["isRecommended"])

    def test_no_travel_is_surfaced_as_a_warning(self):
        plan = self._plan([candidate("r1", -100.0, 120.0)])
        warnings = plan["routes"][0]["warnings"]
        self.assertTrue(any("safe travel limits" in warning for warning in warnings))

    def test_missing_forecast_is_unknown_never_low(self):
        with (
            mock.patch(
                "api.services.ors.build_candidates", return_value=[candidate("r1", -100.0, 120.0)]
            ),
            mock.patch("api.services.weather.fetch_series", return_value={}),
        ):
            plan = services.plan_trip(
                origin=[-100.0, 39.0],
                destination=[-100.0, 40.76],
                departure=DEPARTURE,
                load_lbs=20_000,
                interval_miles=50,
            )

        summary = plan["routes"][0]["summary"]
        self.assertAlmostEqual(summary["unknownMiles"], summary["totalMiles"], delta=0.01)
        self.assertEqual(summary["lowMiles"], 0.0)
        self.assertEqual(summary["avgRisk"], risk.UNKNOWN.score)
        self.assertTrue(any("no forecast data" in w for w in plan["routes"][0]["warnings"]))

    def test_heatmap_is_independent_of_departure_time(self):
        plan = self._plan([candidate("r1", -100.0, 120.0)])
        heatmap = plan["heatmap"]

        self.assertEqual(len(heatmap["hours"]), 49)
        self.assertGreater(len(heatmap["cells"]), 0)
        first_hour = datetime.fromisoformat(heatmap["hours"][0])
        # Anchored to now, not to the trip's 06:00 departure.
        self.assertNotEqual(first_hour.hour, DEPARTURE.hour)
        self.assertGreater(first_hour, datetime.now(UTC) - timedelta(hours=2))

    def test_heatmap_cells_carry_one_value_per_hour(self):
        plan = self._plan([candidate("r1", -100.0, 120.0)])
        heatmap = plan["heatmap"]
        for cell in heatmap["cells"][:5]:
            self.assertEqual(len(cell["values"]), len(heatmap["hours"]))
            self.assertTrue(all(0 <= value <= 5 for value in cell["values"]))

    def test_response_is_json_ready(self):
        """No dataclasses or datetime objects may leak into the payload."""
        import json

        plan = self._plan([candidate("r1", -100.0, 120.0)])
        json.dumps(plan)  # raises TypeError if anything is unserialisable

    def test_single_route_produces_a_notice(self):
        plan = self._plan([candidate("r1", -100.0, 120.0)])
        self.assertTrue(any("distinct truck route" in notice for notice in plan["notices"]))

    def test_checkpoint_interval_is_echoed_back(self):
        plan = self._plan([candidate("r1", -100.0, 120.0)], interval=10)
        self.assertEqual(plan["intervalMiles"], 10)
        self.assertEqual(len(plan["routes"][0]["checkpoints"]), 14)
