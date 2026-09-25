"""
Searching Unsplash, from the server.

═══════════════════════════════════════════════════════════════════════════════
THE ACCESS KEY NEVER REACHES A BROWSER.

Operations asks this Django, and this Django asks Unsplash. The obvious
alternative — the ops screen calling api.unsplash.com directly — would put the
key in a JavaScript bundle served to anybody who can load the page, and a key
in a bundle is a key that has been given away.

It also means the rate limit is spent in one place we can see, rather than once
per open browser tab.
═══════════════════════════════════════════════════════════════════════════════

── THE RATE LIMIT, AND WHERE IT IS ACTUALLY SPENT ─────────────────────────────

The demo tier allows 50 requests an hour. That sounds tight and is not, because
of WHERE the calls happen:

  · the website build makes NONE — the chosen URL and credit are stored on the
    item, so publishing and deploying cost nothing;
  · a visitor makes NONE — they load images.unsplash.com directly, which is
    their CDN and not their API;
  · only an editor searching in ops spends anything, and only on an explicit
    search rather than per keystroke.

That is a handful of requests on the days somebody is choosing pictures, and
zero on every other day. Moving to their production tier later changes nothing
here, which is the point of putting the boundary in this file.

⚠ DO NOT CALL THIS FROM A KEYSTROKE HANDLER. An autocomplete that searches as
  somebody types turns one picture into thirty requests and exhausts the hour
  for everybody else in the company.

── THE DOWNLOAD PING IS NOT OPTIONAL ──────────────────────────────────────────

Unsplash's API terms require a call to the photo's `download_location` when an
image is actually used — it is how photographers are credited with usage. It is
fired once, when an editor picks the image, NOT on every page view: the
photograph is used once by us and read many times by visitors, and pinging per
view would both misreport usage and spend the limit on traffic.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.core.cache import cache

log = logging.getLogger(__name__)

SEARCH_ENDPOINT = "https://api.unsplash.com/search/photos"

# Enough to choose from, few enough to look at. A grid of thirty is a decision
# nobody makes; it is also three times the payload for the same outcome.
PER_PAGE = 12

TIMEOUT_SECONDS = 10

# Identical searches inside this window are answered from memory rather than
# from Unsplash. Somebody comparing two pictures types the same word twice,
# and that should not cost twice.
CACHE_SECONDS = 15 * 60


class UnsplashError(Exception):
    """
    Unsplash could not be asked, or refused.

    One exception with no subclasses: the caller's response to every variety is
    to tell the editor to try again, and the detail belongs in our log.
    """


def is_configured() -> bool:
    return bool(settings.UNSPLASH_ACCESS_KEY)


def _get(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Client-ID {settings.UNSPLASH_ACCESS_KEY}",
            # Their API is versioned by header, not by path. Without this they
            # serve whatever is current, and a response shape can change under
            # us on a day nobody deployed anything.
            "Accept-Version": "v1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            remaining = response.headers.get("X-Ratelimit-Remaining")
            if remaining is not None and remaining.isdigit() and int(remaining) < 10:
                # Worth a line before it bites. The failure otherwise arrives
                # as a 403 in the middle of somebody's afternoon with nothing
                # explaining why it worked an hour ago.
                log.warning("unsplash rate limit is nearly spent: %s left", remaining)
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode()[:300]
        except Exception:
            pass
        if exc.code == 403:
            # Their 403 for an exhausted limit is indistinguishable from a bad
            # key at the status line, so the message says both rather than
            # guessing and sending somebody to check the wrong thing.
            raise UnsplashError(
                "Unsplash refused. Either the hourly limit is spent — it "
                "resets on the hour — or the access key is wrong."
            ) from exc
        raise UnsplashError(f"Unsplash answered {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise UnsplashError(f"Could not reach Unsplash: {exc}") from exc


def _credit_url(user: dict) -> str:
    """
    A photographer's profile, carrying the attribution parameters.

    Unsplash asks for utm_source and utm_medium on links back. Omitting them
    still shows the name, so nothing looks wrong — which is exactly why it gets
    left out, and exactly why it is built here rather than in a template.
    """
    handle = (user or {}).get("username", "")
    if not handle:
        return ""
    query = urllib.parse.urlencode(
        {
            "utm_source": settings.UNSPLASH_APP_NAME or "genmars",
            "utm_medium": "referral",
        }
    )
    return f"https://unsplash.com/@{handle}?{query}"


def search(query: str) -> list[dict]:
    """
    Photos matching a phrase, as much of each as the picker needs.

    Returns [] for an empty query rather than asking Unsplash for everything —
    a blank search box should not cost a request.
    """
    query = (query or "").strip()
    if not query:
        return []
    if not is_configured():
        raise UnsplashError("Unsplash is not configured on this server.")

    key = f"unsplash:search:{query.lower()}"
    cached = cache.get(key)
    if cached is not None:
        return cached

    url = SEARCH_ENDPOINT + "?" + urllib.parse.urlencode(
        {"query": query, "per_page": PER_PAGE, "orientation": "landscape"}
    )
    payload = _get(url)

    photos = [
        {
            "id": photo.get("id", ""),
            # `regular` is ~1080px wide. `full` is the original, which on a
            # card is several megabytes to render at 320px.
            "url": (photo.get("urls") or {}).get("regular", ""),
            "thumb": (photo.get("urls") or {}).get("thumb", ""),
            # Their alt_description is often absent and always generic. It is
            # offered as a starting point and the editor is expected to
            # replace it — see the help text on WorkItem.image_alt.
            "alt": photo.get("alt_description") or "",
            "credit_name": ((photo.get("user") or {}).get("name") or ""),
            "credit_url": _credit_url(photo.get("user") or {}),
            # Held so the ping below can be fired on selection without a
            # second search to find the photo again.
            "download_location": (
                (photo.get("links") or {}).get("download_location", "")
            ),
        }
        for photo in payload.get("results", [])
    ]

    cache.set(key, photos, CACHE_SECONDS)
    return photos


def note_download(download_location: str) -> None:
    """
    Tell Unsplash the photo was used. Required by their API terms.

    Deliberately swallows every failure. This is a courtesy to the
    photographer and to Unsplash; it is not part of saving the editor's work,
    and a picture that will not save because a tracking ping timed out would
    be the wrong thing to break.
    """
    if not (download_location and is_configured()):
        return
    try:
        _get(download_location)
    except UnsplashError as exc:
        log.warning("could not report the unsplash download: %s", exc)
