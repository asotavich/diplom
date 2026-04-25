import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

/**
 * Stacked bar per component category showing how much of it is served
 * from the page's own host vs. third parties. Built from the
 * ``raw_metadata`` emitted by ``analyzer/services.py``.
 */
export default function InternalExternalChart({ metadata }) {
  const categories = [
    { key: "links", label: "Links" },
    { key: "styles", label: "Stylesheets" },
    { key: "scripts", label: "Scripts" },
  ];

  const data = categories
    .map(({ key, label }) => {
      const bucket = metadata[key];
      if (!bucket) return null;
      return {
        name: label,
        internal: bucket.internal || 0,
        external: bucket.external || 0,
      };
    })
    .filter(Boolean);

  if (data.length === 0 || data.every((d) => d.internal + d.external === 0)) {
    return (
      <p className="py-12 text-center text-sm text-slate-500">
        No resource metadata available for this scan.
      </p>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={280}>
      <BarChart data={data} margin={{ top: 10, right: 20, left: 0, bottom: 5 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
        <XAxis dataKey="name" tick={{ fontSize: 12 }} />
        <YAxis allowDecimals={false} tick={{ fontSize: 12 }} />
        <Tooltip contentStyle={{ borderRadius: 6, fontSize: 12 }} />
        <Legend />
        <Bar dataKey="internal" name="Internal" stackId="stack" fill="#3b82f6" radius={[0, 0, 0, 0]} />
        <Bar dataKey="external" name="External" stackId="stack" fill="#f97316" radius={[4, 4, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}
