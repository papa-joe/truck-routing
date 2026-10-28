"""Geometry and checkpoint sampling tests."""

from django.test import SimpleTestCase

from api import geo


def straight_route(lat_start: float, lat_end: float, steps: int = 10) -> list[list[float]]:
    return [[0.0, lat_start + (lat_end - lat_start) * index / steps] for index in range(steps + 1)]


class DistanceTests(SimpleTestCase):
    def test_one_degree_of_latitude(self):
        self.assertAlmostEqual(geo.haversine_miles([0.0, 0.0], [0.0, 1.0]), 69.09, delta=0.2)

    def test_cumulative_is_monotonic(self):
        cumulative = geo.cumulative_miles(straight_route(39.0, 40.0))
        self.assertEqual(cumulative[0], 0.0)
        self.assertEqual(cumulative, sorted(cumulative))
        self.assertAlmostEqual(cumulative[-1], 69.09, delta=0.3)

    def test_point_at_miles_interpolates(self):
        coordinates = straight_route(39.0, 40.0, steps=1)
        cumulative = geo.cumulative_miles(coordinates)
        midpoint = geo.point_at_miles(coordinates, cumulative, cumulative[-1] / 2)
        self.assertAlmostEqual(midpoint[1], 39.5, places=2)

    def test_point_at_miles_clamps(self):
        coordinates = straight_route(39.0, 40.0)
        cumulative = geo.cumulative_miles(coordinates)
        self.assertEqual(geo.point_at_miles(coordinates, cumulative, -5), [0.0, 39.0])
        self.assertEqual(geo.point_at_miles(coordinates, cumulative, 10_000), [0.0, 40.0])


class CheckpointTests(SimpleTestCase):
    def setUp(self):
        # ~277 miles, close enough to exercise every interval.
        self.coordinates = straight_route(39.0, 43.0, steps=40)
        self.cumulative = geo.cumulative_miles(self.coordinates)
        self.total = self.cumulative[-1]

    def test_starts_at_origin_and_ends_at_destination(self):
        checkpoints = geo.sample_checkpoints(self.coordinates, 25, self.cumulative)
        self.assertAlmostEqual(checkpoints[0].offset_miles, 0.0)
        self.assertAlmostEqual(checkpoints[-1].offset_miles, self.total, places=3)

    def test_interval_is_respected(self):
        checkpoints = geo.sample_checkpoints(self.coordinates, 50, self.cumulative)
        offsets = [checkpoint.offset_miles for checkpoint in checkpoints]
        self.assertAlmostEqual(offsets[1], 50.0, places=1)
        self.assertAlmostEqual(offsets[2], 100.0, places=1)

    def test_no_duplicate_when_interval_divides_the_route(self):
        checkpoints = geo.sample_checkpoints(self.coordinates, self.total, self.cumulative)
        self.assertEqual(len(checkpoints), 2)
        self.assertAlmostEqual(checkpoints[0].offset_miles, 0.0)
        self.assertAlmostEqual(checkpoints[-1].offset_miles, self.total, places=3)

    def test_ten_mile_interval_produces_more_samples_than_fifty(self):
        ten = geo.sample_checkpoints(self.coordinates, 10, self.cumulative)
        fifty = geo.sample_checkpoints(self.coordinates, 50, self.cumulative)
        self.assertGreater(len(ten), len(fifty) * 3)

    def test_positions_follow_the_route(self):
        checkpoints = geo.sample_checkpoints(self.coordinates, 50, self.cumulative)
        latitudes = [checkpoint.position[1] for checkpoint in checkpoints]
        self.assertEqual(latitudes, sorted(latitudes))


class IntervalChoiceTests(SimpleTestCase):
    def test_interval_is_kept_on_long_but_feasible_trips(self):
        interval, coarsened = geo.choose_interval(
            total_miles=3000, requested_miles=10, max_checkpoints=400
        )
        self.assertEqual(interval, 10)
        self.assertFalse(coarsened)

    def test_interval_steps_up_to_25_when_10_is_too_dense(self):
        interval, coarsened = geo.choose_interval(
            total_miles=5000, requested_miles=10, max_checkpoints=400
        )
        self.assertEqual(interval, 25)
        self.assertTrue(coarsened)

    def test_interval_steps_up_to_50_on_extreme_lengths(self):
        interval, coarsened = geo.choose_interval(
            total_miles=12000, requested_miles=10, max_checkpoints=400
        )
        self.assertEqual(interval, 50)
        self.assertTrue(coarsened)

    def test_ladder_starts_at_the_requested_interval(self):
        interval, coarsened = geo.choose_interval(
            total_miles=12000, requested_miles=25, max_checkpoints=400
        )
        self.assertEqual(interval, 50)
        self.assertTrue(coarsened)

    def test_checkpoint_count(self):
        self.assertEqual(geo.checkpoint_count(100, 10), 11)
        self.assertEqual(geo.checkpoint_count(100, 25), 5)


class CorridorTests(SimpleTestCase):
    def test_corridor_stays_near_the_route(self):
        coordinates = straight_route(39.0, 40.0, steps=10)
        points = geo.corridor_points(coordinates, 5, (0, 10, -10))
        self.assertGreater(len(points), 10)
        for _, latitude in points:
            self.assertGreater(latitude, 38.5)
            self.assertLess(latitude, 40.5)

    def test_offset_waypoint_moves_sideways(self):
        coordinates = straight_route(39.0, 40.0, steps=10)
        waypoint = geo.offset_waypoint(coordinates, 0.5, 20)
        # Route runs due north, so a perpendicular offset changes longitude.
        self.assertGreater(abs(waypoint[0]), 0.1)
        self.assertAlmostEqual(waypoint[1], 39.5, places=1)


class OverlapTests(SimpleTestCase):
    def test_identical_routes_overlap_completely(self):
        coordinates = straight_route(39.0, 40.0, steps=20)
        self.assertAlmostEqual(geo.overlap_fraction(coordinates, coordinates), 1.0)

    def test_distant_routes_do_not_overlap(self):
        east = straight_route(39.0, 40.0, steps=20)
        west = [[-30.0, latitude] for _, latitude in east]
        self.assertLess(geo.overlap_fraction(east, west), 0.05)

    def test_empty_geometry_is_safe(self):
        self.assertEqual(geo.overlap_fraction([], [[0.0, 0.0]]), 0.0)
