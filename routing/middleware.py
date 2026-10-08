from django.conf import settings
from django.shortcuts import redirect
import logging


logger = logging.getLogger(__name__)


class RequireAuthenticationMiddleware:
    """Default-deny web routes, while preserving the intentional public APIs."""

    PUBLIC_EXACT = {
        "/",
        "/logout/",
        "/register/",
        "/api/health/",
        "/api/driver/companies/",
        "/api/driver/login/",
        "/admin/login/",
        "/admin/logout/",
        "/admin/password_reset/",
        "/admin/password_reset/done/",
    }

    # Driver/mobile endpoints authenticate with their own driver token.
    DRIVER_TOKEN_PATHS = {
        "/api/driver/task/",
        "/api/driver/report/",
        "/api/driver/reports/",
        "/api/driver/report/update/",
        "/api/driver/report/delete/",
        "/api/driver/profile/",
        "/api/driver/profile",
        "/api/driver/live/update/",
        "/api/driver/live/state/",
        "/api/driver/upload-image/",
        "/api/ai/detect/",
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        logger.warning(
            "AUTH DEBUG path=%s user=%s authenticated=%s public=%s",
            request.path,
            getattr(user, "username", "<anonymous>"),
            getattr(user, "is_authenticated", False),
            self._is_public(request.path),
        )
        if self._is_public(request.path):
            return self.get_response(request)

        if not getattr(request.user, "is_authenticated", False):
            response = redirect(f"{settings.LOGIN_URL}?next={request.get_full_path()}")
            response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response["Pragma"] = "no-cache"
            logger.warning("AUTH DEBUG response path=%s status=%s location=%s", request.path, response.status_code, response.get("Location"))
            return response

        response = self.get_response(request)
        if request.path.startswith(("/admin/", "/static/", "/media/")) is False:
            response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response["Pragma"] = "no-cache"
        logger.warning("AUTH DEBUG response path=%s status=%s", request.path, response.status_code)
        return response

    def _is_public(self, path):
        if path in self.PUBLIC_EXACT:
            return True
        if path.startswith("/static/") or path.startswith("/media/"):
            return True
        if path in self.PUBLIC_EXACT:
            return True
        # Authenticated admin pages are still checked by Django admin itself.
        # Anonymous admin URLs fall through to the default-deny redirect.
        if path in self.DRIVER_TOKEN_PATHS:
            return True
        return False
