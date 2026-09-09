"""
Auth API.

Matches the contract in frontend/src/lib/api.ts exactly. Every view is thin:
validate, call `identity`, translate the result into a response. No auth logic
lives here — that is the whole point of the identity boundary, and it is what
makes the eventual AuthGate swap a day's work.

── TWO RULES THAT SHAPE EVERY RESPONSE ──────────────────────────────────────
1. **Never reveal whether an address is registered.** sign-in returns one
   message for unknown-email and wrong-password. forgot and request-code return
   success unconditionally. The frontend is built to match and must not try to
   be more helpful.
2. **Never return the code.** Not in a response, not in a log, not in DEBUG.
"""

from __future__ import annotations

import logging

from urllib.parse import quote

from django.contrib.auth import login, logout
from django.middleware.csrf import get_token
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils.decorators import method_decorator
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import emails, identity
from .auth_backends import SESSION_BACKEND
from .models import EmailCode, Membership, User
from .throttling import (
    CodeThrottle,
    EmailScopedThrottle,
    SignInThrottle,
    SignOnClientThrottle,
    SignOnThrottle,
    SignOnTokenIPThrottle,
    SignUpThrottle,
)

log = logging.getLogger(__name__)

DASHBOARD = "/dashboard"
VERIFY = "/verify"
ONBOARDING = "/onboarding"


# ─────────────────────────────────────────────────────────────────────────────
# Serializers
# ─────────────────────────────────────────────────────────────────────────────


class EmailSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)

    def validate_email(self, value: str) -> str:
        """
        Normalise once, here, because four views look accounts up by this value
        directly rather than through `identity`.

        Addresses are stored lower-cased (User.objects.create_user normalises),
        and every lookup inside identity.py lowercases before querying. The
        views did not, so `Muchemiedwin68@gmail.com` did not match the stored
        `muchemiedwin68@gmail.com` — and the failures were silent by design:
        verify answered "that code is not right, or it has expired" because an
        unknown address must not be distinguishable from a bad code, and
        request-code answered 200 because whether an address is registered is
        not public. A visitor whose keyboard or autofill capitalised one letter
        could not verify, could not resend, could not reset, and was told
        nothing that pointed at the cause.

        Doing it in the serializer rather than at each query is deliberate: it
        is one place, it covers every subclass below, and a view added later
        gets it without knowing it needs it.
        """
        return value.strip().lower()


class SignInSerializer(EmailSerializer):
    # No max/min here: a length rule on sign-in would reject a legitimate long
    # password and, worse, hint at the policy. Validation belongs at signup.
    password = serializers.CharField(trim_whitespace=False)


class SignUpSerializer(EmailSerializer):
    password = serializers.CharField(trim_whitespace=False, min_length=10)
    full_name = serializers.CharField(max_length=200, allow_blank=True, default="")

    def validate_password(self, value: str) -> str:
        """Run Django's configured validators — length, commonness, all-numeric."""
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError as DjangoValidationError

        try:
            validate_password(value)
        except DjangoValidationError as e:
            raise serializers.ValidationError(list(e.messages)) from e
        return value


class InviteAcceptSerializer(EmailSerializer):
    code = serializers.RegexField(r"^\d{6}$", trim_whitespace=True)
    password = serializers.CharField(trim_whitespace=False, min_length=10)

    def validate_password(self, value: str) -> str:
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError as DjangoValidationError

        try:
            validate_password(value)
        except DjangoValidationError as e:
            raise serializers.ValidationError(list(e.messages)) from e
        return value


class CodeSerializer(EmailSerializer):
    code = serializers.RegexField(r"^\d{6}$", trim_whitespace=True)


class ResetSerializer(CodeSerializer):
    password = serializers.CharField(trim_whitespace=False, min_length=10)

    validate_password = SignUpSerializer.validate_password


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(trim_whitespace=False)
    new_password = serializers.CharField(trim_whitespace=False, min_length=10)

    validate_new_password = SignUpSerializer.validate_password


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _fail(exc: identity.AuthError, code: int = status.HTTP_401_UNAUTHORIZED):
    """
    Turn an AuthError into a response.

    `reason` goes to the log; only `safe_message` reaches the client. That split
    is the enumeration defence, and it is why AuthError carries both.
    """
    log.info("auth failure: %s", exc.reason)
    return Response({"detail": exc.safe_message}, status=code)


