"""Boundary and composition tests for the risk table in SCOPE.md."""

from django.test import SimpleTestCase

from api import risk


class WindBoundaryTests(SimpleTestCase):
    def test_bands(self):
        cases = [
            (0.0, risk.LOW),
            (24.99, risk.LOW),
            (25.0, risk.MODERATE),
            (34.99, risk.MODERATE),
            (35.0, risk.HIGH),
            (44.99, risk.HIGH),
            (45.0, risk.SEVERE),
            (54.99, risk.SEVERE),
            (55.0, risk.NO_TRAVEL),
            (80.0, risk.NO_TRAVEL),
        ]
        for mph, expected in cases:
            with self.subTest(mph=mph):
                self.assertIs(risk.wind_severity(mph), expected)

    def test_missing_is_unknown(self):
        self.assertIs(risk.wind_severity(None), risk.UNKNOWN)


class RainBoundaryTests(SimpleTestCase):
    def test_bands(self):
        cases = [
            (0.0, risk.LOW),
            (0.09, risk.LOW),
            (0.10, risk.MODERATE),
            (0.249, risk.MODERATE),
            (0.25, risk.HIGH),
            (0.499, risk.HIGH),
            (0.50, risk.SEVERE),
            (1.00, risk.SEVERE),
            (1.01, risk.NO_TRAVEL),
        ]
        for rate, expected in cases:
            with self.subTest(rate=rate):
                self.assertIs(risk.rain_severity(rate), expected)

    def test_missing_is_unknown(self):
        self.assertIs(risk.rain_severity(None), risk.UNKNOWN)


class SnowBoundaryTests(SimpleTestCase):
    def test_bands(self):
        cases = [
            (0.0, risk.LOW),
            (0.49, risk.LOW),
            (0.5, risk.MODERATE),
            (0.99, risk.MODERATE),
            (1.0, risk.HIGH),
            (1.99, risk.HIGH),
            (2.0, risk.SEVERE),
            (3.0, risk.SEVERE),
            (3.01, risk.NO_TRAVEL),
        ]
        for rate, expected in cases:
            with self.subTest(rate=rate):
                self.assertIs(risk.snow_severity(rate), expected)


class LoadRuleTests(SimpleTestCase):
    def test_high_wind_is_no_travel_for_any_load(self):
        for load in (0, 10_000, 30_000, 80_000):
            with self.subTest(load=load):
                self.assertIs(risk.apply_load_rules(risk.LOW, 60.0, load), risk.NO_TRAVEL)

    def test_severe_wind_with_heavy_load_becomes_no_travel(self):
        self.assertIs(risk.apply_load_rules(risk.SEVERE, 50.0, 30_001), risk.NO_TRAVEL)
        self.assertIs(risk.apply_load_rules(risk.SEVERE, 50.0, 80_000), risk.NO_TRAVEL)

    def test_severe_wind_at_exactly_30000_lb_stays_severe(self):
        self.assertIs(risk.apply_load_rules(risk.SEVERE, 50.0, 30_000), risk.SEVERE)

    def test_high_wind_with_very_heavy_load_becomes_severe(self):
        self.assertIs(risk.apply_load_rules(risk.HIGH, 40.0, 40_001), risk.SEVERE)

    def test_high_wind_at_exactly_40000_lb_stays_high(self):
        self.assertIs(risk.apply_load_rules(risk.HIGH, 40.0, 40_000), risk.HIGH)

    def test_light_load_is_unaffected(self):
        self.assertIs(risk.apply_load_rules(risk.MODERATE, 30.0, 20_000), risk.MODERATE)

    def test_load_rules_never_improve_a_verdict(self):
        self.assertIs(risk.apply_load_rules(risk.SEVERE, 20.0, 500), risk.SEVERE)

    def test_unknown_wind_leaves_severity_alone(self):
        self.assertIs(risk.apply_load_rules(risk.SEVERE, None, 80_000), risk.SEVERE)


class CheckpointClassificationTests(SimpleTestCase):
    def test_worst_condition_wins(self):
        severity = risk.classify_checkpoint(
            wind_mph=20.0, rain_in_hr=0.30, snow_in_hr=0.0, load_lbs=10_000
        )
        self.assertIs(severity, risk.HIGH)

    def test_rain_and_snow_are_independent(self):
        severity = risk.classify_checkpoint(
            wind_mph=5.0, rain_in_hr=0.0, snow_in_hr=2.5, load_lbs=10_000
        )
        self.assertIs(severity, risk.SEVERE)

    def test_heavy_load_upgrades_high_wind(self):
        severity = risk.classify_checkpoint(
            wind_mph=40.0, rain_in_hr=0.0, snow_in_hr=0.0, load_lbs=45_000
        )
        self.assertIs(severity, risk.SEVERE)

    def test_heavy_load_in_severe_wind_blocks_travel(self):
        severity = risk.classify_checkpoint(
            wind_mph=48.0, rain_in_hr=0.0, snow_in_hr=0.0, load_lbs=35_000
        )
        self.assertIs(severity, risk.NO_TRAVEL)

    def test_missing_condition_is_unknown_not_safe(self):
        severity = risk.classify_checkpoint(
            wind_mph=10.0, rain_in_hr=None, snow_in_hr=0.0, load_lbs=10_000
        )
        self.assertIs(severity, risk.UNKNOWN)

    def test_confirmed_no_travel_survives_missing_data(self):
        severity = risk.classify_checkpoint(
            wind_mph=60.0, rain_in_hr=None, snow_in_hr=None, load_lbs=10_000
        )
        self.assertIs(severity, risk.NO_TRAVEL)

    def test_unknown_is_worse_than_moderate_but_not_high(self):
        self.assertGreater(risk.UNKNOWN.score, risk.MODERATE.score)
        self.assertLess(risk.UNKNOWN.score, risk.HIGH.score)
        self.assertGreater(risk.UNKNOWN.score, risk.LOW.score)


