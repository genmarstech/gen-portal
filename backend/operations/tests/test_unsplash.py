"""
Choosing a picture.

═══════════════════════════════════════════════════════════════════════════════
THREE PROPERTIES, AND NONE OF THEM IS "THE SEARCH RETURNS PHOTOS".

1. **The access key never leaves this server.** Operations asks Django; Django
   asks Unsplash. A key in a JavaScript bundle is a key that has been given
   away, and nothing about the page would look wrong.

2. **The credit travels with the picture.** Unsplash's licence requires
   attribution, and a photograph published with no photographer beside it is a
   breach that looks like a cosmetic gap. The model refuses the pair apart.

3. **The rate limit is spent in one place.** The website build makes no calls
   and a visitor makes none — only an editor searching. These tests pin the
   caching that keeps a repeated search from costing twice.

Nothing here reaches the network: urlopen is replaced. A suite that talked to
Unsplash would burn the hourly limit and fail offline.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import json
import urllib.error
from unittest import mock

import pytest
from django.core.cache import cache
from django.urls import reverse

from accounts.models import User
from operations import unsplash
from portal.models import WorkItem

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"
KEY = "unsplash-test-key"


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _configured(settings):
    settings.UNSPLASH_ACCESS_KEY = KEY
    settings.UNSPLASH_APP_NAME = "genmars"


@pytest.fixture
def staff() -> User:
    from django.utils import timezone

    return User.objects.create_user(
        email="ops@genmars.co.ke", password=PASSWORD, full_name="Ops",
        is_staff=True, email_verified_at=timezone.now(),
    )


class _Response:
    def __init__(self, payload, headers=None):
        self._payload = json.dumps(payload).encode()
        self.headers = headers or {}

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


PHOTO = {
    "id": "abc123",
    "urls": {"regular": "https://images.unsplash.com/photo-1?w=1080",
             "thumb": "https://images.unsplash.com/photo-1?w=200"},
    "alt_description": "a shop counter",
    "user": {"name": "A Photographer", "username": "aphotographer"},
    "links": {"download_location": "https://api.unsplash.com/photos/abc123/download"},
}


def _answers(payload=None, headers=None):
    return mock.patch(
        "operations.unsplash.urllib.request.urlopen",
        return_value=_Response(payload or {"results": [PHOTO]}, headers),
    )


# ── the key stays here ──────────────────────────────────────────────────────


def test_the_key_is_sent_by_this_server_and_not_by_the_browser(client, staff):
    """
    The header proves Django made the call. If the ops screen ever called
    Unsplash directly this route would be unused and the key would be in a
    bundle — so what is asserted is that the request carries the credential.
    """
    client.force_login(staff)
    with _answers() as urlopen:
        client.get(reverse("ops-unsplash-search"), {"q": "shop"})

    request = urlopen.call_args[0][0]
    assert request.headers["Authorization"] == f"Client-ID {KEY}"
    assert "api.unsplash.com" in request.full_url


def test_the_key_is_never_in_a_response(client, staff):
    client.force_login(staff)
    with _answers():
        body = client.get(reverse("ops-unsplash-search"), {"q": "shop"}).content
    assert KEY.encode() not in body


def test_a_stranger_cannot_search(client):
    assert client.get(reverse("ops-unsplash-search"), {"q": "shop"}).status_code in (
        401, 403,
    )


# ── the credit travels with the picture ─────────────────────────────────────


def test_every_result_carries_a_photographer(client, staff):
    client.force_login(staff)
    with _answers():
        photos = client.get(
            reverse("ops-unsplash-search"), {"q": "shop"}
        ).json()["photos"]

    assert photos[0]["credit_name"] == "A Photographer"
    assert photos[0]["credit_url"].startswith(
        "https://unsplash.com/@aphotographer?"
    )


def test_the_credit_link_carries_the_attribution_parameters(client, staff):
    """
    Unsplash asks for utm_source and utm_medium on links back. Leaving them off
    still shows the name, so nothing looks wrong — which is exactly why it gets
    forgotten, and why it is built in the client rather than in a template.
    """
    client.force_login(staff)
    with _answers():
        photos = client.get(
            reverse("ops-unsplash-search"), {"q": "shop"}
        ).json()["photos"]

    assert "utm_source=genmars" in photos[0]["credit_url"]
    assert "utm_medium=referral" in photos[0]["credit_url"]


def test_a_picture_without_its_photographer_cannot_be_saved():
    """
    The model rule, not a form rule. A photograph with no credit beside it is
    a licence breach that looks like a cosmetic gap, so it is refused wherever
    the write comes from.
    """
    from django.core.exceptions import ValidationError

    item = WorkItem(
        slug="x", name="X", category=WorkItem.Category.SITE,
        label=WorkItem.Label.CONCEPT, summary="A thing.",
        image_url="https://images.unsplash.com/photo-1",
        image_alt="a shop counter",
    )
    with pytest.raises(ValidationError) as caught:
        item.full_clean()
    assert "image_credit_name" in caught.value.message_dict


def test_a_picture_without_alt_text_cannot_be_saved():
    from django.core.exceptions import ValidationError

    item = WorkItem(
        slug="x", name="X", category=WorkItem.Category.SITE,
        label=WorkItem.Label.CONCEPT, summary="A thing.",
        image_url="https://images.unsplash.com/photo-1",
        image_credit_name="A Photographer",
        image_credit_url="https://unsplash.com/@a",
    )
    with pytest.raises(ValidationError) as caught:
        item.full_clean()
    assert "image_alt" in caught.value.message_dict


def test_no_picture_is_an_ordinary_state():
    """Most work has none and needs none. The rule is about pairs, not
    about requiring an image."""
    item = WorkItem(
        slug="x", name="X", category=WorkItem.Category.SITE,
        label=WorkItem.Label.CONCEPT, summary="A thing.",
    )
    item.full_clean()


# ── the rate limit ──────────────────────────────────────────────────────────


def test_the_same_search_twice_costs_one_request(client, staff):
    client.force_login(staff)
    with _answers() as urlopen:
        client.get(reverse("ops-unsplash-search"), {"q": "shop"})
        client.get(reverse("ops-unsplash-search"), {"q": "shop"})
    assert urlopen.call_count == 1


def test_an_empty_search_costs_nothing(client, staff):
    """A blank box should not spend an hour's allowance on its way to
    returning nothing."""
    client.force_login(staff)
    with _answers() as urlopen:
        body = client.get(reverse("ops-unsplash-search"), {"q": "  "}).json()
    urlopen.assert_not_called()
    assert body["photos"] == []


def test_an_exhausted_limit_says_which_of_the_two_things_it_is(client, staff):
    """
    Unsplash answers 403 both for a spent limit and for a bad key, and those
    need different people to fix them. A single "search failed" sends whoever
    reads it to guess.
    """
    error = urllib.error.HTTPError("u", 403, "Forbidden", {}, mock.MagicMock(read=lambda: b""))
    client.force_login(staff)
    with mock.patch("operations.unsplash.urllib.request.urlopen", side_effect=error):
        body = client.get(reverse("ops-unsplash-search"), {"q": "shop"}).json()

    assert "limit" in body["detail"].lower()
    assert "key" in body["detail"].lower()


def test_being_unconfigured_is_not_an_error(client, staff, settings):
    """
    Everything else on the work screen must still save. An unset key is a
    server that cannot offer pictures, not a broken screen.
    """
    settings.UNSPLASH_ACCESS_KEY = ""
    client.force_login(staff)
    body = client.get(reverse("ops-unsplash-search"), {"q": "shop"}).json()
    assert body["configured"] is False
    assert body["photos"] == []


# ── the download ping ───────────────────────────────────────────────────────


def test_picking_reports_the_download(client, staff):
    """Required by Unsplash's terms — it is how photographers are credited
    with usage."""
    client.force_login(staff)
    with _answers({}) as urlopen:
        response = client.post(
            reverse("ops-unsplash-used"),
            {"download_location": PHOTO["links"]["download_location"]},
            content_type="application/json",
        )
    assert response.status_code == 204
    assert urlopen.call_count == 1


def test_a_failed_ping_does_not_fail_the_editor(client, staff):
    """
    A courtesy to the photographer, not part of saving somebody's work. A
    picture that would not save because a tracking call timed out is the wrong
    thing to break.
    """
    client.force_login(staff)
    with mock.patch(
        "operations.unsplash.urllib.request.urlopen",
        side_effect=urllib.error.URLError("down"),
    ):
        response = client.post(
            reverse("ops-unsplash-used"),
            {"download_location": "https://api.unsplash.com/photos/x/download"},
            content_type="application/json",
        )
    assert response.status_code == 204
