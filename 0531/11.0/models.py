from django.db import models
from django.utils import timezone


class ServicePoint(models.Model):
    id = models.BigAutoField(primary_key=True)
    created_at = models.DateTimeField(default=timezone.now)

    depot = models.TextField(null=True, blank=True)
    client_name = models.TextField(null=True, blank=True)
    service_time = models.FloatField(null=True, blank=True)
    address = models.TextField(null=True, blank=True)

    floor = models.FloatField(null=True, blank=True)
    order_id = models.TextField(null=True, blank=True)

    weekly_1 = models.BooleanField(null=True, blank=True)
    weekly_2 = models.BooleanField(null=True, blank=True)

    lat = models.FloatField(null=True, blank=True)
    lon = models.FloatField(null=True, blank=True)

    class Meta:
        db_table = "service_points"
        managed = False

    def __str__(self):
        return f"{self.id} - {self.client_name}"
    
class Driver(models.Model):
    id = models.BigAutoField(primary_key=True)
    created_at = models.DateTimeField()
    driver_code = models.TextField(unique=True)
    depot_id = models.BigIntegerField()
    max_minutes = models.IntegerField()
    password = models.TextField()

    class Meta:
        db_table = "drivers"
        managed = False

    def __str__(self):
        return self.driver_code