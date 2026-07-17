from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def create_default_depots(apps, schema_editor):
    CompanyProfile = apps.get_model("routing", "CompanyProfile")
    CompanyScheduleSettings = apps.get_model("routing", "CompanyScheduleSettings")
    CompanyDepot = apps.get_model("routing", "CompanyDepot")

    for company in CompanyProfile.objects.all():
        settings = CompanyScheduleSettings.objects.filter(company=company).first()
        name = (getattr(settings, "depot_name", "") or "主要場站").strip()
        address = (getattr(settings, "depot_address", "") or "").strip()
        lat = getattr(settings, "depot_lat", None)
        lon = getattr(settings, "depot_lon", None)
        CompanyDepot.objects.get_or_create(
            company=company,
            code="main",
            defaults={
                "name": name,
                "address": address,
                "lat": lat,
                "lon": lon,
                "is_active": True,
                "sort_order": 1,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("routing", "0006_company_schedule_settings"),
    ]

    operations = [
        migrations.CreateModel(
            name="CompanyDepot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.SlugField(max_length=50)),
                ("name", models.CharField(max_length=120)),
                ("address", models.TextField(blank=True, default="")),
                ("lat", models.FloatField(blank=True, null=True)),
                ("lon", models.FloatField(blank=True, null=True)),
                ("is_active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveIntegerField(default=1)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="depots", to="routing.companyprofile")),
            ],
            options={
                "db_table": "company_depots",
                "ordering": ["sort_order", "id"],
            },
        ),
        migrations.AddConstraint(
            model_name="companydepot",
            constraint=models.UniqueConstraint(fields=("company", "code"), name="uniq_company_depot_code"),
        ),
        migrations.RunPython(create_default_depots, migrations.RunPython.noop),
    ]
