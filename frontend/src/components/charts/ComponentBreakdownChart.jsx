import {
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";

// Brand-aligned palette; each slice keeps a stable color across renders.
const SEGMENTS = [
  { key: "count_links", label: "Links", color: "#3b82f6" },
  { key: "count_styles", label: "Stylesheets", color: "#10b981" },
  { key: "count_scripts", label: "Scripts", color: "#f59e0b" },
];

export default function ComponentBreakdownChart({ report }) {
  const data = SEGMENTS.map(({ key, label, color }) => ({
    name: label,
    value: report[key] || 0,
    color,
  }));

  const total = data.reduce((sum, d) => sum + d.value, 0);

  if (total === 0) {
    return <EmptyState />;
  }

  return (
    <ResponsiveContainer width="100%" height={280}>
      <PieChart>
        <Pie
          data={data}
          dataKey="value"
          nameKey="name"
          cx="50%"
          cy="50%"
          innerRadius={55}
          outerRadius={95}
          paddingAngle={2}
          label={({ name, percent }) => `${name} ${(percent * 100).toFixed(0)}%`}
        >
          {data.map((entry) => (
            <Cell key={entry.name} fill={entry.color} />
          ))}
        </Pie>
        <Tooltip
          formatter={(value, name) => [value, name]}
          contentStyle={{ borderRadius: 6, fontSize: 12 }}
        />
        <Legend iconType="circle" />
      </PieChart>
    </ResponsiveContainer>
  );
}

function EmptyState() {
  return (
    <p className="py-12 text-center text-sm text-slate-500">
      Nothing to visualise — the page had no extractable components.
    </p>
  );
}
