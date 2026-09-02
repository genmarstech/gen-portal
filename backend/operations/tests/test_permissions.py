"""
Teams and permissions.

Read is shared, write is scoped. Three people hiding work from each other would
be theatre; deciding who may commit the company is not, and Charter 02 §I
already decided it — qualification to the commercial partners, capacity veto
and pricing to the founder.

THE TESTS THAT MATTER MOST ARE THE LOCKOUT GUARDS. A permission model that can
strand itself is worse than none, because it fails at the moment somebody is
already having a bad day.
"""

from __future__ import annotations

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from accounts.models import Membership, Organisation, User
from operations import services
from portal.models import Enquiry, Order

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def _clear_throttles():
    from django.core.cache import cache
    cache.clear()
    yield
    cache.clear()


def staff_user(email: str, role: str) -> User:
    return User.objects.create_user(
        email=email, password=PASSWORD, full_name=email.split("@")[0],
        is_staff=True, staff_role=role, email_verified_at=timezone.now(),
    )


@pytest.fixture
def founder() -> User:
    return staff_user("founder@genmars.co.ke", User.StaffRole.FOUNDER)


@pytest.fixture
def commercial() -> User:
    return staff_user("commercial@genmars.co.ke", User.StaffRole.COMMERCIAL)


@pytest.fixture
def engineer() -> User:
    return staff_user("engineer@genmars.co.ke", User.StaffRole.DELIVERY)


@pytest.fixture
def enquiry() -> Enquiry:
    org = Organisation.objects.create(name="Kilimani Dental")
    client_user = User.objects.create_user(
        email="client@example.com", password=PASSWORD, email_verified_at=timezone.now()
    )
    Membership.objects.create(user=client_user, organisation=org)
    return Enquiry.objects.create(
        organisation=org, submitted_by=client_user, problem="Reconciling by hand."
    )


@pytest.fixture
def order(founder, enquiry) -> Order:
    return services.convert_enquiry(
        enquiry=enquiry, actor=founder, title="Work", scope="Do the thing."
    )


# ── read is shared ───────────────────────────────────────────────────────────


def test_every_staff_role_can_read_everything(client, engineer, order):
    """
    A delivery engineer who cannot see the pipeline is being managed, not
    trusted. In a company of three that is machinery for its own sake.
    """
    client.force_login(engineer)
    for name in ["ops-overview", "ops-enquiries", "ops-orders", "ops-delivery", "ops-staff"]:
        assert client.get(reverse(name)).status_code == 200, name
    assert client.get(reverse("ops-order", args=[order.reference])).status_code == 200


def test_a_staff_account_with_no_role_can_read_but_not_change(client, order, enquiry):
    """
    The safe state for somebody whose role has not been decided. It is what a
    new account looks like if a migration or an admin forgets, so it must fail
    closed rather than open.
    """
    nobody = staff_user("nobody@genmars.co.ke", "")
    client.force_login(nobody)

    assert client.get(reverse("ops-orders")).status_code == 200
    assert client.post(
        reverse("ops-convert", args=[enquiry.pk]),
        {"title": "x", "scope": "y"}, content_type="application/json",
    ).status_code == 403
    assert client.post(
        reverse("ops-contracts", args=[order.reference]), {}, content_type="application/json"
    ).status_code == 403


# ── write is scoped ──────────────────────────────────────────────────────────


def test_an_engineer_cannot_qualify_an_enquiry(client, engineer, enquiry):
    """Charter 02 §I — converting commits the company's capacity."""
    client.force_login(engineer)
    response = client.post(
        reverse("ops-convert", args=[enquiry.pk]),
        {"title": "Work", "scope": "Do the thing."},
        content_type="application/json",
    )
    assert response.status_code == 403
    assert Order.objects.count() == 0


def test_a_commercial_partner_can_qualify(client, commercial, enquiry):
    client.force_login(commercial)
    response = client.post(
        reverse("ops-convert", args=[enquiry.pk]),
        {"title": "Work", "scope": "Do the thing."},
        content_type="application/json",
    )
    assert response.status_code == 201, response.json()


def test_an_engineer_cannot_issue_or_sign_a_contract(client, engineer, order):
    """Money and commitment are the same authority."""
    client.force_login(engineer)
    assert client.post(
        reverse("ops-contracts", args=[order.reference]), {}, content_type="application/json"
    ).status_code == 403


