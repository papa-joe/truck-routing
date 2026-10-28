"""Place search tests."""

from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from api import photon
from api.exceptions import ProviderError

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def feature(name, city, state, lon, lat, kind="city"):
    return {
        "geometry": {"coordinates": [lon, lat]},
        "properties": {"name": name, "city": city, "state": state, "osm_value": kind},
    }


@override_settings(CACHES=LOCMEM)
class SearchTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    @mock.patch("api.photon._request")
    def test_labels_are_built_from_available_parts(self, request):
        request.return_value = {
            "features": [feature("Denver", "Denver", "Colorado", -104.99, 39.73)]
        }
        results = photon.search("denver")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["label"], "Denver, Colorado")
        self.assertAlmostEqual(results[0]["lat"], 39.73)

    @mock.patch("api.photon._request")
    def test_duplicate_labels_are_removed(self, request):
        request.return_value = {
            "features": [
                feature("Denver", "Denver", "Colorado", -104.99, 39.73),
                feature("Denver", "Denver", "Colorado", -104.98, 39.74),
            ]
        }
        self.assertEqual(len(photon.search("denver")), 1)

    @mock.patch("api.photon._request")
    def test_results_are_cached(self, request):
        request.return_value = {
            "features": [feature("Denver", "Denver", "Colorado", -104.99, 39.73)]
        }
        photon.search("denver")
        photon.search("denver")
        self.assertEqual(request.call_count, 1)

    @mock.patch("api.photon._request")
    def test_short_queries_do_not_hit_the_network(self, request):
        self.assertEqual(photon.search(" d "), [])
        request.assert_not_called()

    @mock.patch("api.photon.requests.get")
    def test_provider_failure_raises(self, get):
        get.side_effect = photon.requests.RequestException("boom")
        with self.assertRaises(ProviderError):
            photon.search("denver")
