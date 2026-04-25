/**
 * Reports / analysis task API.
 */

import api from "./client";

export async function listReports({ page = 1 } = {}) {
  const { data } = await api.get("/reports/", { params: { page } });
  return data; // { count, next, previous, results: [...] }
}

export async function getReport(slug) {
  const { data } = await api.get(`/reports/${slug}/`);
  return data;
}

export async function deleteReport(slug) {
  await api.delete(`/reports/${slug}/`);
}

export async function submitAnalysis({
  url,
  file = null,
  project = null,
  weightLinks = "0.3333",
  weightStyles = "0.3333",
  weightScripts = "0.3333",
}) {
  // FR-03: when the user supplies a file, switch to multipart so the binary
  // payload survives transport. JSON path is kept for plain URL submissions.
  if (file) {
    const formData = new FormData();
    formData.append("uploaded_file", file);
    if (project !== null && project !== undefined) {
      formData.append("project", project);
    }
    formData.append("weight_links", weightLinks);
    formData.append("weight_styles", weightStyles);
    formData.append("weight_scripts", weightScripts);

    const { data } = await api.post("/reports/", formData, {
      headers: { "Content-Type": "multipart/form-data" },
    });
    return data;
  }

  const { data } = await api.post("/reports/", {
    url,
    project,
    weight_links: weightLinks,
    weight_styles: weightStyles,
    weight_scripts: weightScripts,
  });
  // { task_id, report_id, status, status_url, report_url }
  return data;
}

export async function pollTask(taskId) {
  const { data } = await api.get(`/tasks/${taskId}/`);
  return data;
}

export async function listProjects() {
  const { data } = await api.get("/projects/");
  return data;
}

async function streamDownload(path, fallbackName) {
  const response = await api.get(path, { responseType: "blob" });

  const disposition = response.headers?.["content-disposition"] || "";
  const match = disposition.match(/filename="?([^"]+)"?/i);
  const filename = match ? match[1] : fallbackName;

  const blobUrl = window.URL.createObjectURL(new Blob([response.data]));
  const link = document.createElement("a");
  link.href = blobUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(blobUrl);
}

export function downloadReportExport(slug) {
  return streamDownload(`/reports/${slug}/export/`, `feanalyzer-report-${slug}.xlsx`);
}

export function downloadReportPdf(slug) {
  return streamDownload(`/reports/${slug}/export.pdf/`, `feanalyzer-report-${slug}.pdf`);
}
