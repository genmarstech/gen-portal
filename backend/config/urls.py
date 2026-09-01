from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    # Caddy routes /api/* here; everything else goes to Next.js on :3010.
    path("api/health", lambda r: JsonResponse({"status": "ok"}), name="health"),
    path("api/", include("accounts.urls")),
    path("api/", include("portal.urls")),
    # /api/ops/* — staff only, enforced per view by operations.permissions.IsStaff.
    path("api/", include("operations.urls")),
]