def test_an_engineer_can_do_delivery_work(client, engineer, order):
    """
    The point of the split is not to stop engineers working. Gates, blockers
    and progress notes are open to every staff account.
    """
    client.force_login(engineer)
    gate = order.gates.first()
    assert client.post(
        reverse("ops-gate", args=[order.reference, gate.pk]),
        {"met": True, "note": "Checked against real statements."},
        content_type="application/json",
    ).status_code == 200
    assert client.post(
        reverse("ops-blockers", args=[order.reference]),
        {"summary": "Waiting on credentials", "waiting_on": "client"},
        content_type="application/json",
    ).status_code == 201


def test_only_a_founder_can_reach_client_access(client, commercial, engineer):
    """
    Giving somebody access to a client's commercial detail is an access
    decision, not a commercial one.
    """
    for user in (commercial, engineer):
        client.force_login(user)
        assert client.get(reverse("ops-organisations")).status_code == 403, user.email


# ── the team ─────────────────────────────────────────────────────────────────


def test_only_a_founder_can_change_a_role(client, commercial, engineer):
    client.force_login(commercial)
    response = client.patch(
        reverse("ops-staff-member", args=[engineer.pk]),
        {"role": "founder"}, content_type="application/json",
    )
    assert response.status_code == 403
    engineer.refresh_from_db()
    assert engineer.staff_role == User.StaffRole.DELIVERY


def test_a_founder_can_change_a_role(client, founder, engineer):
    client.force_login(founder)
    response = client.patch(
        reverse("ops-staff-member", args=[engineer.pk]),
        {"role": "commercial"}, content_type="application/json",
    )
    assert response.status_code == 200, response.json()
    engineer.refresh_from_db()
    assert engineer.staff_role == User.StaffRole.COMMERCIAL


def test_the_last_founder_cannot_be_demoted(founder, engineer):
    """
    can_manage_access is founder-only, so removing the last one leaves a system
    nobody can grant anything in — recoverable only by a shell on the
    production box.
    """
    with pytest.raises(services.OperationsError):
        services.set_staff_role(
            actor=founder, user=founder, role=User.StaffRole.COMMERCIAL
        )
    founder.refresh_from_db()
    assert founder.staff_role == User.StaffRole.FOUNDER


def test_a_founder_can_step_down_once_there_is_another(founder, engineer):
    services.set_staff_role(actor=founder, user=engineer, role=User.StaffRole.FOUNDER)
    services.set_staff_role(actor=founder, user=founder, role=User.StaffRole.DELIVERY)
    founder.refresh_from_db()
    assert founder.staff_role == User.StaffRole.DELIVERY


def test_the_last_founder_cannot_be_deactivated(founder):
    with pytest.raises(services.OperationsError):
        services.set_staff_active(actor=founder, user=founder, active=False)


def test_nobody_can_deactivate_themselves(founder, engineer):
    """Never what was meant, and it ends with somebody locked out of the system
    they were tidying."""
    services.set_staff_role(actor=founder, user=engineer, role=User.StaffRole.FOUNDER)
    with pytest.raises(services.OperationsError):
        services.set_staff_active(actor=founder, user=founder, active=False)


def test_deactivating_revokes_sign_in_but_keeps_authorship(client, founder, engineer, order):
    from portal.models import ProgressNote

    note = ProgressNote.objects.create(
        order=order, author=engineer, week_of=timezone.localdate(), body="Did a thing."
    )
    services.set_staff_active(actor=founder, user=engineer, active=False)

    engineer.refresh_from_db()
    assert not engineer.is_active
    # Django refuses an inactive user, so this is a real revocation.
    assert client.login(email=engineer.email, password=PASSWORD) is False
    # And the record of what they did survives.
    note.refresh_from_db()
    assert note.author == engineer


# ── staff invitations ────────────────────────────────────────────────────────


def test_inviting_a_colleague_creates_an_unusable_account_and_emails_them(founder):
    """Same guarantee as a client invite, and it matters more here — this
    account reads every client's commercial detail."""
    mail.outbox.clear()
    user, invited = services.invite_staff(
        actor=founder, email="New.Engineer@genmars.co.ke",
        full_name="New Engineer", role=User.StaffRole.DELIVERY,
    )
    assert invited
    assert user.email == "new.engineer@genmars.co.ke"
    assert user.is_staff and not user.has_usable_password()
    assert len(mail.outbox) == 1
    # It says what the account can see, before they choose a password.
    assert "client work across the whole company" in mail.outbox[0].body


