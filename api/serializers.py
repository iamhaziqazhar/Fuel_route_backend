from __future__ import annotations

from rest_framework import serializers


class OptimizeRequestSerializer(serializers.Serializer):
    start_address = serializers.CharField(
        max_length=500,
        trim_whitespace=True,
        help_text='Free-form starting address (geocoded server-side).',
    )
    end_address = serializers.CharField(
        max_length=500,
        trim_whitespace=True,
        help_text='Free-form destination address (geocoded server-side).',
    )

    def validate_start_address(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError('start_address cannot be blank.')
        return value.strip()

    def validate_end_address(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError('end_address cannot be blank.')
        return value.strip()

    def validate(self, attrs: dict) -> dict:
        if attrs['start_address'].lower() == attrs['end_address'].lower():
            raise serializers.ValidationError(
                'start_address and end_address must differ.'
            )
        return attrs


class StopSerializer(serializers.Serializer):
    name = serializers.CharField()
    address = serializers.CharField()
    lat = serializers.FloatField()
    lng = serializers.FloatField()
    price_per_gallon = serializers.FloatField()
    cost_for_stop = serializers.FloatField()
    distance_from_previous_miles = serializers.FloatField()
    gallons = serializers.FloatField()
    is_safety_stop = serializers.BooleanField()


class OptimizeResponseSerializer(serializers.Serializer):
    route_polyline = serializers.CharField()
    total_distance_miles = serializers.FloatField()
    estimated_duration_seconds = serializers.FloatField()
    stops = StopSerializer(many=True)
    total_cost = serializers.FloatField()
    total_fuel_used_gallons = serializers.FloatField()
    final_leg_miles = serializers.FloatField()
    start = serializers.DictField()
    end = serializers.DictField()