def _verify_url(email: str) -> str:
    """
    /verify needs to know WHICH address it is verifying.

    quote() rather than an f-string: a "+" is legal in an email local part and
    decodes to a space on the other side, which silently verifies the wrong
    address — or, more often, nothing at all.
    """
    return f"{VERIFY}?email={quote(email)}"


def _needs_onboarding(user: User) -> bool:
    """
    A verified client with no organisation has not finished signing up.

    Staff are excluded: they never onboard, they work in the admin, and sending
    them round an onboarding loop they cannot complete would lock them out of
    the app entirely.
    """
    return not user.is_staff and not identity.has_organisation(user)


def _destination(user: User) -> str:
    """
    Where an account belongs right now. Three states, in order:

      unverified              -> /verify, with the address to verify
      verified, no org        -> /onboarding
      verified, has org       -> /dashboard
    """
    if not user.is_email_verified:
        return _verify_url(user.email)
    if _needs_onboarding(user):
        return ONBOARDING
    return DASHBOARD


def _issue_and_send(user: User, purpose: str) -> None:
    issued = identity.issue_code(user, purpose)
    if purpose == EmailCode.Purpose.VERIFY:
        emails.send_verification_code(user.email, issued.code)
    else:
        emails.send_password_reset_code(user.email, issued.code)


# ─────────────────────────────────────────────────────────────────────────────
# Views
# ─────────────────────────────────────────────────────────────────────────────


@method_decorator(ensure_csrf_cookie, name="dispatch")
class SessionView(APIView):
    """
    GET  — who is signed in, and set the CSRF cookie.
    DELETE — sign out.

    The frontend calls GET on load: it needs the CSRF cookie before it can POST
    anything, and it needs to know whether to show the app or the sign-in form.
    """

    permission_classes = [AllowAny]

    def get(self, request):
        get_token(request)  # forces the cookie even on an anonymous request
        user = request.user
        if not user.is_authenticated:
            return Response({"authenticated": False})
        return Response(
            {
                "authenticated": True,
                "email": user.email,
                "full_name": user.full_name,
                "email_verified": user.is_email_verified,
                # The client routes on this. It is computed here rather than
                # inferred from an empty order list: "no orders yet" and "has
                # not finished signing up" are different states that happen to
                # look identical from the dashboard's point of view.
                "needs_onboarding": _needs_onboarding(user),
            }
        )

    def delete(self, request):
        # Django's logout flushes the session, so the old key cannot be reused.
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class SignInView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [SignInThrottle, EmailScopedThrottle]
    throttle_scope = "auth_sign_in"

    def post(self, request):
        data = SignInSerializer(data=request.data)
        data.is_valid(raise_exception=True)

        try:
            user = identity.authenticate(
                data.validated_data["email"], data.validated_data["password"]
            )
        except identity.AccountLocked as e:
            return _fail(e, status.HTTP_423_LOCKED)
        except identity.AuthError as e:
            return _fail(e)

        # login() cycles the session key, which is the session-fixation defence:
        # a key an attacker planted before sign-in is not the key that ends up
        # authenticated.
        login(request, user, backend=SESSION_BACKEND)

        # Signing in to an unverified account has to SEND the code, not just
        # route to the screen that asks for one. Without this you land on
        # /verify with an empty inbox and nothing to type.
        if not user.is_email_verified:
            _issue_and_send(user, EmailCode.Purpose.VERIFY)

        return Response({"next": _destination(user)})


class SignUpView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [SignUpThrottle, EmailScopedThrottle]
    throttle_scope = "auth_sign_up"

    def post(self, request):
        data = SignUpSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        email = data.validated_data["email"]

        try:
            user = identity.create_account(
                email, data.validated_data["password"], data.validated_data["full_name"]
            )
        except identity.AuthError as e:
            if e.reason == "email_taken":
                # Do NOT say the address is taken. Send a verification code to
                # the existing account and return the same response as a fresh
                # signup: someone who owns the address gets a usable code, and
                # someone probing learns nothing.
                existing = User.objects.filter(email=email).first()
                if existing and not existing.is_email_verified:
                    _issue_and_send(existing, EmailCode.Purpose.VERIFY)
                return Response({"next": _verify_url(email)})
            return _fail(e, status.HTTP_400_BAD_REQUEST)

        _issue_and_send(user, EmailCode.Purpose.VERIFY)
        login(request, user, backend=SESSION_BACKEND)
        return Response({"next": _verify_url(email)})


