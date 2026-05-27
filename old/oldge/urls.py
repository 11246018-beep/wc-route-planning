from django.contrib import admin
from django.urls import path
from routing import views

urlpatterns = [
    path("admin/", admin.site.urls),

    path("", views.home, name="home"),
    path("run/", views.run_scheduler, name="run_scheduler"),

    path("api/routes/options/", views.api_route_options, name="api_route_options"),
    path("api/routes/detail/", views.api_route_detail, name="api_route_detail"),
    path("api/routes/old-options/", views.api_old_route_options, name="api_old_route_options"),
    path("api/routes/old-detail/", views.api_old_route_detail, name="api_old_route_detail"),
    path("api/points/page/", views.api_points_page, name="api_points_page"),

    path("data/", views.data_list, name="data_list"),
    path("data/add/", views.data_add, name="data_add"),
    path("data/edit/<int:pk>/", views.data_edit, name="data_edit"),
    path("data/delete/<int:pk>/", views.data_delete, name="data_delete"),
    path("data/import/", views.data_import, name="data_import"),
]