def test_a_client_address_cannot_be_promoted_to_staff(founder):
    """
    Promoting a client account would give it is_staff while it still holds
    Memberships — reading every organisation through operations AND appearing
    as a client of one.
    """
    client_user = User.objects.create_user(
        email="someone@client.co.ke", password=PASSWORD, email_verified_at=timezone.now()
    )
    Membership.objects.create(
        user=client_user, organisation=Organisation.objects.create(name="A Client")
    )
    with pytest.raises(services.OperationsError):
        services.invite_staff(
            actor=founder, email=client_user.email, role=User.StaffRole.DELIVERY
        )
    client_user.refresh_from_db()
    assert not client_user.is_staff


def test_the_directory_tells_the_ui_what_this_user_may_do(client, engineer):
    """
    So the screen does not offer controls it would only get a 403 from. The
    server stays the authority — this is presentation, not enforcement.
    """
    client.force_login(engineer)
    me = client.get(reverse("ops-staff")).json()["me"]
    assert me["can_qualify"] is False
    assert me["can_commit"] is False
    assert me["can_manage_access"] is False


def test_no_client_account_reaches_the_team_endpoints(client, founder):
    outsider = User.objects.create_user(
        email="outsider@example.com", password=PASSWORD, email_verified_at=timezone.now()
    )
    client.force_login(outsider)
    assert client.get(reverse("ops-staff")).status_code == 403
    assert client.patch(
        reverse("ops-staff-member", args=[founder.pk]),
        {"role": "delivery"}, content_type="application/json",
    ).status_code == 403


# ── the named contact survives a revocation ──────────────────────────────────


def test_revoking_someone_who_is_still_a_named_contact_is_refused(founder, engineer, enquiry):
    """
    Charter 05 §I promises the client a contact they can reach. Revoking the
    person named on a live order would leave that promise pointing at an
    account that cannot sign in — filled in, and false.
    """
    order = services.convert_enquiry(
        enquiry=enquiry, actor=founder, title="Work", scope="Do the thing."
    )
    order.contact = engineer
    order.save(update_fields=["contact"])

    with pytest.raises(services.OperationsError) as caught:
        services.set_staff_active(actor=founder, user=engineer, active=False)

    # It names the order, so reassigning is one job rather than a hunt.
    assert order.reference in str(caught.value)
    engineer.refresh_from_db()
    assert engineer.is_active


def test_revoking_is_allowed_once_the_orders_are_reassigned(founder, engineer, enquiry):
    order = services.convert_enquiry(
        enquiry=enquiry, actor=founder, title="Work", scope="Do the thing."
    )
    order.contact = engineer
    order.save(update_fields=["contact"])

    order.contact = founder
    order.save(update_fields=["contact"])

    services.set_staff_active(actor=founder, user=engineer, active=False)
    engineer.refresh_from_db()
    assert not engineer.is_active


def test_a_delivered_order_does_not_block_a_revocation(founder, engineer, enquiry):
    """
    Finished work is a record, not a live promise. Blocking on it would mean
    every departure required rewriting history.
    """
    order = services.convert_enquiry(
        enquiry=enquiry, actor=founder, title="Work", scope="Do the thing."
    )
    order.contact = engineer
    order.status = Order.Status.DELIVERED
    order.save(update_fields=["contact", "status"])

    services.set_staff_active(actor=founder, user=engineer, active=False)
    engineer.refresh_from_db()
    assert not engineer.is_active
    # The finished order still names them, which is the truth about who ran it.
    order.refresh_from_db()
    assert order.contact == engineer


def test_a_revoked_colleague_cannot_be_named_as_a_contact(client, founder, engineer, order):
    """The other direction: not just revoking a contact, but naming a revoked
    account as one."""
    order.contact = founder
    order.save(update_fields=["contact"])
    services.set_staff_active(actor=founder, user=engineer, active=False)

    client.force_login(founder)
    response = client.patch(
        reverse("ops-order", args=[order.reference]),
        {"contact": engineer.pk}, content_type="application/json",
    )
    assert response.status_code == 400, response.json()
    order.refresh_from_db()
    assert order.contact == founder
