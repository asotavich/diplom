"""
analyzer/plantuml.py

PlantUML source generator for an :class:`AnalysisReport` (FR-06).

Given a successfully scanned report, ``build_plantuml(report)`` returns a
self-contained ``@startuml … @enduml`` document that visualises the
*architectural dependency graph* of the analysed page:

* the **Page** itself (entry point — the URL, or the uploaded filename),
* a node per **external** stylesheet / script, grouped by host,
* a count summary for the **internal** resources of each category.

The result is plain text so the React frontend can render it inside a
``<pre>`` code block with a "Copy" button (FR-06). Users can paste it
straight into the PlantUML web renderer or any IDE plug-in to obtain a
diagram for their thesis or report.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import AnalysisReport


_ALIAS_SAFE = re.compile(r"[^A-Za-z0-9]+")


def _alias(prefix: str, value: str, used: set[str]) -> str:
    """
    Build a PlantUML-safe component alias from an arbitrary string.

    PlantUML aliases must be a single token — letters, digits, underscores —
    and must be unique within a diagram, so we sanitise + de-duplicate.
    """
    base = f"{prefix}_{_ALIAS_SAFE.sub('_', value).strip('_') or 'node'}"
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}_{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _short(value: str, max_len: int = 48) -> str:
    """Trim long URLs / hostnames so the diagram stays readable."""
    if len(value) <= max_len:
        return value
    return value[: max_len - 1] + "…"


def build_plantuml(report: "AnalysisReport") -> str:
    """
    Produce a PlantUML component diagram for ``report``.

    Returns
    -------
    str
        Multi-line PlantUML source, beginning with ``@startuml`` and
        ending with ``@enduml``. Always returns a syntactically valid
        diagram, even when ``raw_metadata`` is empty (a placeholder
        node is emitted instead).
    """
    meta = report.raw_metadata or {}
    page_label = (
        meta.get("analyzed_url")
        or report.url
        or (
            f"file: {report.uploaded_file.name.split('/')[-1]}"
            if getattr(report, "uploaded_file", None)
            else f"Report #{report.pk}"
        )
    )
    base_host = meta.get("base_host") or "(local)"

    used: set[str] = set()
    page_alias = _alias("page", base_host, used)

    lines: list[str] = []
    lines.append("@startuml")
    lines.append("title Architectural Dependency Graph")
    lines.append("skinparam componentStyle rectangle")
    lines.append("skinparam shadowing false")
    lines.append("skinparam wrapWidth 220")
    lines.append("")
    lines.append(f'component "{_escape(_short(page_label))}" as {page_alias} <<page>>')
    lines.append("")

    # ---- Internal counts (one summary node per category) -------------------
    for category, label in (
        ("links", "Internal Links"),
        ("styles", "Internal Stylesheets"),
        ("scripts", "Internal Scripts"),
    ):
        info = meta.get(category, {}) or {}
        count = int(info.get("internal", 0) or 0)
        if count == 0:
            continue
        alias = _alias(f"int_{category}", label, used)
        lines.append(
            f'component "{label}\\n×{count}" as {alias} <<{category}>>'
        )
        lines.append(f"{page_alias} --> {alias}")

    # ---- External resources (one node per host, per category) --------------
    for category, header, stereotype in (
        ("styles", "External CSS", "css_external"),
        ("scripts", "External JS", "js_external"),
        ("links", "External Links", "link_external"),
    ):
        hosts = (meta.get(category, {}) or {}).get("top_external_hosts", []) or []
        if not hosts:
            continue
        package_alias = _alias("pkg", header, used)
        host_aliases: list[str] = []
        lines.append("")
        lines.append(f'package "{header}" as {package_alias} {{')
        for entry in hosts:
            host = str(entry.get("host", "")).strip() or "(unknown)"
            count = int(entry.get("count", 0) or 0)
            host_alias = _alias(f"{category}_host", host, used)
            host_aliases.append(host_alias)
            lines.append(
                f'  component "{_escape(_short(host))}\\n×{count}" '
                f"as {host_alias} <<{stereotype}>>"
            )
        lines.append("}")
        for host_alias in host_aliases:
            lines.append(f"{page_alias} ..> {host_alias}")

    lines.append("")
    lines.append("legend right")
    lines.append(f"  Complexity Index C = {report.complexity_index or 0}")
    lines.append(
        f"  L={report.count_links}  S={report.count_styles}  Sc={report.count_scripts}"
    )
    lines.append("endlegend")
    lines.append("@enduml")

    return "\n".join(lines)


def _escape(value: str) -> str:
    """Escape characters that would close a PlantUML string literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')
