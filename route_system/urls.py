from django.contrib import admin
from django.urls import path
from django.views.generic import TemplateView
from routing import account_api, live_api, mobile_api, views
from django.conf import settings
from django.conf.urls.static import static


urlpatterns = [
    path("admin/", admin.site.urls),

    path("", views.login_view, name="login"),
    path("home/", views.home, name="home"),
    path("reports/", TemplateView.as_view(template_name="routing/report_page.html"), name="report_page"),
    path("driver-admin/", TemplateView.as_view(template_name="routing/driver_admin.html"), name="driver_admin_page"),
    path("live-monitor/", TemplateView.as_view(template_name="routing/live_monitor.html"), name="live_monitor_page"),
    path("run/", views.run_scheduler, name="run_scheduler"),
    path("api/run/status/", views.api_run_status, name="api_run_status"),
    path("register/", views.register_view, name="register"),
    path("cleaning-records/", views.cleaning_records_page, name="cleaning_records_page"),
    path("cleaning-report/", views.cleaning_report_page),

    path("api/routes/options/", views.api_route_options, name="api_route_options"),
    path("api/routes/detail/", views.api_route_detail, name="api_route_detail"),
    path("api/routes/search-point/", views.api_search_point_route, name="api_search_point_route"),
    path("api/routes/old-options/", views.api_old_route_options, name="api_old_route_options"),
    path("api/routes/old-detail/", views.api_old_route_detail, name="api_old_route_detail"),
    path("api/points/page/", views.api_points_page, name="api_points_page"),

    path("api/driver/login/", views.driver_login_api, name="driver_login_api"),
    path("api/driver/task/", mobile_api.driver_task_api, name="driver_task_api"),
    path("api/driver/report/", mobile_api.driver_report_api, name="driver_report_api"),
    path("api/driver/reports/", mobile_api.driver_reports_api, name="driver_reports_api"),
    path("api/driver/report/update/", mobile_api.driver_report_update_api, name="driver_report_update_api"),
    path("api/driver/report/delete/", mobile_api.driver_report_delete_api, name="driver_report_delete_api"),
    path("api/driver/profile/", account_api.driver_profile_api, name="driver_profile_api"),
    path("api/driver/live/update/", live_api.driver_live_update_api, name="driver_live_update_api"),
    path("api/ai/detect/", mobile_api.detect_cleaning_ai_api, name="detect_cleaning_ai_api"),

    path("api/admin/drivers/", account_api.admin_drivers_api, name="admin_drivers_api"),
    path("api/admin/driver/save/", account_api.admin_driver_save_api, name="admin_driver_save_api"),
    path("api/admin/driver/password/", account_api.admin_driver_password_api, name="admin_driver_password_api"),
    path("api/admin/driver/delete/", account_api.admin_driver_delete_api, name="admin_driver_delete_api"),
    path("api/admin/live/overview/", live_api.admin_live_overview_api, name="admin_live_overview_api"),
    path("api/admin/cleaning-records/", mobile_api.admin_cleaning_records_api, name="admin_cleaning_records_api"),
    path("api/admin/cleaning-record/delete/", mobile_api.admin_cleaning_record_delete_api, name="admin_cleaning_record_delete_api"),
    path("api/admin/cleaning-summary/", mobile_api.admin_cleaning_summary_api, name="admin_cleaning_summary_api"),
    path("api/toilet-demand-analysis/", views.toilet_demand_analysis_api),
    path("api/toilet-demand-analysis/delete/", views.toilet_demand_analysis_delete_api),

    path("api/export/excel/", mobile_api.export_excel_api, name="export_excel_api"),

    path("data/", views.data_list, name="data_list"),
    path("data/add/", views.data_add, name="data_add"),
    path("data/edit/<int:pk>/", views.data_edit, name="data_edit"),
    path("data/delete/<int:pk>/", views.data_delete, name="data_delete"),
    path("data/import/", views.data_import, name="data_import"),

]
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)