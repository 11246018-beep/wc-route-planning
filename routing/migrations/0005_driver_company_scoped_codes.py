from django.db import migrations, models


def populate_driver_profile_ids(apps, schema_editor):
    Driver = apps.get_model("routing", "Driver")
    DriverCompanyProfile = apps.get_model("routing", "DriverCompanyProfile")
    for profile in DriverCompanyProfile.objects.all():
        code = str(profile.driver_code or "").strip().upper()
        if not code:
            continue
        driver = Driver.objects.filter(driver_code__iexact=code).first()
        if driver:
            profile.driver_id = driver.id
            profile.driver_code = code
            profile.save(update_fields=["driver_id", "driver_code"])


class Migration(migrations.Migration):

    dependencies = [
        ("routing", "0004_service_point_company_profiles"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            DO $$
            DECLARE
                constraint_name text;
            BEGIN
                FOR constraint_name IN
                    SELECT con.conname
                    FROM pg_constraint con
                    JOIN pg_class rel ON rel.oid = con.conrelid
                    JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
                    WHERE rel.relname = 'drivers'
                      AND con.contype = 'u'
                      AND pg_get_constraintdef(con.oid) ILIKE '%driver_code%'
                LOOP
                    EXECUTE format('ALTER TABLE drivers DROP CONSTRAINT IF EXISTS %I', constraint_name);
                END LOOP;

                FOR constraint_name IN
                    SELECT indexname
                    FROM pg_indexes
                    WHERE tablename = 'drivers'
                      AND indexdef ILIKE '%UNIQUE%'
                      AND indexdef ILIKE '%driver_code%'
                LOOP
                    EXECUTE format('DROP INDEX IF EXISTS %I', constraint_name);
                END LOOP;
            END $$;
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AlterField(
            model_name="driver",
            name="driver_code",
            field=models.TextField(),
        ),
        migrations.AddField(
            model_name="drivercompanyprofile",
            name="driver_id",
            field=models.BigIntegerField(blank=True, db_index=True, null=True, unique=True),
        ),
        migrations.AlterField(
            model_name="drivercompanyprofile",
            name="driver_code",
            field=models.CharField(db_index=True, max_length=50),
        ),
        migrations.RunPython(populate_driver_profile_ids, migrations.RunPython.noop),
        migrations.RunSQL(
            sql="""
            DO $$
            DECLARE
                constraint_name text;
            BEGIN
                FOR constraint_name IN
                    SELECT con.conname
                    FROM pg_constraint con
                    JOIN pg_class rel ON rel.oid = con.conrelid
                    WHERE rel.relname = 'driver_company_profiles'
                      AND con.contype = 'u'
                      AND pg_get_constraintdef(con.oid) ILIKE '%driver_code%'
                LOOP
                    EXECUTE format('ALTER TABLE driver_company_profiles DROP CONSTRAINT IF EXISTS %I', constraint_name);
                END LOOP;

                FOR constraint_name IN
                    SELECT indexname
                    FROM pg_indexes
                    WHERE tablename = 'driver_company_profiles'
                      AND indexdef ILIKE '%UNIQUE%'
                      AND indexdef ILIKE '%driver_code%'
                LOOP
                    EXECUTE format('DROP INDEX IF EXISTS %I', constraint_name);
                END LOOP;
            END $$;
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AddConstraint(
            model_name="drivercompanyprofile",
            constraint=models.UniqueConstraint(fields=("company", "driver_code"), name="uniq_driver_code_per_company"),
        ),
        migrations.RunSQL(
            sql="ALTER TABLE uploaded_photos ADD COLUMN IF NOT EXISTS company_key text;",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
