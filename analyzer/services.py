"""
analyzer/services.py

Pure analysis logic — no Django / ORM imports, so it's trivially unit-
testable and safe to call from synchronous request handlers or Celery
workers alike.

Public surface
--------------
``analyze_webpage(url) -> dict``
    Fetches the given URL, extracts internal navigation links, external
    stylesheets, and client-side scripts, and returns a dict with raw
    counts plus structured metadata ready to be fed to a charting
    library (Recharts in Stage 4).

``analyze_html_content(html, source_label) -> dict``
    Same return shape as ``analyze_webpage`` but driven by HTML bytes /
    text the user uploaded directly (FR-03). No network access happens.

On network or HTTP failure the URL variant raises — callers are expected
to handle the exception (the Celery task does this by retrying the
first couple of attempts and finally marking the report FAILED).
"""

from __future__ import annotations

import contextlib
import ipaddress
import socket
from collections import Counter
from typing import Iterable, Iterator
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from urllib3.util import connection as _urllib3_connection

REQUEST_TIMEOUT_SEC = 10
MAX_REDIRECTS = 5
#: Hard cap on the response body the Celery worker is willing to load
#: into memory when scanning a URL. Matches the upload limit so a hostile
#: target page cannot OOM the worker (audit M-3).
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
ALLOWED_SCHEMES = frozenset({"http", "https"})
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 FEAnalyzer/1.0"
)


class UnsafeURLError(ValueError):
    """
    Raised when a target URL is rejected by SSRF defences:
    bad scheme, missing host, a host that resolves to a non-public IP
    (loopback, private RFC 1918, link-local, multicast, reserved, or the
    cloud-metadata 169.254.169.254 family), or a response body that
    overflows the per-fetch byte cap.
    """


def _validate_url_safety(url: str) -> str:
    """
    Server-Side Request Forgery (SSRF) gate for any URL we are about to fetch.

    Rejects:
        * non-http(s) schemes (e.g. ``file://``, ``gopher://``)
        * missing hostnames
        * IP literals or DNS names that resolve to a *non-public* address
          (loopback, RFC 1918 private, link-local, multicast, reserved,
          unspecified). This blocks attacks like submitting
          ``http://169.254.169.254/`` (cloud metadata),
          ``http://10.0.0.1/`` (internal LAN), ``http://localhost:5432/``,
          or DNS records that point at any of the above.

    Returns
    -------
    str
        The first validated IP literal for the URL's hostname. The caller
        is expected to pin urllib3's TCP connect to this exact address
        (see :func:`_pin_dns`) so a hostile DNS server cannot return one
        IP at validation time and a different one at connect time
        (DNS-rebinding TOCTOU — audit C-C).

    Raises
    ------
    UnsafeURLError
        With a user-safe message that callers can surface verbatim.
    """
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeURLError(
            f"Only http and https URLs are allowed (got '{parsed.scheme or '?'}')."
        )
    host = parsed.hostname
    if not host:
        raise UnsafeURLError("URL is missing a hostname.")

    try:
        addrinfo = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"Could not resolve hostname '{host}'.") from exc

    first_ip: str | None = None
    for entry in addrinfo:
        sockaddr = entry[4]
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise UnsafeURLError(
                f"URL host '{host}' resolves to a non-public address ({ip}); "
                "refusing to fetch."
            )
        if first_ip is None:
            first_ip = str(ip)

    if first_ip is None:
        raise UnsafeURLError(f"Could not resolve hostname '{host}'.")
    return first_ip


@contextlib.contextmanager
def _pin_dns(host: str, ip: str) -> Iterator[None]:
    """
    Force ``urllib3`` to TCP-connect to a specific IP for ``host``.

    Defeats the DNS-rebinding TOCTOU window between
    :func:`_validate_url_safety` (which calls ``getaddrinfo`` once) and the
    actual ``requests.Session.get`` call (which would otherwise resolve the
    host *again* via urllib3 and could land on a hostile private IP).

    Implementation: monkey-patches
    ``urllib3.util.connection.create_connection`` for the duration of the
    ``with`` block, redirecting any connect attempt to ``host`` onto
    ``ip`` while preserving the original hostname for SNI / certificate
    verification on HTTPS.
    """
    original = _urllib3_connection.create_connection

    def patched(address, *args, **kwargs):
        addr_host, addr_port = address
        if addr_host == host:
            return original((ip, addr_port), *args, **kwargs)
        return original(address, *args, **kwargs)

    _urllib3_connection.create_connection = patched
    try:
        yield
    finally:
        _urllib3_connection.create_connection = original


def _read_capped_text(response: requests.Response, max_bytes: int) -> str:
    """
    Read a streaming ``requests.Response`` body up to ``max_bytes`` and
    decode it as text using the response's declared encoding (UTF-8 fallback).

    Raises :class:`UnsafeURLError` if the body exceeds the cap so the
    Celery worker can never be coerced into loading a multi-gigabyte
    payload into memory (audit M-3).
    """
    chunks: list[bytes] = []
    received = 0
    for chunk in response.iter_content(chunk_size=8192):
        if not chunk:
            continue
        received += len(chunk)
        if received > max_bytes:
            response.close()
            raise UnsafeURLError(
                f"Response body exceeds the {max_bytes}-byte cap; refusing to load."
            )
        chunks.append(chunk)
    encoding = response.encoding or response.apparent_encoding or "utf-8"
    return b"".join(chunks).decode(encoding, errors="replace")


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

