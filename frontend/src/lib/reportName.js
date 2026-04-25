/**
 * Human-friendly label for a report row. Prefer the parsed hostname for
 * URL scans, fall back to the uploaded HTML's filename, and finally to a
 * neutral placeholder so we never leak the database primary key.
 */
export function reportDisplayName(report) {
  if (!report) return "Untitled report";

  if (report.url) {
    try {
      const { hostname } = new URL(report.url);
      return hostname.replace(/^www\./i, "") || report.url;
    } catch {
      return report.url;
    }
  }

  if (report.uploaded_file_name) return report.uploaded_file_name;

  return "Untitled report";
}
