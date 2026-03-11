from django.db import models

class ServicePoint(models.Model):
    depot = models.TextField()
    client_name = models.TextField()
    service_time = models.FloatField()
    address = models.TextField()

    lat = models.FloatField()
    lon = models.FloatField()

    weekly_1 = models.IntegerField(null=True, blank=True)
    weekly_2 = models.IntegerField(null=True, blank=True)

    order_id = models.TextField(null=True, blank=True)
    floor = models.TextField(null=True, blank=True)
    

    class Meta:
        db_table = "service_points"