class SummarizeTests(SimpleTestCase):
    def test_buckets_and_average(self):
        summary = risk.summarize(
            [
                risk.Leg(10.0, risk.LOW),
                risk.Leg(10.0, risk.MODERATE),
                risk.Leg(10.0, risk.HIGH),
                risk.Leg(10.0, risk.SEVERE),
            ]
        )
        self.assertAlmostEqual(summary.total_miles, 40.0)
        self.assertAlmostEqual(summary.low_miles, 10.0)
        self.assertAlmostEqual(summary.high_miles, 10.0)
        self.assertAlmostEqual(summary.severe_miles, 10.0)
        self.assertAlmostEqual(summary.avg_risk, (0 + 1 + 2 + 3) / 4)

    def test_no_travel_counts_as_severe_miles(self):
        summary = risk.summarize([risk.Leg(5.0, risk.NO_TRAVEL), risk.Leg(5.0, risk.LOW)])
        self.assertAlmostEqual(summary.no_travel_miles, 5.0)
        self.assertAlmostEqual(summary.severe_miles, 5.0)
        self.assertTrue(summary.has_no_travel)

    def test_unknown_miles_are_tracked_separately(self):
        summary = risk.summarize([risk.Leg(8.0, risk.UNKNOWN), risk.Leg(2.0, risk.LOW)])
        self.assertAlmostEqual(summary.unknown_miles, 8.0)
        self.assertAlmostEqual(summary.unknown_fraction, 0.8)
        self.assertAlmostEqual(summary.avg_risk, (1.5 * 8 + 0.0 * 2) / 10)

    def test_zero_miles_is_safe(self):
        summary = risk.summarize([])
        self.assertEqual(summary.total_miles, 0.0)
        self.assertEqual(summary.avg_risk, 0.0)


class RankingTests(SimpleTestCase):
    def _route(self, route_id, severe, high, avg, duration, total=100.0):
        summary = risk.summarize(
            [
                risk.Leg(severe, risk.SEVERE),
                risk.Leg(high, risk.HIGH),
                risk.Leg(max(0.0, total - severe - high), risk.LOW),
            ]
        )
        return risk.Rankable(id=route_id, summary=summary, duration_minutes=duration)

    def test_fewest_severe_miles_wins_first(self):
        risky = self._route("a", severe=30, high=0, avg=0, duration=100)
        safer = self._route("b", severe=10, high=0, avg=0, duration=500)
        ranked = risk.rank_routes([risky, safer])
        self.assertEqual([route.id for route in ranked], ["b", "a"])

    def test_severe_beats_high_regardless_of_totals(self):
        a = self._route("a", severe=0, high=90, avg=0, duration=100)
        b = self._route("b", severe=5, high=0, avg=0, duration=900)
        ranked = risk.rank_routes([a, b])
        self.assertEqual(ranked[0].id, "a")

    def test_high_miles_break_a_severe_tie(self):
        a = self._route("a", severe=10, high=40, avg=0, duration=100)
        b = self._route("b", severe=10, high=5, avg=0, duration=900)
        ranked = risk.rank_routes([a, b])
        self.assertEqual(ranked[0].id, "b")

    def test_average_risk_breaks_a_high_tie(self):
        """Equal severe and high miles: the lower average risk wins even if slower."""
        cleaner = risk.Rankable(
            id="cleaner",
            summary=risk.summarize(
                [
                    risk.Leg(10.0, risk.SEVERE),
                    risk.Leg(10.0, risk.HIGH),
                    risk.Leg(80.0, risk.LOW),
                ]
            ),
            duration_minutes=900,
        )
        rougher = risk.Rankable(
            id="rougher",
            summary=risk.summarize(
                [
                    risk.Leg(10.0, risk.SEVERE),
                    risk.Leg(10.0, risk.HIGH),
                    risk.Leg(80.0, risk.MODERATE),
                ]
            ),
            duration_minutes=100,
        )
        ranked = risk.rank_routes([rougher, cleaner])
        self.assertEqual([route.id for route in ranked], ["cleaner", "rougher"])

    def test_duration_is_the_last_tie_break(self):
        fast = risk.Rankable(
            id="fast",
            summary=risk.summarize([risk.Leg(100.0, risk.LOW)]),
            duration_minutes=300,
        )
        slow = risk.Rankable(
            id="slow",
            summary=risk.summarize([risk.Leg(100.0, risk.LOW)]),
            duration_minutes=600,
        )
        ranked = risk.rank_routes([slow, fast])
        self.assertEqual(ranked[0].id, "fast")

    def test_weighted_score_is_not_used(self):
        """A route with more severe miles must lose even with a better average."""
        severe_but_short = risk.Rankable(
            id="severe",
            summary=risk.summarize([risk.Leg(50.0, risk.SEVERE)]),
            duration_minutes=60,
        )
        clean_but_long = risk.Rankable(
            id="clean",
            summary=risk.summarize([risk.Leg(500.0, risk.MODERATE)]),
            duration_minutes=600,
        )
        ranked = risk.rank_routes([severe_but_short, clean_but_long])
        self.assertEqual(ranked[0].id, "clean")
