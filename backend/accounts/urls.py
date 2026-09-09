"""Auth routes. Paths match frontend/src/lib/api.ts exactly."""

from django.urls import path

from . import views

urlpatterns = [
    path("auth/session", views.SessionView.as_view(), name="session"),
    path("auth/sign-in", views.SignInView.as_view(), name="sign-in"),
    path("auth/sign-up", views.SignUpView.as_view(), name="sign-up"),
    path("auth/request-code", views.RequestCodeView.as_view(), name="request-code"),
    path("auth/verify", views.VerifyView.as_view(), name="verify"),
    path("auth/forgot", views.ForgotView.as_view(), name="forgot"),
    path("auth/reset", views.ResetView.as_view(), name="reset"),
    path("auth/accept-invite", views.AcceptInviteView.as_view(), name="accept-invite"),
    path("auth/change-password", views.ChangePasswordView.as_view(), name="change-password"),

    # Signing in to a sibling application with a Genmars account. See the
    # section at the foot of views.py for which of these a browser calls and
    # which one a sibling's server calls.
    path("auth/sign-on/app", views.SignOnAppView.as_view(), name="sign-on-app"),
    path(
        "auth/sign-on/authorize",
        views.SignOnAuthorizeView.as_view(),
        name="sign-on-authorize",
    ),
    path("auth/sign-on/token", views.SignOnTokenView.as_view(), name="sign-on-token"),
]
