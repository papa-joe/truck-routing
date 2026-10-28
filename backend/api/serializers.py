"""Request validation for the planning endpoint."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from django.conf import settings
from rest_framework import serializers

from .geo import haversine_miles

INTERVAL_CHOICES = (10, 25, 50)
MAX_LOAD_LBS = 120_000
MAX_ADVANCE_DAYS = 16


class PointSerializer(serializers.Serializer):
    lat = serializers.FloatField(min_value=-90, max_value=90)
    lon = serializers.FloatField(min_value=-180, max_value=180)
    label = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")


class PlanRequestSerializer(serializers.Serializer):
    origin = PointSerializer()
    destination = PointSerializer()
    # Parsed by hand: DRF silently localises naive datetimes, and a driver's
    # "3pm" must never be guessed at.
    departureAt = serializers.CharField(max_length=40)
    loadLbs = serializers.FloatField(min_value=0, max_value=MAX_LOAD_LBS)
    intervalMiles = serializers.IntegerField()

    def validate_intervalMiles(self, value: int) -> int:
        if value not in INTERVAL_CHOICES:
            raise serializers.ValidationError("Choose a 10, 25 or 50 mile sampling interval.")
        return value

    def validate_departureAt(self, value: str) -> datetime:
        try:
            moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            raise serializers.ValidationError(
                "Use an ISO 8601 date and time, for example 2026-05-01T07:30:00Z."
            ) from None

        if moment.tzinfo is None:
            raise serializers.ValidationError("Include a timezone offset; UTC is expected.")

        moment = moment.astimezone(UTC)
        now = datetime.now(UTC)
        if moment < now - timedelta(hours=1):
            raise serializers.ValidationError("Departure time cannot be in the past.")
        if moment > now + timedelta(days=MAX_ADVANCE_DAYS):
            raise serializers.ValidationError(
                f"Forecasts are only available {MAX_ADVANCE_DAYS} days ahead."
            )
        return moment

    def validate(self, attrs: dict) -> dict:
        origin = attrs["origin"]
        destination = attrs["destination"]
        miles = haversine_miles(
            [origin["lon"], origin["lat"]], [destination["lon"], destination["lat"]]
        )
        if miles < 1:
            raise serializers.ValidationError(
                {"destination": "Origin and destination are at the same place."}
            )
        if miles > settings.MAX_ROUTE_MILES:
            raise serializers.ValidationError(
                {
                    "destination": (
                        f"Trips are limited to {settings.MAX_ROUTE_MILES:,} miles "
                        f"in a straight line; this one is {miles:,.0f} miles."
                    )
                }
            )
        return attrs
