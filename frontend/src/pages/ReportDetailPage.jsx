import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import Loading from "../components/Loading.jsx";
import ComponentBreakdownChart from "../components/charts/ComponentBreakdownChart.jsx";
import ExternalHostsChart from "../components/charts/ExternalHostsChart.jsx";
import InternalExternalChart from "../components/charts/InternalExternalChart.jsx";
import {
  downloadReportExport,
  downloadReportPdf,
  getReport,
} from "../api/reports";
import { reportDisplayName } from "../lib/reportName";

export default function ReportDetailPage() {
  const { slug } = useParams();
  const [report, setReport] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");
  const [downloadingFormat, setDownloadingFormat] = useState(null);
  const [downloadError, setDownloadError] = useState("");
  const [copyState, setCopyState] = useState("idle"); // idle | copied | error

  useEffect(() => {
    let cancelled = false;
    getReport(slug)
      .then((data) => {
        if (!cancelled) setReport(data);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(
            err.response?.status === 404
              ? "Report not found."
              : err.response?.data?.detail || "Failed to load the report."
          );
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [slug]);

  async function handleDownload(format) {
    setDownloadError("");
    setDownloadingFormat(format);
    try {
      if (format === "pdf") {
        await downloadReportPdf(slug);
      } else {
        await downloadReportExport(slug);
      }
    } catch (err) {
      if (err.response?.status === 409) {
        setDownloadError("Report is not ready for export yet.");
      } else {
        setDownloadError(
          err.response?.data?.detail || "Failed to download the report."
        );
      }
    } finally {
      setDownloadingFormat(null);
    }
  }

  async function handleCopyPlantuml() {
    if (!report?.plantuml_source) return;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(report.plantuml_source);
      } else {
        // Fallback for older browsers / non-secure contexts.
        const ta = document.createElement("textarea");
        ta.value = report.plantuml_source;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        ta.remove();
      }
      setCopyState("copied");
      setTimeout(() => setCopyState("idle"), 2000);
    } catch {
      setCopyState("error");
      setTimeout(() => setCopyState("idle"), 2500);
    }
  }

  if (isLoading) return <Loading label="Loading report..." />;

  if (error) {
    return (
      <div className="rounded border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
        {error}
      </div>
    );
  }

  const meta = report.raw_metadata || {};
  const pending = report.status !== "SUCCESS";

  return (
    <div className="space-y-8">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <Link to="/" className="text-sm text-slate-500 hover:text-slate-700">
              ← Back to dashboard
            </Link>
            <Link
              to="/analyze"
              className="inline-flex items-center gap-2 rounded bg-brand-600 px-3 py-1.5 text-sm font-medium text-white shadow-sm hover:bg-brand-700"
            >
              <PlusIcon />
              New analysis
            </Link>
          </div>
          <h1 className="mt-2 max-w-3xl break-all text-xl font-semibold text-slate-900">
            Report: {reportDisplayName(report)}
          </h1>
          <p className="text-sm text-slate-500">
            {report.source_type === "FILE" ? "Uploaded file" : "Public URL"}
            {" · "}
            Scanned {new Date(report.scanned_at).toLocaleString()}
          </p>
          {report.status === "SUCCESS" && (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => handleDownload("xlsx")}
                disabled={downloadingFormat !== null}
                className="inline-flex items-center gap-2 rounded bg-brand-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
              >
                <DownloadIcon />
                {downloadingFormat === "xlsx"
                  ? "Preparing Excel..."
                  : "Download Excel (.xlsx)"}
              </button>
              <button
                type="button"
                onClick={() => handleDownload("pdf")}
                disabled={downloadingFormat !== null}
                className="inline-flex items-center gap-2 rounded border border-brand-600 bg-white px-4 py-2 text-sm font-medium text-brand-700 shadow-sm transition hover:bg-brand-50 disabled:cursor-not-allowed disabled:opacity-60"
              >
                <DownloadIcon />
                {downloadingFormat === "pdf"
                  ? "Preparing PDF..."
                  : "Download PDF (.pdf)"}
              </button>
              {downloadError && (
                <p className="basis-full text-sm text-red-600">{downloadError}</p>
              )}
            </div>
          )}
        </div>
        <div className="rounded-xl border border-slate-200 bg-white p-4 text-right shadow-sm min-w-[12rem]">
          <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Complexity C
          </div>
          <div className="mt-1 font-mono text-3xl font-semibold text-brand-600">
            {report.complexity_index ?? "—"}
          </div>
          <div className="mt-1 text-xs text-slate-400">Status: {report.status}</div>
        </div>
      </header>

      {pending && (
        <div className="rounded border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          {report.status === "FAILED"
            ? `This scan failed: ${report.error_message || "unknown error."}`
            : "This scan is still running. Come back in a few seconds."}
        </div>
      )}

      {report.status === "SUCCESS" && (
        <>
          {/* --- Count summary ------------------------------------------ */}
          <section className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <CountCard
              label="Links (|L|)"
              value={report.count_links}
              weight={report.weight_links}
              accent="#3b82f6"
            />
            <CountCard
              label="Stylesheets (|S|)"
              value={report.count_styles}
              weight={report.weight_styles}
              accent="#10b981"
            />
            <CountCard
              label="Scripts (|Sc|)"
              value={report.count_scripts}
              weight={report.weight_scripts}
              accent="#f59e0b"
            />
          </section>

          {/* --- Breakdown pie ------------------------------------------ */}
          <section className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <Panel title="Component breakdown">
              <ComponentBreakdownChart report={report} />
            </Panel>
            <Panel title="Internal vs. external">
              <InternalExternalChart metadata={meta} />
            </Panel>
          </section>

          {/* --- Top external hosts ------------------------------------- */}
          <Panel title="Top external hosts">
            <ExternalHostsChart metadata={meta} />
          </Panel>

          {/* --- PlantUML diagram source (FR-06) ------------------------ */}
          {report.plantuml_source && (
            <Panel title="Architecture diagram (PlantUML source)">
              <p className="mb-3 text-sm text-slate-500">
                <button
                  type="button"
                  onClick={handleCopyPlantuml}
                  className="font-medium text-brand-600 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/40"
                  aria-live="polite"
                >
                  {copyState === "copied"
                    ? "Copied!"
                    : copyState === "error"
                      ? "Copy failed — try again"
                      : "Copy this snippet"}
                </button>{" "}
                into the{" "}
                <a
                  href="https://www.plantuml.com/plantuml"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-brand-600 hover:underline"
                >
                  PlantUML web renderer
                </a>{" "}
                or any PlantUML-aware IDE plugin to generate the dependency
                diagram for this scan.
              </p>
              <pre className="max-h-96 overflow-auto rounded-lg border border-slate-200 bg-slate-900 px-4 py-3 font-mono text-xs leading-relaxed text-slate-100">
                {report.plantuml_source}
              </pre>
            </Panel>
          )}
        </>
      )}
    </div>
  );
}

function PlusIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 20 20"
      fill="currentColor"
      className="h-4 w-4"
      aria-hidden="true"
    >
      <path d="M10 3.75a.75.75 0 0 1 .75.75v4.75h4.75a.75.75 0 0 1 0 1.5H10.75v4.75a.75.75 0 0 1-1.5 0V10.75H4.5a.75.75 0 0 1 0-1.5h4.75V4.5a.75.75 0 0 1 .75-.75Z" />
    </svg>
  );
}

function DownloadIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 20 20"
      fill="currentColor"
      className="h-4 w-4"
      aria-hidden="true"
    >
      <path d="M10.75 2.75a.75.75 0 0 0-1.5 0v8.614L6.295 8.235a.75.75 0 1 0-1.09 1.03l4.25 4.5a.75.75 0 0 0 1.09 0l4.25-4.5a.75.75 0 0 0-1.09-1.03l-2.955 3.129V2.75Z" />
      <path d="M3.5 12.75a.75.75 0 0 0-1.5 0v2.5A2.75 2.75 0 0 0 4.75 18h10.5A2.75 2.75 0 0 0 18 15.25v-2.5a.75.75 0 0 0-1.5 0v2.5c0 .69-.56 1.25-1.25 1.25H4.75c-.69 0-1.25-.56-1.25-1.25v-2.5Z" />
    </svg>
  );
}

function CountCard({ label, value, weight, accent }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-slate-500">
        <span className="h-2 w-2 rounded-full" style={{ backgroundColor: accent }} />
        {label}
      </div>
      <div className="mt-2 text-2xl font-semibold text-slate-900">{value}</div>
      <div className="mt-1 text-xs text-slate-400">Weight: {weight}</div>
    </div>
  );
}

function Panel({ title, children }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <h2 className="text-sm font-medium text-slate-700">{title}</h2>
      <div className="mt-4">{children}</div>
    </div>
  );
}
