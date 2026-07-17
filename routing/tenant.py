from pathlib import Path
from types import SimpleNamespace

from django.db.utils import DatabaseError, OperationalError, ProgrammingError
from django.utils.text import slugify

from .models import CompanyProfile, Driver, DriverCompanyProfile, UserCompanyProfile


DEFAULT_COMPANY_KEY = "toilet_demo"
DEFAULT_COMPANY_NAME = "Dispatch Nav 流動廁所示範公司"


def fallback_company():
    return SimpleNamespace(
        id=None,
        key=DEFAULT_COMPANY_KEY,
        name=DEFAULT_COMPANY_NAME,
        industry_type="toilet_cleaning",
        is_active=True,
    )


def normalize_company_key(value):
    key = slugify(str(value or "").strip()) or DEFAULT_COMPANY_KEY
    return key.replace("-", "_")[:50]


def get_default_company():
    try:
        company, _ = CompanyProfile.objects.get_or_create(
            key=DEFAULT_COMPANY_KEY,
            defaults={
                "name": DEFAULT_COMPANY_NAME,
                "industry_type": "toilet_cleaning",
                "is_active": True,
            },
        )
        return company
    except (DatabaseError, OperationalError, ProgrammingError):
        return fallback_company()


def get_user_company(user):
    if not getattr(user, "is_authenticated", False):
        return get_default_company()

    try:
        profile = UserCompanyProfile.objects.select_related("company").filter(user=user).first()
        if profile and profile.company and profile.company.is_active:
            return profile.company

        company = get_default_company()
        if getattr(company, "id", None):
            UserCompanyProfile.objects.get_or_create(user=user, defaults={"company": company})
        return company
    except (DatabaseError, OperationalError, ProgrammingError):
        return fallback_company()


def find_company_by_key(company_key):
    key = normalize_company_key(company_key)
    try:
        return CompanyProfile.objects.filter(key=key, is_active=True).first()
    except (DatabaseError, OperationalError, ProgrammingError):
        return None


def get_driver_company(driver_or_code, company_key=""):
    driver_id = getattr(driver_or_code, "id", None)
    code = str(getattr(driver_or_code, "driver_code", driver_or_code) or "").strip().upper()
    requested_company = find_company_by_key(company_key) if company_key else None
    if requested_company:
        return requested_company
    if not code and not driver_id:
        return get_default_company()

    try:
        profile_qs = DriverCompanyProfile.objects.select_related("company")
        if driver_id:
            profile = profile_qs.filter(driver_id=driver_id).first()
        else:
            matches = list(profile_qs.filter(driver_code__iexact=code)[:2])
            profile = matches[0] if len(matches) == 1 else None
        if profile and profile.company and profile.company.is_active:
            return profile.company

        company = get_default_company()
        if getattr(company, "id", None) and code:
            driver = None
            if driver_id:
                driver = Driver.objects.filter(id=driver_id).first()
            if driver is None:
                driver = Driver.objects.filter(driver_code__iexact=code).first()
            DriverCompanyProfile.objects.get_or_create(
                driver_id=getattr(driver, "id", None),
                defaults={"driver_code": code, "company": company},
            )
        return company
    except (DatabaseError, OperationalError, ProgrammingError):
        return fallback_company()


def find_driver_for_company(driver_code, company=None, company_key=""):
    code = str(driver_code or "").strip().upper()
    if not code:
        return None
    try:
        company = company or find_company_by_key(company_key)
        if company and getattr(company, "id", None):
            profile = (
                DriverCompanyProfile.objects
                .filter(company=company, driver_code__iexact=code)
                .order_by("id")
                .first()
            )
            if profile and profile.driver_id:
                driver = Driver.objects.filter(id=profile.driver_id).first()
                if driver:
                    return driver
            return Driver.objects.filter(driver_code__iexact=code).first() if profile else None

        drivers = list(Driver.objects.filter(driver_code__iexact=code)[:2])
        return drivers[0] if len(drivers) == 1 else None
    except (DatabaseError, OperationalError, ProgrammingError):
        return Driver.objects.filter(driver_code__iexact=code).first()


def company_output_dir(base_output_dir: Path, company):
    key = normalize_company_key(getattr(company, "key", "") or DEFAULT_COMPANY_KEY)
    return base_output_dir / "tenants" / key


def current_company_output_dir(base_output_dir: Path, user):
    return company_output_dir(base_output_dir, get_user_company(user))


def tenant_file_path(base_output_dir: Path, company, filename, fallback=True):
    tenant_path = company_output_dir(base_output_dir, company) / filename
    if tenant_path.exists() or not fallback:
        return tenant_path
    return base_output_dir / filename


def ensure_tenant_output_dir(base_output_dir: Path, company):
    path = company_output_dir(base_output_dir, company)
    path.mkdir(parents=True, exist_ok=True)
    return path


def serialize_company(company):
    return {
        "id": getattr(company, "id", None),
        "key": getattr(company, "key", DEFAULT_COMPANY_KEY),
        "name": getattr(company, "name", DEFAULT_COMPANY_NAME),
        "industry_type": getattr(company, "industry_type", "toilet_cleaning"),
    }
