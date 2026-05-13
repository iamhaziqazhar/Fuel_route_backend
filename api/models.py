from decimal import Decimal
from django.db import models


class FuelPrice(models.Model):
    city = models.CharField(max_length=128)
    state = models.CharField(max_length=64)

    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)

    regular_price = models.DecimalField(max_digits=6, decimal_places=3, null=True, blank=True)
    mid_grade_price = models.DecimalField(max_digits=6, decimal_places=3, null=True, blank=True)
    premium_price = models.DecimalField(max_digits=6, decimal_places=3, null=True, blank=True)
    diesel_price = models.DecimalField(max_digits=6, decimal_places=3, null=True, blank=True)

    price_per_gallon = models.DecimalField(
        max_digits=6, decimal_places=3, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['latitude', 'longitude'],
                name='uniq_fuelprice_lat_long',
            ),
        ]
        indexes = [
            models.Index(fields=['state', 'city']),
            models.Index(fields=['latitude', 'longitude'], name='fuelprice_lat_lng_idx'),
            models.Index(fields=['price_per_gallon'], name='fuelprice_ppg_idx'),
        ]

    def __str__(self) -> str:
        return f'{self.city}, {self.state} ({self.latitude}, {self.longitude})'

    def compute_price_per_gallon(self) -> Decimal | None:
        if self.regular_price is not None:
            return self.regular_price
        if self.mid_grade_price is not None:
            return self.mid_grade_price
        return None

    def save(self, *args, **kwargs):
        self.price_per_gallon = self.compute_price_per_gallon()
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            kwargs['update_fields'] = set(update_fields) | {'price_per_gallon'}
        super().save(*args, **kwargs)
