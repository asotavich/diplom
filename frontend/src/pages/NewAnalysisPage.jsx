import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { pollTask, submitAnalysis } from "../api/reports";

const POLL_INTERVAL_MS = 2000;
const POLL_MAX_ATTEMPTS = 90; // ~3 minutes before we stop polling

const STATUS_COPY = {
  PENDING: "Queued — waiting for a worker.",
  RUNNING: "Fetching the page and computing metrics...",
  SUCCESS: "Done!",
  FAILED: "Something went wrong.",
};

export default function NewAnalysisPage() {
  const navigate = useNavigate();
  const intervalRef = useRef(null);
  const attemptsRef = useRef(0);

  const [sourceMode, setSourceMode] = useState("url"); // "url" | "file"
  const [url, setUrl] = useState("");
  const [file, setFile] = useState(null);
  const [weights, setWeights] = useState({
    links: "0.3333",
    styles: "0.3333",
    scripts: "0.3334",
  });
  const [submitting, setSubmitting] = useState(false);
  const [task, setTask] = useState(null); // { task_id, report_id, status }
  const [error, setError] = useState("");

  // Cleanup any interval on unmount.
  useEffect(() => () => clearInterval(intervalRef.current), []);

  const weightsSum = Object.values(weights)
    .map(Number)
    .reduce((a, b) => a + b, 0);
  const weightsValid = Math.abs(weightsSum - 1) < 0.01;

  function updateWeight(key) {
    return (event) =>
      setWeights((prev) => ({ ...prev, [key]: event.target.value }));
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");
    if (!weightsValid) {
      setError("Weights must sum to 1.00 (currently " + weightsSum.toFixed(4) + ").");
      return;
    }
    if (sourceMode === "url" && !url.trim()) {
      setError("Please enter a URL.");
      return;
    }
    if (sourceMode === "file" && !file) {
      setError("Please choose an HTML file to upload.");
      return;
    }

    setSubmitting(true);
    try {
      const response = await submitAnalysis({
        url: sourceMode === "url" ? url.trim() : null,
        file: sourceMode === "file" ? file : null,
        weightLinks: weights.links,
        weightStyles: weights.styles,
        weightScripts: weights.scripts,
      });
      setTask({
        task_id: response.task_id,
        report_id: response.report_id,
        status: response.status,
      });
      attemptsRef.current = 0;
      startPolling(response.task_id);
    } catch (err) {
      const data = err.response?.data || {};
      setError(
        data.url?.[0] ||
          data.uploaded_file?.[0] ||
          data.weights ||
          data.detail ||
          (sourceMode === "url"
            ? "Could not submit the URL. Is it valid and reachable?"
            : "Could not submit the file. Make sure it is a valid .html document.")
      );
    } finally {
      setSubmitting(false);
    }
  }

  function startPolling(taskId) {
    clearInterval(intervalRef.current);
    intervalRef.current = setInterval(async () => {
      attemptsRef.current += 1;
      try {
        const data = await pollTask(taskId);
        setTask((prev) => ({ ...prev, ...data }));

        if (data.status === "SUCCESS") {
          clearInterval(intervalRef.current);
          navigate(`/reports/${data.report_slug}`);
        } else if (data.status === "FAILED") {
          clearInterval(intervalRef.current);
          setError(data.error_message || "Analysis failed.");
        } else if (attemptsRef.current >= POLL_MAX_ATTEMPTS) {
          clearInterval(intervalRef.current);
          setError("Analysis is taking longer than expected. Check the dashboard later.");
        }
      } catch (err) {
        clearInterval(intervalRef.current);
        setError(err.response?.data?.detail || "Lost connection while polling.");
      }
    }, POLL_INTERVAL_MS);
  }

  const isPolling = task && task.status !== "SUCCESS" && task.status !== "FAILED";

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900">New analysis</h1>
        <p className="text-sm text-slate-500">
          Submit a public URL or upload an HTML file; we extract its
          architectural components and compute the complexity index
          C = Σ Wᵢ · Nᵢ.
        </p>
      </header>

      <form
        onSubmit={handleSubmit}
        className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm space-y-5"
      >
        {/* --- Source mode tabs (FR-03) --------------------------------- */}
        <div
          role="tablist"
          aria-label="Source type"
          className="inline-flex rounded-lg border border-slate-200 bg-slate-50 p-1 text-sm"
        >
          {[
            { value: "url", label: "URL" },
            { value: "file", label: "File upload" },
          ].map((opt) => {
            const active = sourceMode === opt.value;
            return (
              <button
                key={opt.value}
                type="button"
                role="tab"
                aria-selected={active}
                disabled={submitting || isPolling}
                onClick={() => {
                  setSourceMode(opt.value);
                  setError("");
                }}
                className={`rounded-md px-4 py-1.5 font-medium transition ${
                  active
                    ? "bg-white text-brand-700 shadow-sm"
                    : "text-slate-600 hover:text-slate-900"
                }`}
              >
                {opt.label}
              </button>
            );
          })}
        </div>

        {sourceMode === "url" ? (
          <div>
            <label htmlFor="url" className="block text-sm font-medium text-slate-700">
              URL
            </label>
            <input
              id="url"
              type="url"
              required
              placeholder="https://example.com"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              disabled={submitting || isPolling}
              className="mt-1 w-full rounded border border-slate-300 px-3 py-2 font-mono text-sm shadow-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20"
            />
          </div>
        ) : (
          <div>
            <label htmlFor="html-file" className="block text-sm font-medium text-slate-700">
              HTML file
            </label>
            <input
              id="html-file"
              type="file"
              accept=".html,.htm,text/html"
              required
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              disabled={submitting || isPolling}
              className="mt-1 w-full cursor-pointer rounded border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm file:mr-3 file:rounded file:border-0 file:bg-brand-50 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-brand-700 hover:file:bg-brand-100"
            />
            <p className="mt-1 text-xs text-slate-500">
              Accepts a single .html or .htm document (max 5 MB).
              {file && (
                <span className="block text-slate-700">
                  Selected: <span className="font-mono">{file.name}</span> ·{" "}
                  {(file.size / 1024).toFixed(1)} KB
                </span>
              )}
            </p>
          </div>
        )}

        <fieldset className="space-y-2">
          <legend className="text-sm font-medium text-slate-700">
            Weight coefficients (must sum to 1.00)
          </legend>
          <div className="grid grid-cols-3 gap-3">
            {[
              { key: "links", label: "Wₗᵢₙₖₛ" },
              { key: "styles", label: "Wₛₜᵧₗₑₛ" },
              { key: "scripts", label: "Wₛ𝒸ᵣᵢₚₜₛ" },
            ].map((w) => (
              <label key={w.key} className="block text-xs font-medium text-slate-500">
                {w.label}
                <input
                  type="number"
                  step="0.0001"
                  min="0"
                  max="1"
                  value={weights[w.key]}
                  onChange={updateWeight(w.key)}
                  disabled={submitting || isPolling}
                  className="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 font-mono text-sm"
                />
              </label>
            ))}
          </div>
          <p className={`text-xs ${weightsValid ? "text-slate-500" : "text-amber-600"}`}>
            Σ = {weightsSum.toFixed(4)}
          </p>
        </fieldset>

        {error && (
          <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={submitting || isPolling}
          className="w-full rounded bg-brand-600 px-4 py-2 font-medium text-white shadow-sm hover:bg-brand-700"
        >
          {submitting ? "Submitting..." : isPolling ? "Analysing..." : "Analyse"}
        </button>
      </form>

      {task && (
        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex items-center gap-3">
            {isPolling && (
              <span
                aria-hidden="true"
                className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-brand-500"
              />
            )}
            <div>
              <div className="text-sm font-medium text-slate-700">
                {STATUS_COPY[task.status] || task.status}
              </div>
              <div className="text-xs text-slate-500">
                task {task.task_id}
              </div>
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
