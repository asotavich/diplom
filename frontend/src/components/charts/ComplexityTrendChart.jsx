import { useMemo } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

/**
 * Line chart of complexity C over time for the dashboard. Oldest scan
 * on the left; the latest on the right. Handles the edge case where
 * the report list is unsorted by timestamp by doing one local sort.
 */
export default function ComplexityTrendChart({ reports }) {
  const data = useMemo(
    () =>
      [...reports]
        .sort((a, b) => new Date(a.scanned_at) - new Date(b.scanned_at))
        .map((r) => ({
          id: r.id,
          date: new Date(r.scanned_at).toLocaleDateString(),
          complexity: Number(r.complexity_index || 0),
          url: r.url,
        })),
    [reports]
  );

  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={data} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
        <XAxis dataKey="date" tick={{ fontSize: 12 }} />
        <YAxis tick={{ fontSize: 12 }} />
        <Tooltip
          contentStyle={{ borderRadius: 6, fontSize: 12 }}
          formatter={(value) => [value, "Complexity C"]}
          labelFormatter={(_, payload) => payload?.[0]?.payload?.url || ""}
        />
        <Line
          type="monotone"
          dataKey="complexity"
          stroke="#3b82f6"
          strokeWidth={2}
          dot={{ r: 4, strokeWidth: 2, fill: "#fff" }}
          activeDot={{ r: 6 }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
