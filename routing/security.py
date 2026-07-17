from django.conf import settings
from django.contrib.auth.hashers import check_password, identify_hasher, make_password
from django.core import signing

from .models import Driver
from .tenant import find_driver_for_company, get_driver_company


DRIVER_TOKEN_SALT = "dispatch-nav-driver-token"
DRIVER_TOKEN_MAX_AGE = getattr(settings, "DRIVER_TOKEN_MAX_AGE", 60 * 60 * 24 * 30)


def is_manager_user(user):
    return bool(user and user.is_authenticated and (user.is_staff or user.is_superuser))


def is_hashed_password(value):
    try:
        identify_hasher(value or "")
        return True
    except Exception:
        return False


def hash_driver_password(raw_password):
    return make_password(raw_password or "")


def normalize_driver_code(value):
    return str(value or "").strip().upper()


def find_driver_by_code(driver_code, company_key=""):
    target_code = normalize_driver_code(driver_code)
    if not target_code:
        return None

    if company_key:
        return find_driver_for_company(target_code, company_key=company_key)

    driver = Driver.objects.filter(driver_code__iexact=target_code).first()
    if driver:
        return driver

    for candidate in Driver.objects.all().only("id", "driver_code"):
        if normalize_driver_code(candidate.driver_code) == target_code:
            return Driver.objects.filter(id=candidate.id).first()

    return None


def verify_driver_password(driver, raw_password):
    stored = driver.password or ""
    raw_password = raw_password or ""

    if is_hashed_password(stored):
        return check_password(raw_password, stored)

    if stored == raw_password:
        driver.password = hash_driver_password(raw_password)
        driver.save(update_fields=["password"])
        return True

    return False


def make_driver_token(driver, company=None):
    company = company or get_driver_company(driver)
    return signing.dumps(
        {
            "driver_id": getattr(driver, "id", None),
            "driver_code": normalize_driver_code(driver.driver_code),
            "company_key": getattr(company, "key", ""),
            "password_hash": driver.password or "",
        },
        salt=DRIVER_TOKEN_SALT,
    )


def get_driver_token_from_request(request):
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1].strip()
    return (
        request.headers.get("X-Driver-Token")
        or request.GET.get("driver_token")
        or request.POST.get("driver_token")
        or ""
    ).strip()


def authenticate_driver_token(request, expected_driver_code=None, expected_company_key=None):
    token = get_driver_token_from_request(request)
    if not token:
        return None, "缺少司機登入 token"

    try:
        payload = signing.loads(
            token,
            salt=DRIVER_TOKEN_SALT,
            max_age=DRIVER_TOKEN_MAX_AGE,
        )
    except signing.SignatureExpired:
        return None, "司機登入已過期，請重新登入"
    except signing.BadSignature:
        return None, "司機登入 token 無效"

    token_driver_code = normalize_driver_code(payload.get("driver_code"))
    if expected_driver_code and token_driver_code != normalize_driver_code(expected_driver_code):
        return None, "司機 token 與 driver_code 不一致"
    if expected_company_key and payload.get("company_key") != expected_company_key:
        return None, "司機 token 與公司不一致"

    driver_id = payload.get("driver_id")
    driver = Driver.objects.filter(id=driver_id).first() if driver_id else None
    if driver is None:
        driver = find_driver_by_code(token_driver_code, payload.get("company_key") or "")
    if not driver:
        return None, "找不到司機帳號"

    if payload.get("password_hash") != (driver.password or ""):
        return None, "司機密碼已變更，請重新登入"

    return driver, ""
