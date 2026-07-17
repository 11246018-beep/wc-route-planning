from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def link_existing_points(apps, schema_editor):
    CompanyProfile = apps.get_model("routing", "CompanyProfile")
    ServicePointCompanyProfile = apps.get_model("routing", "ServicePointCompanyProfile")
    ServicePoint = apps.get_model("routing", "ServicePoint")

    company, _ = CompanyProfile.objects.get_or_create(
        key="toilet_demo",
        defaults={
            "name": "Dispatch Nav 流動廁所示範公司",
            "industry_type": "toilet_cleaning",
            "is_active": True,
        },
    )

    rows = [
        ServicePointCompanyProfile(service_point_id=point_id, company=company)
        for point_id in ServicePoint.objects.values_list("id", flat=True)
    ]
    ServicePointCompanyProfile.objects.bulk_create(rows, ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        ("routing", "0003_company_profiles"),
    ]

    operations = [
        migrations.CreateModel(
            name="ServicePointCompanyProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("service_point_id", models.BigIntegerField(unique=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "company",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="service_point_profiles",
                        to="routing.companyprofile",
                    ),
                ),
            ],
            options={
                "db_table": "service_point_company_profiles",
            },
        ),
        migrations.RunPython(link_existing_points, migrations.RunPython.noop),
    ]
