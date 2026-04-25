import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import Loading from "../components/Loading.jsx";
import ComplexityTrendChart from "../components/charts/ComplexityTrendChart.jsx";
import { listReports } from "../api/reports";
import { reportDisplayName } from "../lib/reportName";

const STATUS_STYLES = {
  PENDING: "bg-amber-100 text-amber-800",
  RUNNING: "bg-blue-100 text-blue-800",
  SUCCESS: "bg-emerald-100 text-emerald-800",
  FAILED: "bg-red-100 text-red-800",
};

function StatusBadge({ status }) {
  return (
    <span
      className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
        STATUS_STYLES[status] || "bg-slate-100 text-slate-800"
      }`}
    >
      {status}
    </span>
  );
}

export default function DashboardPage() {
  const [reports, setReports] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    listReports()
      .then((data) => {
        if (!cancelled) setReports(data.results || []);
      })
      .catch((err) => {
        if (!cancelled) setError(err.response?.data?.detail || "Failed to load reports.");
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const successfulReports = useMemo(
    () => reports.filter((r) => r.status === "SUCCESS"),
    [reports]
  );

  const stats = useMemo(() => {
    if (successfulReports.length === 0) {
      return { total: reports.length, success: 0, avgComplexity: null };
    }
    const sum = successfulReports.reduce(
      (acc, r) => acc + Number(r.complexity_index || 0),
      0
    );
    return {
      total: reports.length,
      success: successfulReports.length,
      avgComplexity: (sum / successfulReports.length).toFixed(2),
    };
  }, [reports, successfulReports]);

  if (isLoading) return <Loading label="Loading dashboard..." />;

  return (
    <div className="space-y-8">
      <section className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Dashboard</h1>
          <p className="text-sm text-slate-500">Your recent analyses and complexity trend.</p>
        </div>
        <Link
          to="/analyze"
          className="inline-flex items-center gap-2 rounded bg-brand-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-brand-700"
        >
          <PlusIcon />
          New analysis
        </Link>
      </section>

      {error && (
        <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* --- Summary cards --------------------------------------------- */}
      <section className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <SummaryCard label="Total scans" value={stats.total} />
        <SummaryCard label="Successful" value={stats.success} />
        <SummaryCard
          label="Avg complexity"
          value={stats.avgComplexity ?? "—"}
          hint="C = Σ Wᵢ · Nᵢ"
        />
      </section>

      {/* --- Trend chart ----------------------------------------------- */}
      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="text-sm font-medium text-slate-700">Complexity over time</h2>
        <div className="mt-4">
          {successfulReports.length >= 2 ? (
            <ComplexityTrendChart reports={successfulReports} />
          ) : (
            <EmptyState message="Run at least two analyses to see a trend." />
          )}
        </div>
      </section>

      {/* --- History table --------------------------------------------- */}
      <section className="rounded-xl border border-slate-200 bg-white shadow-sm">
        <header className="border-b border-slate-200 px-6 py-4">
          <h2 className="text-sm font-medium text-slate-700">Scan history</h2>
        </header>
        {reports.length === 0 ? (
          <div className="flex flex-col items-center gap-4 p-10">
            <EmptyState message="No analyses yet — submit a URL to get started." />
            <Link
              to="/analyze"
              className="inline-flex items-center gap-2 rounded bg-brand-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-brand-700"
            >
              <PlusIcon />
              Start a new analysis
            </Link>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-6 py-3">Target</th>
                  <th className="px-6 py-3">Status</th>
                  <th className="px-6 py-3">L / S / Sc</th>
                  <th className="px-6 py-3">Complexity</th>
                  <th className="px-6 py-3">Scanned</th>
                  <th className="px-6 py-3" />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200">
                {reports.map((r) => (
                  <tr key={r.id} className="hover:bg-slate-50">
                    <td className="px-6 py-3">
                      <span
                        className="block max-w-xs truncate font-medium text-slate-800"
                        title={r.url || r.uploaded_file_name || ""}
                      >
                        {reportDisplayName(r)}
                      </span>
                    </td>
                    <td className="px-6 py-3">
                      <StatusBadge status={r.status} />
                    </td>
                    <td className="px-6 py-3 text-slate-600">
                      {r.count_links} / {r.count_styles} / {r.count_scripts}
                    </td>
                    <td className="px-6 py-3 font-mono text-slate-700">
                      {r.complexity_index ?? "—"}
                    </td>
                    <td className="px-6 py-3 text-slate-500">
                      {new Date(r.scanned_at).toLocaleString()}
                    </td>
                    <td className="px-6 py-3 text-right">
                      <Link
                        to={`/reports/${r.slug}`}
                        className="text-sm font-medium text-brand-600 hover:text-brand-700"
                      >
                        View →
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function SummaryCard({ label, value, hint }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-2 text-2xl font-semibold text-slate-900">{value}</div>
      {hint && <div className="mt-1 text-xs text-slate-400">{hint}</div>}
    </div>
  );
}

function EmptyState({ message }) {
  return (
    <p className="py-8 text-center text-sm text-slate-500">{message}</p>
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
