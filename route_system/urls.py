from django.contrib import admin
from django.urls import path
from django.views.generic import TemplateView
from django.contrib.auth.decorators import login_required, user_passes_test
from routing import account_api, live_api, mobile_api, views
from django.conf import settings
from django.conf.urls.static import static


def manager_required(user):
    return bool(user and user.is_authenticated and (user.is_staff or user.is_superuser))


login_page = lambda template_name: login_required(TemplateView.as_view(template_name=template_name), login_url="login")
manager_page = lambda template_name: user_passes_test(manager_required, login_url="login")(
    TemplateView.as_view(template_name=template_name)
)
login_api = lambda view: login_required(view, login_url="login")
manager_api = lambda view: user_passes_test(manager_required, login_url="login")(view)


urlpatterns = [
    path("admin/", admin.site.urls),

    path("", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("api/health/", views.health_api, name="health_api"),
    path("home/", views.home, name="home"),
    path("accounts/", views.account_management, name="account_management"),
    path("accounts/action/<int:user_id>/", views.account_management_action, name="account_management_action"),
    path("company-settings/", views.company_settings, name="company_settings"),
    path("company-settings/action/", views.company_settings_action, name="company_settings_action"),
    path("companies/", views.company_management, name="company_management"),
    path("companies/action/", views.company_management_action, name="company_management_action"),
    path("admin-logs/", views.admin_action_logs_page, name="admin_action_logs"),
    path("reports/", login_page("routing/report_page.html"), name="report_page"),
    path("driver-admin/", login_page("routing/driver_admin.html"), name="driver_admin_page"),
    path("live-monitor/", login_page("routing/live_monitor.html"), name="live_monitor_page"),
    path("run/", manager_api(views.run_scheduler), name="run_scheduler"),
    path("api/run/status/", manager_api(views.api_run_status), name="api_run_status"),
    path("register/", views.register_view, name="register"),
    path("cleaning-records/", login_page("routing/cleaning_records.html"), name="cleaning_records_page"),
    path("cleaning-report/", login_page("routing/cleaning_report.html")),

    # Read-only accounts may inspect routes and points. Mutating actions such as
    # running the scheduler remain protected by manager_api above.
    path("api/routes/options/", login_api(views.api_route_options), name="api_route_options"),
    path("api/routes/detail/", login_api(views.api_route_detail), name="api_route_detail"),
    path("api/routes/search-point/", login_api(views.api_search_point_route), name="api_search_point_route"),
    path("api/routes/old-options/", login_api(views.api_old_route_options), name="api_old_route_options"),
    path("api/routes/old-detail/", login_api(views.api_old_route_detail), name="api_old_route_detail"),
    path("api/routes/esg-baseline/upload/", manager_api(views.api_upload_esg_baseline), name="api_upload_esg_baseline"),
    path("api/points/page/", login_api(views.api_points_page), name="api_points_page"),

    path("api/driver/companies/", views.driver_companies_api, name="driver_companies_api"),
    path("api/driver/login/", views.driver_login_api, name="driver_login_api"),
    path("api/driver/task/", mobile_api.driver_task_api, name="driver_task_api"),
    path("api/driver/report/", mobile_api.driver_report_api, name="driver_report_api"),
    path("api/driver/reports/", mobile_api.driver_reports_api, name="driver_reports_api"),
    path("api/driver/report/update/", mobile_api.driver_report_update_api, name="driver_report_update_api"),
    path("api/driver/report/delete/", mobile_api.driver_report_delete_api, name="driver_report_delete_api"),
    path("api/driver/profile/", account_api.driver_profile_api, name="driver_profile_api"),
    # Some app builds call this endpoint without a trailing slash; serve JSON directly
    # instead of returning Django's HTML redirect page.
    path("api/driver/profile", account_api.driver_profile_api),
    path("api/driver/live/update/", live_api.driver_live_update_api, name="driver_live_update_api"),
    path("api/driver/live/state/", live_api.driver_live_state_api, name="driver_live_state_api"),
    path("api/ai/detect/", mobile_api.detect_cleaning_ai_api, name="detect_cleaning_ai_api"),

    path("api/admin/drivers/", login_api(account_api.admin_drivers_api), name="admin_drivers_api"),
    path("api/admin/driver/save/", account_api.admin_driver_save_api, name="admin_driver_save_api"),
    path("api/admin/driver/password/", account_api.admin_driver_password_api, name="admin_driver_password_api"),
    path("api/admin/driver/delete/", account_api.admin_driver_delete_api, name="admin_driver_delete_api"),
    path("api/admin/live/overview/", login_api(live_api.admin_live_overview_api), name="admin_live_overview_api"),
    path("api/admin/live/reset-progress/", manager_api(live_api.admin_live_reset_progress_api), name="admin_live_reset_progress_api"),
    path("api/admin/cleaning-records/", login_api(mobile_api.admin_cleaning_records_api), name="admin_cleaning_records_api"),
    path("api/admin/cleaning-record/delete/", mobile_api.admin_cleaning_record_delete_api, name="admin_cleaning_record_delete_api"),
    path("api/admin/cleaning-summary/", login_api(mobile_api.admin_cleaning_summary_api), name="admin_cleaning_summary_api"),
    path("api/toilet-demand-analysis/", login_api(views.toilet_demand_analysis_api)),
    path("api/toilet-demand-analysis/delete/", manager_api(views.toilet_demand_analysis_delete_api)),

    path("api/export/excel/", manager_api(mobile_api.export_excel_api), name="export_excel_api"),

    path("data/", views.data_list, name="data_list"),
    path("data/add/", views.data_add, name="data_add"),
    path("data/edit/<int:pk>/", views.data_edit, name="data_edit"),
    path("data/delete/<int:pk>/", views.data_delete, name="data_delete"),
    path("data/bulk-delete/", views.data_bulk_delete, name="data_bulk_delete"),
    path("data/import/", views.data_import, name="data_import"),

]
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
