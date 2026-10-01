"""
The admin login form, with a second factor.

── IT IS THE SAME DOOR, NOT A SECOND ONE ───────────────────────────────────────

`config/urls.py` already replaces `admin.site.login` with a rate-limited
version rather than adding a parallel view, for the stated reason that
limiting a door nobody uses limits nothing. The same applies here: this
subclasses `AdminAuthenticationForm` and is installed as
`admin.site.login_form`, so there is one login form and it is this one.

── THE PASSWORD IS CHECKED FIRST, AND THE CODE SECOND ──────────────────────────

`super().clean()` runs the ordinary authentication — which means the lockout in
`IdentityBackend` still counts a wrong password as a wrong password, and a
locked account is refused before the code is ever looked at. A second factor
that let somebody probe passwords without consuming the lockout would have made
the first factor weaker, not the account stronger.

── AND A WRONG CODE SAYS THE SAME THING AS A WRONG PASSWORD ────────────────────

Deliberately, and by construction rather than by copying the sentence:
`get_invalid_login_error()` is the one Django raises for a bad password.

"Password right, code wrong" tells an attacker they have found a working
password and that the only thing left to get is the phone. The uniform message
tells them nothing they did not already know — the same reasoning
`accounts/identity.py` applies to "no such account" versus "wrong password".
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.admin.forms import AdminAuthenticationForm
from django.core.exceptions import ValidationError
from django import forms

class AdminTotpLoginForm(AdminAuthenticationForm):
    token = forms.CharField(
        label="Authenticator code",
        required=False,
        max_length=16,
        strip=True,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "one-time-code",
                "inputmode": "numeric",
                "placeholder": "6 digits",
            }
        ),
        help_text="From your authenticator app. Leave blank if you have not set one up.",
    )

    def clean(self):
        # Password first: the lockout and the rate limit are attached to it,
        # and they must still fire for somebody guessing passwords.
        cleaned = super().clean()

        user = self.get_user()
        if user is None:
            return cleaned

        device = getattr(user, "totp", None)
        enrolled = device is not None and device.is_confirmed

        if not enrolled:
            # ── THE ROLLOUT SWITCH ──────────────────────────────────────
            # Off by default. On, nobody without a confirmed device gets in
            # — which is the end state, and is exactly what would lock the
            # company out of its own admin if it were the default on the
            # day this shipped. See the banner on StaffTotp.
            if getattr(settings, "ADMIN_REQUIRE_TOTP", False):
                raise ValidationError(
                    "This account has no authenticator set up, and one is "
                    "required. Run `manage.py enrol_totp` from the server.",
                    code="totp_required",
                )
            return cleaned

        if not device.check_code(self.cleaned_data.get("token", "")):
            # ── DJANGO'S OWN WORDING, NOT A COPY OF IT ──────────────────
            # `get_invalid_login_error()` is what AuthenticationForm raises
            # for a wrong password, so this is the identical sentence by
            # construction and stays identical if Django ever rewords it.
            #
            # Writing the message out by hand is what the first version did,
            # and the two drifted immediately: "...email, password and code..."
            # against Django's "...email and password... case-sensitive." A
            # wrong code therefore announced itself as a wrong CODE, which
            # tells an attacker the password was right and the only thing
            # left is the phone. The test compares the two strings for
            # exactly this reason.
            raise self.get_invalid_login_error()

        return cleaned
