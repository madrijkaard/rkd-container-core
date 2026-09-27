"""Server-side Cloudflare Turnstile validation for operator login."""

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings


SITEVERIFY_URL = 'https://challenges.cloudflare.com/turnstile/v0/siteverify'


def verify_turnstile(token):
    """Return True for a valid login challenge, False for rejection, None for provider failure."""
    if not isinstance(token, str) or not token or len(token) > 2048:
        return False
    request = Request(
        SITEVERIFY_URL,
        data=urlencode({'secret': settings.TURNSTILE_SECRET_KEY, 'response': token}).encode('ascii'),
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        method='POST',
    )
    try:
        with urlopen(request, timeout=5) as response:
            result = json.load(response)
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        return None
    if not isinstance(result, dict) or result.get('success') is not True:
        return False
    if result.get('action') != 'login':
        return False
    allowed = settings.TURNSTILE_ALLOWED_HOSTNAMES
    hostname = result.get('hostname')
    if allowed and (not isinstance(hostname, str) or hostname.lower() not in allowed):
        return False
    return True