class RequestCodeView(APIView):
    """Resend a verification code. Always reports success."""

    permission_classes = [AllowAny]
    throttle_classes = [CodeThrottle, EmailScopedThrottle]
    throttle_scope = "auth_code"

    def post(self, request):
        data = EmailSerializer(data=request.data)
        data.is_valid(raise_exception=True)

        user = User.objects.filter(email=data.validated_data["email"]).first()
        if user and not user.is_email_verified:
            _issue_and_send(user, EmailCode.Purpose.VERIFY)
        return Response({"ok": True})


class VerifyView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [CodeThrottle, EmailScopedThrottle]
    throttle_scope = "auth_code"

    def post(self, request):
        data = CodeSerializer(data=request.data)
        data.is_valid(raise_exception=True)

        user = User.objects.filter(email=data.validated_data["email"]).first()
        if user is None:
            # Same message as a wrong code — an unknown address must not be
            # distinguishable from a bad code.
            return _fail(
                identity.AuthError(
                    "unknown_email", "That code is not right, or it has expired."
                ),
                status.HTTP_400_BAD_REQUEST,
            )

        try:
            identity.verify_email(user, data.validated_data["code"])
        except identity.AuthError as e:
            return _fail(e, status.HTTP_400_BAD_REQUEST)

        if not request.user.is_authenticated:
            login(request, user, backend=SESSION_BACKEND)

        # _destination, not a hard-coded DASHBOARD. Verifying an address is the
        # step BEFORE onboarding, so for most people finishing here the next
        # screen is the onboarding form, not the dashboard.
        return Response({"next": _destination(user)})


class ForgotView(APIView):
    """Start a reset. Always reports success — see identity.start_password_reset."""

    permission_classes = [AllowAny]
    throttle_classes = [CodeThrottle, EmailScopedThrottle]
    throttle_scope = "auth_code"

    def post(self, request):
        data = EmailSerializer(data=request.data)
        data.is_valid(raise_exception=True)

        user = User.objects.filter(email=data.validated_data["email"]).first()
        if user:
            _issue_and_send(user, EmailCode.Purpose.RESET)
        return Response({"ok": True})


class ResetView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [CodeThrottle, EmailScopedThrottle]
    throttle_scope = "auth_code"

    def post(self, request):
        data = ResetSerializer(data=request.data)
        data.is_valid(raise_exception=True)

        try:
            user = identity.complete_password_reset(
                data.validated_data["email"],
                data.validated_data["code"],
                data.validated_data["password"],
            )
        except identity.AuthError as e:
            return _fail(e, status.HTTP_400_BAD_REQUEST)

        # Sign them in on the new password. They proved control of the inbox,
        # which is the same evidence sign-in would have asked for.
        login(request, user, backend=SESSION_BACKEND)

        # Signing in to an unverified account has to SEND the code, not just
        # route to the screen that asks for one. Without this you land on
        # /verify with an empty inbox and nothing to type.
        if not user.is_email_verified:
            _issue_and_send(user, EmailCode.Purpose.VERIFY)

        return Response({"next": _destination(user)})


class AcceptInviteView(APIView):
    """
    Set the password on an account Genmars created for a client.

    Throttled exactly like the other code endpoints: an invite code is a
    six-digit credential, and an unthrottled endpoint that sets a password from
    one is a brute-force target with a very small keyspace.

    Signs them in on success. They have just proved inbox control and chosen a
    password; asking them to immediately type it again is friction with nothing
    behind it.
    """

    permission_classes = [AllowAny]
    throttle_classes = [CodeThrottle, EmailScopedThrottle]
    throttle_scope = "auth_code"

    def post(self, request):
        data = InviteAcceptSerializer(data=request.data)
        data.is_valid(raise_exception=True)

        try:
            user = identity.accept_invite(
                data.validated_data["email"],
                data.validated_data["code"],
                data.validated_data["password"],
            )
        except identity.AuthError as e:
            return _fail(e, status.HTTP_400_BAD_REQUEST)

        login(request, user, backend=SESSION_BACKEND)
        return Response({"next": _destination(user)})


