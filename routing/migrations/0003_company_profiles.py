from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def create_default_company(apps, schema_editor):
    CompanyProfile = apps.get_model("routing", "CompanyProfile")
    UserCompanyProfile = apps.get_model("routing", "UserCompanyProfile")
    DriverCompanyProfile = apps.get_model("routing", "DriverCompanyProfile")
    User = apps.get_model("auth", "User")
    Driver = apps.get_model("routing", "Driver")

    company, _ = CompanyProfile.objects.get_or_create(
        key="toilet_demo",
        defaults={
            "name": "Dispatch Nav 流動廁所示範公司",
            "industry_type": "toilet_cleaning",
            "is_active": True,
        },
    )

    for user in User.objects.all():
        UserCompanyProfile.objects.get_or_create(user=user, defaults={"company": company})

    for driver in Driver.objects.all():
        code = str(getattr(driver, "driver_code", "") or "").strip().upper()
        if code:
            DriverCompanyProfile.objects.get_or_create(driver_code=code, defaults={"company": company})


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("routing", "0002_cleaningrecord"),
    ]

    operations = [
        migrations.CreateModel(
            name="CompanyProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.SlugField(max_length=50, unique=True)),
                ("name", models.CharField(max_length=120)),
                ("industry_type", models.CharField(choices=[("toilet_cleaning", "流動廁所清掃"), ("generic_dispatch", "通用外勤派遣")], default="toilet_cleaning", max_length=50)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={"db_table": "company_profiles"},
        ),
        migrations.CreateModel(
            name="DriverCompanyProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("driver_code", models.CharField(max_length=50, unique=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="driver_profiles", to="routing.companyprofile")),
            ],
            options={"db_table": "driver_company_profiles"},
        ),
        migrations.CreateModel(
            name="UserCompanyProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="user_profiles", to="routing.companyprofile")),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="company_profile", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "user_company_profiles"},
        ),
        migrations.RunPython(create_default_company, migrations.RunPython.noop),
    ]
