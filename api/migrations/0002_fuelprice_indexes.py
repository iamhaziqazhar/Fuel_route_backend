"""Add composite (lat, lng) and price_per_gallon indexes for spatial lookup."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0001_initial'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='fuelprice',
            index=models.Index(
                fields=['latitude', 'longitude'],
                name='fuelprice_lat_lng_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='fuelprice',
            index=models.Index(
                fields=['price_per_gallon'],
                name='fuelprice_ppg_idx',
            ),
        ),
    ]
