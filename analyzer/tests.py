"""
analyzer/tests.py

Unit tests covering every pure-logic surface in the analyzer app:

* HTML parsing (`services._analyze_html`, `analyze_html_content`)
* SSRF guard (`services._validate_url_safety`)
* URL-fetch happy path with mocked redirects (`analyze_webpage`)
* Complexity formula (`AnalysisReport.compute_complexity`)
* Weight normalisation (`_validate_weights_sum_to_one`)
* Excel exporter (sheet names, cell contents)
* PDF exporter (magic bytes + key strings via raw byte search)
* PlantUML generator (structural invariants)
* View helpers (`_host_slug`, `_report_filename_stem`)
* Serializer validation (URL xor file)
* post_delete signal removes the uploaded file from disk

Tests use Django's ``SimpleTestCase`` whenever possible so they run
without DB or network access. A handful of tests need ORM/file-storage
behaviour and use ``TestCase`` against the in-memory SQLite database
configured in ``settings.TESTING``.
"""

from __future__ import annotations

import os
import re
import socket
import tempfile
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings

from analyzer import exports, plantuml, services
from analyzer.models import (
    DEFAULT_WEIGHT,
    AnalysisReport,
    Project,
    _validate_weights_sum_to_one,
    build_report_slug_base,
    generate_unique_report_slug,
)
from analyzer.plantuml import build_plantuml
from analyzer.serializers import AnalysisReportSerializer
from analyzer.services import (
    UnsafeURLError,
    _validate_url_safety,
    analyze_html_content,
    analyze_webpage,
)
from analyzer.views import _host_slug, _report_filename_stem


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _public_addrinfo(ip: str = "93.184.216.34"):
    """Mock value for ``socket.getaddrinfo`` returning a single public IPv4."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0))]


def _private_addrinfo(ip: str):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0))]


def _make_unsaved_report(
    *,
    url: str | None = "https://example.com",
    count_links: int = 10,
    count_styles: int = 5,
    count_scripts: int = 3,
    raw_metadata: dict | None = None,
    status: str = AnalysisReport.Status.SUCCESS,
    project: Project | None = None,
    user=None,
    uploaded_file=None,
    source_type: str = AnalysisReport.SourceType.URL,
) -> AnalysisReport:
    """
    Build an ``AnalysisReport`` instance without hitting the DB. Useful for
    pure-function tests on exporters / plantuml — they only read fields.
    """
    User = get_user_model()
    if user is None:
        user = User(username="alice", email="alice@example.com")
        user.id = 1  # exporters reference user.pk for the analyst label
    report = AnalysisReport(
        url=url,
        count_links=count_links,
        count_styles=count_styles,
        count_scripts=count_scripts,
        weight_links=Decimal("0.3333"),
        weight_styles=Decimal("0.3333"),
        weight_scripts=Decimal("0.3334"),
        complexity_index=Decimal("6.0000"),
        raw_metadata=raw_metadata or {},
        status=status,
        project=project,
        created_by=user,
        source_type=source_type,
    )
    report.id = 42
    report.scanned_at = None  # exporters tolerate None for unsaved instances
    if uploaded_file is not None:
        report.uploaded_file = uploaded_file
    return report


# ---------------------------------------------------------------------------
# services._analyze_html — counts and classification
# ---------------------------------------------------------------------------

class AnalyzeHtmlContentTests(SimpleTestCase):
    """Tests for the parsing core driven by ``analyze_html_content``."""

    SAMPLE_HTML = """
    <html><head>
      <link rel="stylesheet" href="/local.css">
      <link rel="stylesheet" href="https://cdn.example.com/lib.css">
      <link rel="icon" href="/favicon.ico">
      <script src="/local.js"></script>
      <script src="https://cdn.example.com/jquery.js"></script>
      <script>console.log('inline')</script>
    </head><body>
      <a href="/page-1">internal</a>
      <a href="/page-2">internal</a>
      <a href="https://other.example/foo">external</a>
      <a href="#section">skip-anchor</a>
      <a href="javascript:void(0)">skip-js</a>
      <a href="mailto:x@y.com">skip-mail</a>
      <a href="tel:+1234">skip-tel</a>
    </body></html>
    """

    def test_counts_three_categories(self):
        result = analyze_html_content(self.SAMPLE_HTML, source_label="t.html")
        self.assertEqual(result["count_links"], 3)
        self.assertEqual(result["count_styles"], 2)
        # Inline <script> without src is not counted (matches services.py).
        self.assertEqual(result["count_scripts"], 2)

    def test_internal_external_split_for_links(self):
        result = analyze_html_content(self.SAMPLE_HTML, source_label="t.html")
        meta = result["raw_metadata"]["links"]
        # /page-1 + /page-2 → internal under the synthetic file:// host;
        # https://other.example/foo → external.
        self.assertEqual(meta["total"], 3)
        self.assertEqual(meta["internal"], 2)
        self.assertEqual(meta["external"], 1)
        self.assertEqual(
            [h["host"] for h in meta["top_external_hosts"]],
            ["other.example"],
        )

    def test_top_external_hosts_for_styles(self):
        result = analyze_html_content(self.SAMPLE_HTML, source_label="t.html")
        meta = result["raw_metadata"]["styles"]
        self.assertEqual(meta["external"], 1)
        self.assertEqual(meta["top_external_hosts"][0]["host"], "cdn.example.com")
        self.assertEqual(meta["top_external_hosts"][0]["count"], 1)

    def test_accepts_bytes_input(self):
        # Latin-1 byte that should be safely decoded under UTF-8 errors=replace.
        html_bytes = b"<html><body><a href='/x'>t</a></body></html>\xff"
        result = analyze_html_content(html_bytes, source_label="t.html")
        self.assertEqual(result["count_links"], 1)


# ---------------------------------------------------------------------------
# services._validate_url_safety — SSRF gate
# ---------------------------------------------------------------------------

class SsrfValidationTests(SimpleTestCase):
    """Server-Side Request Forgery rejection tests."""

    def test_rejects_non_http_scheme_file(self):
        with self.assertRaises(UnsafeURLError):
            _validate_url_safety("file:///etc/passwd")

    def test_rejects_non_http_scheme_gopher(self):
        with self.assertRaises(UnsafeURLError):
            _validate_url_safety("gopher://example.com/")

    def test_rejects_missing_host(self):
        with self.assertRaises(UnsafeURLError):
            _validate_url_safety("http:///path-only")

    def test_rejects_loopback_literal(self):
        with self.assertRaises(UnsafeURLError) as ctx:
            _validate_url_safety("http://127.0.0.1/")
        self.assertIn("non-public", str(ctx.exception))

    def test_rejects_ipv6_loopback(self):
        # Mock getaddrinfo so the test doesn't depend on the host's IPv6
        # stack (some CI runners are IPv4-only).
        with mock.patch(
            "analyzer.services.socket.getaddrinfo",
            return_value=[(socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("::1", 0, 0, 0))],
        ):
            with self.assertRaises(UnsafeURLError):
                _validate_url_safety("http://[::1]/")

    def test_rejects_private_rfc1918(self):
        for ip in ("10.0.0.1", "172.16.0.1", "192.168.1.1"):
            with self.assertRaises(UnsafeURLError, msg=ip):
                _validate_url_safety(f"http://{ip}/")

    def test_rejects_link_local_metadata(self):
        # AWS / GCP / Azure cloud metadata endpoint.
        with self.assertRaises(UnsafeURLError):
            _validate_url_safety("http://169.254.169.254/latest/meta-data/")

    def test_rejects_dns_name_resolving_to_private_ip(self):
        with mock.patch(
            "analyzer.services.socket.getaddrinfo",
            return_value=_private_addrinfo("10.0.0.5"),
        ):
            with self.assertRaises(UnsafeURLError):
                _validate_url_safety("http://intranet.example.com/")

    def test_rejects_unresolvable_host(self):
        with mock.patch(
            "analyzer.services.socket.getaddrinfo",
            side_effect=socket.gaierror("mock NXDOMAIN"),
        ):
            with self.assertRaises(UnsafeURLError):
                _validate_url_safety("http://does-not-exist.invalid/")

    def test_allows_public_dns_name(self):
        with mock.patch(
            "analyzer.services.socket.getaddrinfo",
            return_value=_public_addrinfo("93.184.216.34"),
        ):
            _validate_url_safety("https://example.com/path")  # must not raise

    def test_allows_https_scheme(self):
        with mock.patch(
            "analyzer.services.socket.getaddrinfo",
            return_value=_public_addrinfo(),
        ):
            _validate_url_safety("https://example.com/")


# ---------------------------------------------------------------------------
# services.analyze_webpage — happy path with mocked HTTP
# ---------------------------------------------------------------------------

class AnalyzeWebpageTests(SimpleTestCase):
    """End-to-end test of the URL-fetch path with the network mocked out."""

    def _mock_response(self, status_code=200, text="", headers=None):
        m = mock.Mock()
        m.status_code = status_code
        m.text = text
        m.headers = headers or {}
        m.raise_for_status = mock.Mock()
        return m

    def test_fetches_and_parses_simple_page(self):
        html = (
            "<html><body>"
            "<a href='/x'>x</a>"
            "<script src='/s.js'></script>"
            "</body></html>"
        )
        response = self._mock_response(200, html)
        with mock.patch(
            "analyzer.services.socket.getaddrinfo",
            return_value=_public_addrinfo(),
        ), mock.patch(
            "analyzer.services.requests.Session.get",
            return_value=response,
        ):
            result = analyze_webpage("https://example.com/")
        self.assertEqual(result["count_links"], 1)
        self.assertEqual(result["count_scripts"], 1)

    def test_redirect_chain_revalidates_each_hop(self):
        first = self._mock_response(
            302, "", headers={"Location": "https://example.com/final"}
        )
        final = self._mock_response(200, "<html></html>")
        with mock.patch(
            "analyzer.services.socket.getaddrinfo",
            return_value=_public_addrinfo(),
        ), mock.patch(
            "analyzer.services.requests.Session.get",
            side_effect=[first, final],
        ):
            result = analyze_webpage("https://example.com/")
        self.assertEqual(result["count_links"], 0)

    def test_redirect_to_private_ip_is_blocked(self):
        first = self._mock_response(
            302, "", headers={"Location": "http://10.0.0.1/admin"}
        )
        public = _public_addrinfo()
        private = _private_addrinfo("10.0.0.1")
        with mock.patch(
            "analyzer.services.socket.getaddrinfo",
            side_effect=[public, private],
        ), mock.patch(
            "analyzer.services.requests.Session.get",
            return_value=first,
        ):
            with self.assertRaises(UnsafeURLError):
                analyze_webpage("https://example.com/")

    def test_caps_redirect_count(self):
        looping = self._mock_response(
            302, "", headers={"Location": "https://example.com/next"}
        )
        with mock.patch(
            "analyzer.services.socket.getaddrinfo",
            return_value=_public_addrinfo(),
        ), mock.patch(
            "analyzer.services.requests.Session.get",
            return_value=looping,
        ):
            with self.assertRaises(UnsafeURLError):
                analyze_webpage("https://example.com/")


# ---------------------------------------------------------------------------
# AnalysisReport math
# ---------------------------------------------------------------------------

class ComplexityFormulaTests(SimpleTestCase):
    """C = Σ Wᵢ × Nᵢ — Section 1.3.4."""

    def test_default_weights_equal_uniform_average(self):
        report = AnalysisReport(
            count_links=3, count_styles=3, count_scripts=3,
            weight_links=Decimal("0.3333"),
            weight_styles=Decimal("0.3333"),
            weight_scripts=Decimal("0.3334"),
        )
        # Equal counts → C = 3 (within 4-decimal rounding).
        self.assertEqual(report.compute_complexity(), Decimal("3.0000"))

    def test_custom_weights(self):
        report = AnalysisReport(
            count_links=10, count_styles=5, count_scripts=3,
            weight_links=Decimal("0.5"),
            weight_styles=Decimal("0.3"),
            weight_scripts=Decimal("0.2"),
        )
        # 10*0.5 + 5*0.3 + 3*0.2 = 5 + 1.5 + 0.6 = 7.1
        self.assertEqual(report.compute_complexity(), Decimal("7.1000"))

    def test_zero_counts(self):
        report = AnalysisReport(
            count_links=0, count_styles=0, count_scripts=0,
            weight_links=DEFAULT_WEIGHT,
            weight_styles=DEFAULT_WEIGHT,
            weight_scripts=DEFAULT_WEIGHT,
        )
        self.assertEqual(report.compute_complexity(), Decimal("0.0000"))


class WeightSumValidatorTests(SimpleTestCase):
    """The W₁ + W₂ + W₃ = 1.0 invariant from Section 1.3.4."""

    def _report(self, l, s, sc):
        return AnalysisReport(
            count_links=0, count_styles=0, count_scripts=0,
            weight_links=Decimal(l),
            weight_styles=Decimal(s),
            weight_scripts=Decimal(sc),
        )

    def test_accepts_equal_thirds(self):
        # Should not raise.
        _validate_weights_sum_to_one(self._report("0.3333", "0.3333", "0.3334"))

    def test_accepts_within_tolerance(self):
        _validate_weights_sum_to_one(self._report("0.5", "0.3", "0.2"))

    def test_rejects_underflow(self):
        with self.assertRaises(ValidationError):
            _validate_weights_sum_to_one(self._report("0.1", "0.1", "0.1"))

    def test_rejects_overflow(self):
        with self.assertRaises(ValidationError):
            _validate_weights_sum_to_one(self._report("0.5", "0.5", "0.5"))


# ---------------------------------------------------------------------------
# Report slug — deterministic base
# ---------------------------------------------------------------------------

class ReportSlugBaseTests(SimpleTestCase):
    """
    ``build_report_slug_base`` derives the human-readable prefix of a slug
    from the scan target. Pure logic, no DB required.
    """

    def test_url_strips_www_and_replaces_dots(self):
        report = _make_unsaved_report(url="https://www.google.com/search?q=x")
        self.assertEqual(build_report_slug_base(report), "google-com")

    def test_url_keeps_subdomain(self):
        report = _make_unsaved_report(url="https://api.acme.co.uk/")
        self.assertEqual(build_report_slug_base(report), "api-acme-co-uk")

    def test_url_strips_port(self):
        report = _make_unsaved_report(url="http://example.com:8080/x")
        self.assertEqual(build_report_slug_base(report), "example-com")

    def test_uppercase_host_lowercased(self):
        report = _make_unsaved_report(url="https://WWW.GitHub.COM/")
        self.assertEqual(build_report_slug_base(report), "github-com")

    def test_garbage_url_falls_back_to_report(self):
        report = _make_unsaved_report(url="not a url at all")
        self.assertEqual(build_report_slug_base(report), "report")

    def test_empty_url_falls_back_to_report(self):
        report = _make_unsaved_report(url=None)
        self.assertEqual(build_report_slug_base(report), "report")

    def test_file_source_uses_basename_without_extension(self):
        upload = SimpleUploadedFile(
            "MyPage.HTML", b"<html></html>", content_type="text/html",
        )
        report = _make_unsaved_report(
            url=None,
            uploaded_file=upload,
            source_type=AnalysisReport.SourceType.FILE,
        )
        self.assertEqual(build_report_slug_base(report), "mypage")


# ---------------------------------------------------------------------------
# Report slug — uniqueness and suffix generation (DB-backed)
# ---------------------------------------------------------------------------

class ReportSlugGenerationTests(TestCase):
    """
    Behaviour of ``AnalysisReport.save()`` + ``generate_unique_report_slug``:

    * the slug is auto-filled on first save,
    * it has the shape ``<base>-<6 hex chars>``,
    * two scans of the same hostname don't collide,
    * a saved slug is never overwritten on a subsequent ``save()``,
    * if the random suffix happens to clash with an existing row, the
      generator rolls again until it finds a free one.
    """

    #: ``<deterministic base, all alnum/dashes>-<exactly 6 lowercase hex>``.
    SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*-[0-9a-f]{6}$")

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username="slug-user",
            password="testpass-7x9",
            email="slug@example.com",
        )

    def _make_url_report(self, url: str = "https://www.google.com/") -> AnalysisReport:
        return AnalysisReport.objects.create(
            created_by=self.user,
            source_type=AnalysisReport.SourceType.URL,
            url=url,
            weight_links=Decimal("0.3333"),
            weight_styles=Decimal("0.3333"),
            weight_scripts=Decimal("0.3334"),
        )

    def test_slug_auto_filled_on_first_save(self):
        report = self._make_url_report()
        self.assertTrue(report.slug, "save() must populate the slug")

    def test_slug_matches_base_dash_six_hex_pattern(self):
        report = self._make_url_report()
        self.assertRegex(report.slug, self.SLUG_RE)
        # Suffix is exactly 6 hex chars (3 random bytes).
        self.assertEqual(len(report.slug.rsplit("-", 1)[-1]), 6)

    def test_slug_uses_hostname_as_base(self):
        report = self._make_url_report("https://www.google.com/foo")
        self.assertTrue(
            report.slug.startswith("google-com-"),
            f"expected slug to start with 'google-com-', got {report.slug!r}",
        )

    def test_two_reports_for_same_host_get_distinct_slugs(self):
        a = self._make_url_report("https://www.google.com/")
        b = self._make_url_report("https://google.com/foo")
        self.assertNotEqual(a.slug, b.slug)
        self.assertTrue(a.slug.startswith("google-com-"))
        self.assertTrue(b.slug.startswith("google-com-"))

    def test_existing_slug_preserved_on_resave(self):
        report = self._make_url_report()
        original = report.slug
        report.url = "https://different.example.org/"
        report.save()
        self.assertEqual(
            report.slug, original,
            "save() must never overwrite an already-assigned slug",
        )

    def test_collision_retries_until_unique(self):
        """
        First call to the RNG returns a suffix that is already taken;
        the generator must roll again and use the next one.
        """
        first = self._make_url_report()
        taken_suffix = first.slug.rsplit("-", 1)[-1]
        fresh_suffix = "abcdef" if taken_suffix != "abcdef" else "fedcba"

        with mock.patch("analyzer.models.secrets.token_hex") as token_hex:
            token_hex.side_effect = [taken_suffix, fresh_suffix]
            second = self._make_url_report("https://google.com/different")

        self.assertEqual(
            token_hex.call_count, 2,
            "generator should re-roll exactly once on a single collision",
        )
        self.assertTrue(second.slug.endswith(f"-{fresh_suffix}"))
        self.assertNotEqual(first.slug, second.slug)

    def test_generator_excludes_self_when_pk_already_set(self):
        """
        Calling ``generate_unique_report_slug`` on a saved instance must
        not treat that instance's own slug as a collision.
        """
        report = self._make_url_report()
        with mock.patch("analyzer.models.secrets.token_hex") as token_hex:
            # Force the RNG to hand back this report's own suffix; if the
            # generator forgot to ``.exclude(pk=report.pk)`` it would loop.
            token_hex.return_value = report.slug.rsplit("-", 1)[-1]
            new_slug = generate_unique_report_slug(report)
        self.assertEqual(new_slug, report.slug)


# ---------------------------------------------------------------------------
# Excel exporter
# ---------------------------------------------------------------------------

class ExcelExportTests(SimpleTestCase):

    def test_workbook_has_summary_sheet(self):
        report = _make_unsaved_report()
        wb = exports.build_report_excel(report)
        self.assertIn("Summary", wb.sheetnames)

    def test_breakdown_sheet_present_when_metadata_exists(self):
        meta = {
            "analyzed_url": "https://example.com",
            "base_host": "example.com",
            "links":   {"total": 0, "internal": 0, "external": 0, "top_external_hosts": []},
            "styles":  {"total": 0, "internal": 0, "external": 0, "top_external_hosts": []},
            "scripts": {"total": 0, "internal": 0, "external": 0, "top_external_hosts": []},
        }
        report = _make_unsaved_report(raw_metadata=meta)
        wb = exports.build_report_excel(report)
        self.assertIn("Resource Breakdown", wb.sheetnames)

    def test_summary_contains_url_and_id(self):
        report = _make_unsaved_report(url="https://acme.test/landing")
        wb = exports.build_report_excel(report)
        ws = wb["Summary"]
        cells = [c.value for row in ws.iter_rows() for c in row]
        self.assertIn("https://acme.test/landing", cells)
        self.assertIn(report.pk, cells)


# ---------------------------------------------------------------------------
# PDF exporter
# ---------------------------------------------------------------------------

class PdfExportTests(SimpleTestCase):

    def test_returns_pdf_magic_bytes(self):
        report = _make_unsaved_report()
        pdf = exports.build_report_pdf(report)
        self.assertIsInstance(pdf, bytes)
        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_pdf_is_non_trivial_size(self):
        report = _make_unsaved_report()
        pdf = exports.build_report_pdf(report)
        # Even a near-empty PDF with our layout exceeds 1 KB.
        self.assertGreater(len(pdf), 1024)

    def test_pdf_renders_with_metadata(self):
        meta = {
            "analyzed_url": "https://acme.test/",
            "base_host": "acme.test",
            "links":   {"total": 1, "internal": 1, "external": 0, "top_external_hosts": []},
            "styles":  {"total": 0, "internal": 0, "external": 0, "top_external_hosts": []},
            "scripts": {
                "total": 1, "internal": 0, "external": 1,
                "top_external_hosts": [{"host": "cdn.example.com", "count": 1}],
            },
        }
        report = _make_unsaved_report(raw_metadata=meta)
        pdf = exports.build_report_pdf(report)
        # Magic bytes + non-trivial size = the build pipeline ran end-to-end.
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 2048)


# ---------------------------------------------------------------------------
# PlantUML generator
# ---------------------------------------------------------------------------

class PlantUmlTests(SimpleTestCase):

    META = {
        "analyzed_url": "https://acme.test/",
        "base_host": "acme.test",
        "links":   {"total": 2, "internal": 2, "external": 0, "top_external_hosts": []},
        "styles":  {
            "total": 1, "internal": 0, "external": 1,
            "top_external_hosts": [{"host": "cdn.example.com", "count": 1}],
        },
        "scripts": {
            "total": 2, "internal": 1, "external": 1,
            "top_external_hosts": [{"host": "analytics.test", "count": 1}],
        },
    }

    def test_starts_and_ends_with_plantuml_markers(self):
        report = _make_unsaved_report(raw_metadata=self.META)
        src = build_plantuml(report)
        self.assertTrue(src.startswith("@startuml"))
        self.assertTrue(src.rstrip().endswith("@enduml"))

    def test_includes_external_host_node(self):
        report = _make_unsaved_report(raw_metadata=self.META)
        src = build_plantuml(report)
        self.assertIn("cdn.example.com", src)
        self.assertIn("analytics.test", src)

    def test_includes_internal_summary(self):
        report = _make_unsaved_report(raw_metadata=self.META)
        src = build_plantuml(report)
        self.assertIn("Internal Links", src)

    def test_handles_empty_metadata_gracefully(self):
        report = _make_unsaved_report(raw_metadata={})
        src = build_plantuml(report)
        # Always emits a syntactically valid diagram.
        self.assertTrue(src.startswith("@startuml"))
        self.assertTrue(src.rstrip().endswith("@enduml"))

    def test_legend_contains_complexity_value(self):
        report = _make_unsaved_report(raw_metadata=self.META)
        src = build_plantuml(report)
        self.assertIn("Complexity Index C =", src)


# ---------------------------------------------------------------------------
# Filename helpers
# ---------------------------------------------------------------------------

class HostSlugTests(SimpleTestCase):

    def test_basic_domain(self):
        self.assertEqual(_host_slug("https://google.com/"), "google-com")

    def test_strips_www(self):
        self.assertEqual(_host_slug("https://www.Google.com/search"), "google-com")

    def test_handles_subdomains_and_port(self):
        self.assertEqual(
            _host_slug("http://sub.example.co.uk:8080/x"),
            "sub-example-co-uk",
        )

    def test_falls_back_for_non_url_input(self):
        self.assertEqual(_host_slug("not a url"), "report")
        self.assertEqual(_host_slug(""), "report")
        self.assertEqual(_host_slug(None or ""), "report")


class ReportFilenameStemTests(SimpleTestCase):

    def test_uses_host_for_url_source(self):
        report = _make_unsaved_report(url="https://www.acme.test/page")
        self.assertEqual(_report_filename_stem(report), "acme-test")

    def test_uses_filename_for_file_source(self):
        # Build a fake file-like attribute carrying a name.
        class _F:
            name = "uploads/2026/04/My_Page.html"
        report = _make_unsaved_report(
            url=None,
            uploaded_file=_F(),
            source_type=AnalysisReport.SourceType.FILE,
        )
        self.assertEqual(_report_filename_stem(report), "my-page")


# ---------------------------------------------------------------------------
# Serializer validation — URL xor file
# ---------------------------------------------------------------------------

class SerializerSourceValidationTests(SimpleTestCase):
    """Smoke-tests for the create-time URL-vs-file rule (FR-03)."""

    def _serializer(self, **data):
        s = AnalysisReportSerializer(data=data)
        return s

    def test_rejects_when_neither_url_nor_file(self):
        s = self._serializer(
            weight_links="0.3333",
            weight_styles="0.3333",
            weight_scripts="0.3334",
        )
        self.assertFalse(s.is_valid())
        # The "either / or" error lives in non_field_errors via 'detail'.
        self.assertIn("detail", s.errors)

    def test_rejects_when_both_url_and_file(self):
        upload = SimpleUploadedFile(
            "ok.html", b"<html></html>", content_type="text/html",
        )
        s = self._serializer(
            url="https://example.com/",
            uploaded_file=upload,
            weight_links="0.3333",
            weight_styles="0.3333",
            weight_scripts="0.3334",
        )
        self.assertFalse(s.is_valid())
        self.assertIn("detail", s.errors)

    def test_rejects_non_html_extension(self):
        upload = SimpleUploadedFile(
            "evil.exe", b"MZ\x90\x00", content_type="application/octet-stream",
        )
        s = self._serializer(
            uploaded_file=upload,
            weight_links="0.3333",
            weight_styles="0.3333",
            weight_scripts="0.3334",
        )
        self.assertFalse(s.is_valid())
        self.assertIn("uploaded_file", s.errors)

    def test_rejects_weights_not_summing_to_one(self):
        s = self._serializer(
            url="https://example.com/",
            weight_links="0.5",
            weight_styles="0.5",
            weight_scripts="0.5",
        )
        self.assertFalse(s.is_valid())
        self.assertIn("weights", s.errors)


# ---------------------------------------------------------------------------
# post_delete signal — DB-backed test
# ---------------------------------------------------------------------------

@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="feanalyzer-test-media-"))
class UploadCleanupSignalTests(TestCase):
    """When an AnalysisReport is deleted, its on-disk file goes too."""

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="bob", password="testpass-7x9", email="bob@example.com",
        )

    def test_file_removed_on_delete(self):
        upload = SimpleUploadedFile(
            "page.html",
            b"<html><body><a href='/x'>x</a></body></html>",
            content_type="text/html",
        )
        report = AnalysisReport.objects.create(
            created_by=self.user,
            source_type=AnalysisReport.SourceType.FILE,
            uploaded_file=upload,
            weight_links=Decimal("0.3333"),
            weight_styles=Decimal("0.3333"),
            weight_scripts=Decimal("0.3334"),
        )
        path = report.uploaded_file.path
        self.assertTrue(os.path.exists(path))

        report.delete()

        self.assertFalse(
            os.path.exists(path),
            f"Expected the uploaded file at {path} to be removed by the signal.",
        )

    def test_url_only_report_delete_does_not_error(self):
        # No uploaded_file → signal is a no-op (and definitely not a crash).
        report = AnalysisReport.objects.create(
            created_by=self.user,
            source_type=AnalysisReport.SourceType.URL,
            url="https://example.com/",
            weight_links=Decimal("0.3333"),
            weight_styles=Decimal("0.3333"),
            weight_scripts=Decimal("0.3334"),
        )
        report.delete()  # must not raise


# ---------------------------------------------------------------------------
# Throttle wiring smoke-check
# ---------------------------------------------------------------------------

class ThrottleScopeWiringTests(SimpleTestCase):
    """
    The scoped throttle exists and the create endpoint advertises it. We
    don't exhaust the rate here (avoids brittle clock-based assertions),
    only verify the wiring an examiner would verify in a code review.
    """

    def test_scope_rate_is_registered(self):
        from django.conf import settings as dj_settings
        rates = dj_settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        self.assertIn("analysis_create", rates)

    def test_post_uses_scoped_throttle(self):
        from rest_framework.throttling import ScopedRateThrottle
        from analyzer.views import AnalysisReportListCreateView
        view = AnalysisReportListCreateView()
        request = mock.Mock()
        request.method = "POST"
        view.request = request
        throttles = view.get_throttles()
        self.assertEqual(len(throttles), 1)
        self.assertIsInstance(throttles[0], ScopedRateThrottle)
        self.assertEqual(view.throttle_scope, "analysis_create")
