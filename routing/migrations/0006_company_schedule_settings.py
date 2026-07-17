from django.db import migrations, models
import django.db.models.deletion


def create_default_settings(apps, schema_editor):
    CompanyProfile = apps.get_model("routing", "CompanyProfile")
    CompanyScheduleSettings = apps.get_model("routing", "CompanyScheduleSettings")
    for company in CompanyProfile.objects.all():
        CompanyScheduleSettings.objects.get_or_create(
            company=company,
            defaults={
                "default_route_variant": "normal",
                "daily_work_minutes": 540,
                "default_service_minutes": 10,
                "driver_limit": 14,
                "schedule_days": 6,
                "depot_name": "總部",
                "depot_address": "",
                "depot_lat": None,
                "depot_lon": None,
                "co2_kg_per_km": 0.21,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("routing", "0005_driver_company_scoped_codes"),
    ]

    operations = [
        migrations.CreateModel(
            name="CompanyScheduleSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "default_route_variant",
                    models.CharField(
                        choices=[("normal", "不跨縣市"), ("compact", "可跨縣市"), ("cross", "跨縣市完整")],
                        default="normal",
                        max_length=20,
                    ),
                ),
                ("daily_work_minutes", models.PositiveIntegerField(default=540)),
                ("default_service_minutes", models.PositiveIntegerField(default=10)),
                ("driver_limit", models.PositiveIntegerField(default=14)),
                ("schedule_days", models.PositiveIntegerField(default=6)),
                ("depot_name", models.CharField(blank=True, default="", max_length=120)),
                ("depot_address", models.TextField(blank=True, default="")),
                ("depot_lat", models.FloatField(blank=True, null=True)),
                ("depot_lon", models.FloatField(blank=True, null=True)),
                ("co2_kg_per_km", models.FloatField(default=0.21)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "company",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="schedule_settings",
                        to="routing.companyprofile",
                    ),
                ),
            ],
            options={
                "db_table": "company_schedule_settings",
            },
        ),
        migrations.RunPython(create_default_settings, migrations.RunPython.noop),
    ]