class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        data = ChangePasswordSerializer(data=request.data)
        data.is_valid(raise_exception=True)

        try:
            identity.change_password(
                request.user,
                data.validated_data["current_password"],
                data.validated_data["new_password"],
            )
        except identity.AuthError as e:
            return _fail(e, status.HTTP_400_BAD_REQUEST)

        # Changing a password must not sign you out of the tab you are in.
        # update_session_auth_hash re-signs the session with the new password.
        from django.contrib.auth import update_session_auth_hash

        update_session_auth_hash(request, request.user)
        return Response({"ok": True})


# ─────────────────────────────────────────────────────────────────────────────
# Signing in to a sibling application
# ─────────────────────────────────────────────────────────────────────────────
#
# Three endpoints, and it is worth being clear which is called by what:
#
#   GET  sign-on/app        a BROWSER, possibly signed out. Describes the app
#                           asking, so the consent screen can name it.
#   POST sign-on/authorize  a BROWSER, signed in, having agreed. Mints the code.
#   POST sign-on/token      a SIBLING'S SERVER. Swaps the code for an identity.
#
# The third one is the only endpoint in this file called by a machine, and it is
# the only one where being unhelpful about failures is the correct behaviour —
# see identity.redeem_grant.


class SignOnQuerySerializer(serializers.Serializer):
    client_id = serializers.CharField(max_length=64)
    redirect_uri = serializers.CharField(max_length=500)


class SignOnAuthorizeSerializer(SignOnQuerySerializer):
    # Opaque to us. The sibling's own CSRF defence for this flow, echoed back
    # untouched — we never parse it, never store it, and never decide anything
    # from it.
    state = serializers.CharField(max_length=500, allow_blank=True, required=False, default="")


class SignOnTokenSerializer(SignOnQuerySerializer):
    client_secret = serializers.CharField(max_length=200)
    code = serializers.CharField(max_length=200)


def _describe_app(app, redirect_uri: str) -> dict:
    return {
        "client_id": app.client_id,
        "name": app.system.name,
        "purpose": app.system.purpose,
        "audience": app.audience,
        "audience_label": app.get_audience_display(),
        "redirect_uri": redirect_uri,
    }


