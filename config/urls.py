from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from welfare.views import health

urlpatterns = [
    path("health/", health, name="health"),
    path(
        "accedi/",
        auth_views.LoginView.as_view(
            template_name="welfare/auth/login.html", redirect_authenticated_user=True
        ),
        name="login",
    ),
    path("esci/", auth_views.LogoutView.as_view(), name="logout"),
    path(
        "cambia-password/",
        auth_views.PasswordChangeView.as_view(
            template_name="welfare/auth/password_change.html",
            success_url="/cambia-password/fatto/",
        ),
        name="password_change",
    ),
    path(
        "cambia-password/fatto/",
        auth_views.PasswordChangeDoneView.as_view(
            template_name="welfare/auth/password_change_done.html"
        ),
        name="password_change_done",
    ),
    path("django-admin/", admin.site.urls),
    path("", include("welfare.urls")),
]
