import { useMemo } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const CATEGORY_COLORS = {
  links: "#3b82f6",
  styles: "#10b981",
  scripts: "#f59e0b",
};

/**
 * Horizontal bar of the top third-party hosts this page depends on,
 * collapsing the per-category lists into a single ranking so the chart
 * scans easily. Each bar is colored by which category the host occurs
 * most in (links / styles / scripts).
 */
export default function ExternalHostsChart({ metadata }) {
  const data = useMemo(() => aggregateTopHosts(metadata), [metadata]);

  if (data.length === 0) {
    return (
      <p className="py-12 text-center text-sm text-slate-500">
        No external hosts referenced by this page.
      </p>
    );
  }

  // Dynamic height so long lists don't squash the labels.
  const height = Math.max(220, data.length * 28 + 40);

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart
        data={data}
        layout="vertical"
        margin={{ top: 5, right: 20, left: 10, bottom: 5 }}
      >
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
        <XAxis type="number" allowDecimals={false} tick={{ fontSize: 12 }} />
        <YAxis
          type="category"
          dataKey="host"
          width={180}
          tick={{ fontSize: 12 }}
        />
        <Tooltip
          contentStyle={{ borderRadius: 6, fontSize: 12 }}
          formatter={(value, _name, payload) => [
            value,
            `${payload?.payload?.primaryCategory ?? "count"}`,
          ]}
        />
        <Bar dataKey="count" radius={[0, 4, 4, 0]}>
          {data.map((entry) => (
            <Cell
              key={entry.host}
              fill={CATEGORY_COLORS[entry.primaryCategory] || "#6366f1"}
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

function aggregateTopHosts(metadata) {
  const totals = new Map(); // host -> { host, count, perCategory: { links, styles, scripts } }

  for (const category of ["links", "styles", "scripts"]) {
    const bucket = metadata?.[category];
    if (!bucket?.top_external_hosts) continue;
    for (const { host, count } of bucket.top_external_hosts) {
      if (!totals.has(host)) {
        totals.set(host, {
          host,
          count: 0,
          perCategory: { links: 0, styles: 0, scripts: 0 },
        });
      }
      const entry = totals.get(host);
      entry.count += count;
      entry.perCategory[category] += count;
    }
  }

  return Array.from(totals.values())
    .map((entry) => {
      const primaryCategory = Object.entries(entry.perCategory).sort(
        (a, b) => b[1] - a[1]
      )[0][0];
      return { ...entry, primaryCategory };
    })
    .sort((a, b) => b.count - a.count)
    .slice(0, 10);
}