class SignOnAppView(APIView):
    """
    "Which application is asking, and may I go in?"

    Answers without a session, because the consent screen is shown to somebody
    who may still have to sign in — and a screen that cannot say what it is
    asking about until after you have signed in is a screen nobody should trust.

    ── WHY THIS ONE IS SPECIFIC ABOUT FAILURES ─────────────────────────────────

    Everything else in this file refuses vaguely, because being specific would
    tell an attacker whether an address is registered. Nothing here is secret:
    the client_id travels in the URL of the link that got the person here, and
    the redirect address belongs to the application, not to a person. Being
    vague would only mean an engineer wiring up a sibling gets "no" with no
    reason, which is how a misconfiguration lives for a week.
    """

    permission_classes = [AllowAny]
    throttle_classes = [SignOnThrottle]
    throttle_scope = "auth_sign_on"

    def get(self, request):
        form = SignOnQuerySerializer(data=request.query_params)
        form.is_valid(raise_exception=True)
        client_id = form.validated_data["client_id"]
        redirect_uri = form.validated_data["redirect_uri"].strip()

        app = identity.find_sign_on_app(client_id)
        if app is None:
            return Response(
                {"detail": "That application is not registered for Genmars sign-in."},
                status=status.HTTP_404_NOT_FOUND,
            )

        missing = app.why_not_usable()
        if missing:
            return Response(
                {
                    "detail": (
                        f"{app.system.name} is registered but not ready for sign-in: "
                        f"{', '.join(missing)}."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        if not app.allows_redirect(redirect_uri):
            return Response(
                {
                    "detail": (
                        "That return address is not registered for this "
                        "application, so we will not send anyone to it."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = request.user
        signed_in = bool(user and user.is_authenticated)
        body = _describe_app(app, redirect_uri)
        body["you"] = {
            "authenticated": signed_in,
            "email": user.email if signed_in else "",
            "admitted": bool(signed_in and app.admits(user)),
            # Said plainly, because "you cannot go in" with no reason is the
            # message that generates a support ticket.
            "blocked_because": self._blocked_because(app, user) if signed_in else "",
        }
        return Response(body)

    @staticmethod
    def _blocked_because(app, user) -> str:
        if app.admits(user):
            return ""
        if not user.is_email_verified:
            return "Your email address is not verified yet."
        if app.audience == app.Audience.STAFF and not user.is_staff:
            return "This application is for Genmars staff only."
        return "This account cannot sign in to that application."


class SignOnAuthorizeView(APIView):
    """
    Mint the one-time code and say where to send the browser.

    Returns the address rather than issuing a 302, so the code is never put in a
    redirect the frontend did not ask for — the person has pressed a button that
    says which application they are joining, and this is the response to that
    press.
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [SignOnThrottle]
    throttle_scope = "auth_sign_on"

    def post(self, request):
        form = SignOnAuthorizeSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        redirect_uri = form.validated_data["redirect_uri"].strip()

        app = identity.find_sign_on_app(form.validated_data["client_id"])
        if app is None or not app.is_usable or not app.allows_redirect(redirect_uri):
            # Collapsed on purpose here, unlike the GET above: by this point a
            # request is being made with a session, and the specific answer is
            # already available from the endpoint that does not need one.
            return Response(
                {"detail": "That sign-in link is not valid."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not app.admits(request.user):
            return Response(
                {"detail": SignOnAppView._blocked_because(app, request.user)},
                status=status.HTTP_403_FORBIDDEN,
            )

        code = identity.issue_grant(app=app, user=request.user, redirect_uri=redirect_uri)

        # The app name and who went where, never the code.
        log.info(
            "sign-on granted: %s → %s", request.user.email, app.system.slug
        )

        state = form.validated_data.get("state") or ""
        target = f"{redirect_uri}?code={quote(code)}"
        if state:
            target = f"{target}&state={quote(state)}"
        return Response({"redirect_to": target})


class SignOnTokenView(APIView):
    """
    A sibling's server swapping a code for one answer about one person.

    ══════════════════════════════════════════════════════════════════════════
    NO SESSION, NO COOKIE, NO CSRF — AND THAT IS CORRECT HERE.

    The caller is a server holding a secret, not a browser carrying a cookie.
    DRF's SessionAuthentication only enforces CSRF once it has found a user in
    the session; this request carries none, so `request.user` is anonymous and
    no CSRF check applies. Nothing about that is a hole: the credential IS the
    secret, and a browser cannot be tricked into supplying one it never has.
    ══════════════════════════════════════════════════════════════════════════

    ── WHAT COMES BACK, AND WHY IT IS NOT MORE ─────────────────────────────────

    Who the person is, once. No token to keep, no scope to widen, no second
    endpoint behind it. The sibling starts its own session from this and never
    calls us again for that person.

    `organisations` is included because a sibling that serves clients cannot
    scope anything without it, and it is the person's OWN memberships — the
    same fact the portal already shows them. It is empty for most staff.
    """

    permission_classes = [AllowAny]
    throttle_classes = [SignOnTokenIPThrottle, SignOnClientThrottle]
    throttle_scope = "auth_sign_on_token"

    def post(self, request):
        form = SignOnTokenSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        data = form.validated_data

        try:
            user = identity.redeem_grant(
                client_id=data["client_id"],
                client_secret=data["client_secret"],
                code=data["code"],
                redirect_uri=data["redirect_uri"].strip(),
            )
        except identity.AuthError as e:
            return _fail(e, status.HTTP_400_BAD_REQUEST)

        memberships = (
            Membership.objects.select_related("organisation")
            .filter(user=user)
            .order_by("organisation__name")
        )

        return Response(
            {
                "account": {
                    "id": user.pk,
                    "email": user.email,
                    "full_name": user.full_name,
                    "is_staff": user.is_staff,
                    # Empty string for a client, and for a staff account whose
                    # role has not been decided. The sibling should treat both
                    # as "no authority", which is what it means here too.
                    "staff_role": user.staff_role,
                    "email_verified": user.is_email_verified,
                },
                "organisations": [
                    {"id": m.organisation_id, "name": m.organisation.name}
                    for m in memberships
                ],
                "issued_at": timezone.now().isoformat(),
            }
        )