def analyze_webpage(url: str) -> dict:
    """
    Download ``url`` and decompose it into the WP = (L, S, Sc) tuple
    defined in Section 1.3.4 of the thesis.

    Each hop (initial URL + every redirect) is passed through
    :func:`_validate_url_safety` so a hostile target cannot redirect us
    onto an internal address.

    Returns
    -------
    dict
        ``{
            "count_links":   int,
            "count_styles":  int,
            "count_scripts": int,
            "raw_metadata":  {...structured chart data...},
        }``

    Raises
    ------
    UnsafeURLError
        Initial URL or any redirect hop fails the SSRF safety check, or
        the response body exceeds :data:`MAX_RESPONSE_BYTES`.
    requests.RequestException
        On network failure or a non-2xx HTTP response. Celery retries
        these transparently.
    """
    pinned_ip = _validate_url_safety(url)
    pinned_host = urlparse(url).hostname

    session = requests.Session()
    current_url = url

    # Manual redirect handling: requests' built-in follower would
    # short-circuit our SSRF re-validation between hops.
    for _ in range(MAX_REDIRECTS + 1):
        # Pin DNS for the current hop's hostname to the IP we just
        # validated. Defeats DNS-rebinding TOCTOU (audit C-C).
        with _pin_dns(pinned_host, pinned_ip):
            response = session.get(
                current_url,
                headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT_SEC,
                allow_redirects=False,
                stream=True,
            )
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                response.close()
                if not location:
                    break
                current_url = urljoin(current_url, location)
                pinned_ip = _validate_url_safety(current_url)
                pinned_host = urlparse(current_url).hostname
                continue

            response.raise_for_status()
            html = _read_capped_text(response, MAX_RESPONSE_BYTES)
        return _analyze_html(html=html, base_url=current_url)

    raise UnsafeURLError(f"Too many redirects (max {MAX_REDIRECTS}).")


def analyze_html_content(html: str | bytes, source_label: str) -> dict:
    """
    Decompose an already-loaded HTML payload (FR-03 — direct file upload).

    Parameters
    ----------
    html : str | bytes
        Raw HTML content to parse. Bytes are decoded as UTF-8 (errors
        replaced) since BeautifulSoup also accepts bytes but we want a
        deterministic decoding policy.
    source_label : str
        Human-readable identifier for the source (typically the original
        filename, e.g. ``"index.html"``). Stored in the metadata as
        ``base_host`` so the dashboard / exports can label internal vs.
        external resources sensibly. Resources that resolve to this label
        when no scheme/host is present are treated as *internal*.

    Returns
    -------
    dict
        Same shape as :func:`analyze_webpage`. No network I/O is performed.
    """
    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="replace")

    # The fake base URL is only used so urljoin() can resolve relative paths
    # to *something*. Anything resolving back to this synthetic host counts
    # as internal — anything with a real scheme/host counts as external.
    pseudo_base = f"file://{source_label}/"
    return _analyze_html(html=html, base_url=pseudo_base)


def _analyze_html(*, html: str, base_url: str) -> dict:
    """Shared parsing path used by both the URL and FILE variants."""
    soup = BeautifulSoup(html, "html.parser")

    links = {
        a.get("href")
        for a in soup.find_all("a")
        if a.get("href") and not a.get("href").startswith(("#", "javascript:", "mailto:", "tel:"))
    }
    styles = {
        link.get("href")
        for link in soup.find_all("link", rel="stylesheet")
        if link.get("href")
    }
    scripts = {
        script.get("src")
        for script in soup.find_all("script")
        if script.get("src")
    }

    return {
        "count_links": len(links),
        "count_styles": len(styles),
        "count_scripts": len(scripts),
        "raw_metadata": _build_metadata(
            base_url=base_url,
            links=links,
            styles=styles,
            scripts=scripts,
        ),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_metadata(
    *,
    base_url: str,
    links: Iterable[str],
    styles: Iterable[str],
    scripts: Iterable[str],
) -> dict:
    """
    Produce a chart-friendly breakdown of each resource category.

    The frontend (Stage 4) consumes this directly; keep the shape stable.
    """
    base_host = urlparse(base_url).netloc

    return {
        "analyzed_url": base_url,
        "base_host": base_host,
        "links": _categorize(base_url, base_host, links),
        "styles": _categorize(base_url, base_host, styles),
        "scripts": _categorize(base_url, base_host, scripts),
    }


def _categorize(base_url: str, base_host: str, urls: Iterable[str]) -> dict:
    """
    Split a set of URLs into internal / external and list the most-used
    external hosts. Handy for donut charts like "which CDNs this page
    depends on".
    """
    internal = 0
    external = 0
    host_counter: Counter[str] = Counter()

    for raw in urls:
        absolute = urljoin(base_url, raw)
        host = urlparse(absolute).netloc
        if not host or host == base_host:
            internal += 1
        else:
            external += 1
            host_counter[host] += 1

    return {
        "total": internal + external,
        "internal": internal,
        "external": external,
        "top_external_hosts": [
            {"host": h, "count": c} for h, c in host_counter.most_common(10)
        ],
    }
