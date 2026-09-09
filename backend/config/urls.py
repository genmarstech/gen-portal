from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path

from accounts.auth_backends import rate_limited_admin_login

# ── THE ADMIN LOGIN IS RATE LIMITED BEFORE admin.site.urls IS BUILT ─────────
#
# admin.site.login is what `path("admin/", admin.site.urls)` wires to
# /admin/login/, so replacing the attribute here limits the real door rather
# than adding a second one beside it. It has to happen BEFORE admin.site.urls
# is evaluated on the next line — that call captures the view.
#
# AUTHENTICATION_BACKENDS gives that door the account lockout; this gives it a
# per-IP limit. Both are needed: the lockout stops somebody working through
# passwords against one address, the limit stops somebody working through
# addresses with one password, and neither covers the other's case.
# See docs/SECURITY-AUDIT-2026-09-09.md finding 2.
admin.site.login = rate_limited_admin_login(admin.site.login)

urlpatterns = [
    path("admin/", admin.site.urls),
    # Caddy routes /api/* here; everything else goes to Next.js on :3010.
    path("api/health", lambda r: JsonResponse({"status": "ok"}), name="health"),
    path("api/", include("accounts.urls")),
    path("api/", include("portal.urls")),
    # /api/ops/* — staff only, enforced per view by operations.permissions.IsStaff.
    path("api/", include("operations.urls")),
]
