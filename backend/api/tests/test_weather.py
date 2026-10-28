"""Weather client tests: units, batching, caching and missing data."""

from datetime import UTC, date, datetime
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from api import weather

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def payload(
    times=("2026-10-27T00:00", "2026-10-27T01:00"),
    wind=(10.0, 12.0),
    rain=(0.0, 0.2),
    snow=(0.0, None),
):
    return {
        "hourly": {
            "time": list(times),
            "wind_speed_10m": list(wind),
            "rain": list(rain),
            "snowfall": list(snow),
        }
    }


@override_settings(CACHES=LOCMEM)
class ParseTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_single_location_object(self):
        parsed = weather._parse(payload(), [(39.0, -105.0)])
        series = parsed[(39.0, -105.0)]
        first = series[datetime(2026, 10, 27, 0, tzinfo=UTC)]
        self.assertEqual(first.wind_mph, 10.0)
        self.assertEqual(first.rain_in_hr, 0.0)

    def test_multiple_locations_return_a_list(self):
        parsed = weather._parse(
            [payload(), payload(wind=(30.0, 30.0))], [(39.0, -105.0), (38.0, -100.0)]
        )
        self.assertEqual(
            parsed[(39.0, -105.0)][datetime(2026, 10, 27, 0, tzinfo=UTC)].wind_mph, 10.0
        )
        self.assertEqual(
            parsed[(38.0, -100.0)][datetime(2026, 10, 27, 0, tzinfo=UTC)].wind_mph, 30.0
        )

    def test_null_values_become_none_not_zero(self):
        parsed = weather._parse(payload(snow=(None, None)), [(39.0, -105.0)])
        series = parsed[(39.0, -105.0)]
        self.assertIsNone(series[datetime(2026, 10, 27, 0, tzinfo=UTC)].snow_in_hr)

    def test_missing_variable_array_is_handled(self):
        broken = {"hourly": {"time": ["2026-10-27T00:00"], "wind_speed_10m": []}}
        parsed = weather._parse(broken, [(39.0, -105.0)])
        sample = parsed[(39.0, -105.0)][datetime(2026, 10, 27, 0, tzinfo=UTC)]
        self.assertIsNone(sample.wind_mph)


class LookupTests(SimpleTestCase):
    def test_conditions_are_floored_to_the_hour(self):
        series = {datetime(2026, 10, 27, 14, tzinfo=UTC): weather.Conditions(10.0, 0.0, 0.0)}
        found = weather.conditions_at(series, datetime(2026, 10, 27, 14, 45, tzinfo=UTC))
        self.assertIsNotNone(found)
        self.assertEqual(found.wind_mph, 10.0)

    def test_unknown_hour_returns_none(self):
        series = {datetime(2026, 10, 27, 14, tzinfo=UTC): weather.Conditions(10.0, 0.0, 0.0)}
        self.assertIsNone(weather.conditions_at(series, datetime(2026, 10, 28, 2, tzinfo=UTC)))

    def test_missing_series_returns_none(self):
        self.assertIsNone(weather.conditions_at(None, datetime.now(UTC)))

    def test_hourly_window_is_contiguous(self):
        window = weather.hourly_window(datetime(2026, 10, 27, 13, 35, tzinfo=UTC), 49)
        self.assertEqual(len(window), 49)
        self.assertEqual(window[0].minute, 0)
        self.assertEqual((window[-1] - window[0]).total_seconds(), 48 * 3600)


@override_settings(CACHES=LOCMEM)
class FetchTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    @mock.patch("api.weather._request")
    def test_results_are_cached_between_calls(self, request):
        request.return_value = payload()
        points = [[-105.0, 39.0], [-104.5, 39.2]]
        weather.fetch_series(points, date(2026, 10, 27), 2)
        weather.fetch_series(points, date(2026, 10, 27), 2)
        self.assertEqual(request.call_count, 1)

    @mock.patch("api.weather._request")
    def test_points_are_chunked_into_separate_requests(self, request):
        request.side_effect = lambda params: [
            payload() for _ in range(len(params["latitude"].split(",")))
        ]
        points = [[-105.0 + index / 100, 39.0] for index in range(weather.CHUNK_SIZE + 5)]
        weather.fetch_series(points, date(2026, 10, 27), 2)
        self.assertEqual(request.call_count, 2)

    @mock.patch("api.weather._request")
    def test_units_are_requested_in_mph_and_inches(self, request):
        request.return_value = payload()
        weather.fetch_series([[-105.0, 39.0]], date(2026, 10, 27), 2)
        params = request.call_args.args[0]
        self.assertEqual(params["wind_speed_unit"], "mph")
        self.assertEqual(params["precipitation_unit"], "inch")
        self.assertIn("snowfall", params["hourly"])

    @mock.patch("api.weather._request")
    def test_duplicate_points_hit_the_api_once(self, request):
        request.return_value = payload()
        weather.fetch_series(
            [[-105.0, 39.0], [-105.0, 39.0], [-105.0001, 39.0001]], date(2026, 10, 27), 2
        )
        params = request.call_args.args[0]
        self.assertEqual(len(params["latitude"].split(",")), 1)
