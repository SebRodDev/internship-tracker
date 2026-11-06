"""Small HTTP client: gzip, retries, and a per-host concurrency cap."""
from __future__ import annotations

import gzip
import http.client
import json
import random
import threading
import time
import urllib.error
import urllib.request
import zlib
from collections import defaultdict
from urllib.parse import urlparse

USER_AGENT = "personal-internship-tracker/1.0"
MAX_PER_HOST = 8

_lock = threading.Lock()
_host_slots = defaultdict(lambda: threading.BoundedSemaphore(MAX_PER_HOST))


class FetchError(Exception):
    """The request failed; the board may work next run."""


class Gone(FetchError):
    """The server returned 404, so the board may no longer exist."""


class Unchanged(Exception):
    """The board is the same as last time, so there's nothing to read."""


def _slot(url):
    with _lock:
        return _host_slots[urlparse(url).netloc]


def fetch_text(url, timeout=30):
    """GET a web page and return its text."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with _slot(url), urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", "replace")
    except (OSError, http.client.HTTPException) as error:
        raise FetchError("%s: %r" % (url, error))


def fetch_json(url, body=None, timeout=20, retries=2, cache=None, extra_headers=None):
    """GET a URL, or POST `body` as JSON, and return the parsed response.

    Pass a dict as `cache` to remember the server's ETag there. If the content hasn't
    changed by the next request, the server sends no body and this raises Unchanged.
    """
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
    }
    headers.update(extra_headers or {})
    if cache is not None and cache.get("etag"):
        headers["If-None-Match"] = cache["etag"]
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"

    last_error = None
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(1.5 * attempt + random.random())
        try:
            request = urllib.request.Request(url, data=data, headers=headers)
            with _slot(url), urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                etag = response.headers.get("ETag")
                if response.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
            parsed = json.loads(raw)
            if cache is not None and etag:
                cache["etag"] = etag
            return parsed
        except urllib.error.HTTPError as error:
            if error.code == 304:
                raise Unchanged(url)
            if error.code == 404:
                raise Gone(url)
            last_error = "HTTP %s" % error.code
            if error.code != 429 and error.code < 500:
                break
        except (OSError, ValueError, EOFError, http.client.HTTPException, zlib.error) as error:
            last_error = repr(error)
    raise FetchError("%s: %s" % (url, last_error))
