import { Navigate, useLocation } from "react-router-dom";

import { useAuth } from "../auth/AuthContext.jsx";
import Loading from "./Loading.jsx";

/**
 * Gatekeeper: redirects to /login if the user isn't authenticated, and
 * waits for the initial profile fetch so the UI doesn't flicker.
 */
export default function ProtectedRoute({ children }) {
  const { isAuthenticated, isBootstrapping } = useAuth();
  const location = useLocation();

  if (isBootstrapping) {
    return <Loading label="Checking session..." />;
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  return children;
}
