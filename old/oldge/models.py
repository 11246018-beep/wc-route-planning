from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class CompanyProfile(models.Model):
    INDUSTRY_CHOICES = [
        ("toilet_cleaning", "流動廁所清掃"),
        ("generic_dispatch", "通用外勤派遣"),
    ]

    key = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=120)
    industry_type = models.CharField(max_length=50, choices=INDUSTRY_CHOICES, default="toilet_cleaning")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "company_profiles"

    def __str__(self):
        return self.name


class CompanyScheduleSettings(models.Model):
    ROUTE_VARIANT_CHOICES = [
        ("normal", "不跨縣市"),
        ("compact", "可跨縣市"),
        ("cross", "跨縣市完整"),
    ]

    company = models.OneToOneField(CompanyProfile, on_delete=models.CASCADE, related_name="schedule_settings")
    default_route_variant = models.CharField(max_length=20, choices=ROUTE_VARIANT_CHOICES, default="normal")
    daily_work_minutes = models.PositiveIntegerField(default=540)
    default_service_minutes = models.PositiveIntegerField(default=10)
    driver_limit = models.PositiveIntegerField(default=14)
    schedule_days = models.PositiveIntegerField(default=6)
    depot_name = models.CharField(max_length=120, blank=True, default="")
    depot_address = models.TextField(blank=True, default="")
    depot_lat = models.FloatField(null=True, blank=True)
    depot_lon = models.FloatField(null=True, blank=True)
    co2_kg_per_km = models.FloatField(default=0.21)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "company_schedule_settings"

    def __str__(self):
        return f"{self.company.key} schedule settings"


class CompanyDepot(models.Model):
    company = models.ForeignKey(CompanyProfile, on_delete=models.CASCADE, related_name="depots")
    code = models.SlugField(max_length=50)
    name = models.CharField(max_length=120)
    address = models.TextField(blank=True, default="")
    lat = models.FloatField(null=True, blank=True)
    lon = models.FloatField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "company_depots"
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="uniq_company_depot_code"),
        ]

    def __str__(self):
        return f"{self.company.key}:{self.code}"


class UserCompanyProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="company_profile")
    company = models.ForeignKey(CompanyProfile, on_delete=models.CASCADE, related_name="user_profiles")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "user_company_profiles"

    def __str__(self):
        return f"{self.user.username} -> {self.company.key}"


class DriverCompanyProfile(models.Model):
    driver_id = models.BigIntegerField(null=True, blank=True, unique=True, db_index=True)
    driver_code = models.CharField(max_length=50, db_index=True)
    company = models.ForeignKey(CompanyProfile, on_delete=models.CASCADE, related_name="driver_profiles")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "driver_company_profiles"
        constraints = [
            models.UniqueConstraint(fields=["company", "driver_code"], name="uniq_driver_code_per_company"),
        ]

    def __str__(self):
        return f"{self.driver_code} -> {self.company.key}"


class ServicePointCompanyProfile(models.Model):
    service_point_id = models.BigIntegerField(unique=True)
    company = models.ForeignKey(CompanyProfile, on_delete=models.CASCADE, related_name="service_point_profiles")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "service_point_company_profiles"

    def __str__(self):
        return f"{self.service_point_id} -> {self.company.key}"


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
    driver_code = models.TextField()
    depot_id = models.BigIntegerField()
    max_minutes = models.IntegerField()
    password = models.TextField()

    class Meta:
        db_table = "drivers"
        managed = False

    def __str__(self):
        return self.driver_code

class CleaningRecord(models.Model):
    id = models.BigAutoField(primary_key=True)
    driver = models.ForeignKey(Driver, on_delete=models.CASCADE)

    image = models.ImageField(upload_to="cleaning_images/")
    score = models.IntegerField(null=True, blank=True)
    status = models.CharField(max_length=50, null=True, blank=True)

    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "cleaning_records"

    def __str__(self):
        return f"{self.driver.driver_code} - {self.created_at}"
