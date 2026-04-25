import { useState } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";

import { useAuth } from "../auth/AuthContext.jsx";

export default function RegisterPage() {
  const { register, isAuthenticated, isBootstrapping } = useAuth();
  const navigate = useNavigate();

  const [form, setForm] = useState({
    username: "",
    email: "",
    password: "",
    passwordConfirm: "",
  });
  const [errors, setErrors] = useState({});
  const [submitting, setSubmitting] = useState(false);

  if (!isBootstrapping && isAuthenticated) {
    return <Navigate to="/" replace />;
  }

  function update(key) {
    return (event) => setForm((prev) => ({ ...prev, [key]: event.target.value }));
  }

  async function onSubmit(event) {
    event.preventDefault();
    setSubmitting(true);
    setErrors({});
    try {
      await register(form);
      navigate("/", { replace: true });
    } catch (err) {
      // DRF validation errors come back as { field: [msg, ...], ... }.
      const data = err.response?.data || {};
      const normalised = Object.fromEntries(
        Object.entries(data).map(([key, value]) => [
          key,
          Array.isArray(value) ? value.join(" ") : String(value),
        ])
      );
      setErrors(normalised);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4 py-12">
      <div className="w-full max-w-md rounded-xl border border-slate-200 bg-white p-8 shadow-sm">
        <h1 className="text-2xl font-semibold text-slate-900">Create account</h1>
        <p className="mt-1 text-sm text-slate-500">Start analysing front-end complexity.</p>

        <form onSubmit={onSubmit} className="mt-6 space-y-4" noValidate>
          {[
            { key: "username", label: "Username", type: "text", autoComplete: "username" },
            { key: "email", label: "Email", type: "email", autoComplete: "email" },
            { key: "password", label: "Password", type: "password", autoComplete: "new-password" },
            { key: "passwordConfirm", label: "Confirm password", type: "password", autoComplete: "new-password" },
          ].map((field) => (
            <div key={field.key}>
              <label htmlFor={field.key} className="block text-sm font-medium text-slate-700">
                {field.label}
              </label>
              <input
                id={field.key}
                type={field.type}
                autoComplete={field.autoComplete}
                required
                value={form[field.key]}
                onChange={update(field.key)}
                className="mt-1 w-full rounded border border-slate-300 px-3 py-2 shadow-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20"
              />
              {errors[field.key === "passwordConfirm" ? "password_confirm" : field.key] && (
                <p className="mt-1 text-xs text-red-600">
                  {errors[field.key === "passwordConfirm" ? "password_confirm" : field.key]}
                </p>
              )}
            </div>
          ))}

          {errors.non_field_errors && (
            <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
              {errors.non_field_errors}
            </div>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded bg-brand-600 px-4 py-2 font-medium text-white shadow-sm hover:bg-brand-700"
          >
            {submitting ? "Creating..." : "Create account"}
          </button>
        </form>

        <p className="mt-6 text-center text-sm text-slate-500">
          Already have an account?{" "}
          <Link to="/login" className="font-medium text-brand-600 hover:text-brand-700">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
