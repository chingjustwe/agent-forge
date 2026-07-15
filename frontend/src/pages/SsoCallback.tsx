import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { setToken } from "../api";

const ERROR_MESSAGES: Record<string, string> = {
  state_mismatch: "Security check failed (state mismatch). Please try again.",
  provider_not_found: "SSO provider not found or disabled.",
  token_exchange_failed: "Failed to exchange authorization code. Please try again.",
  no_access_token: "Identity provider did not return an access token.",
  userinfo_failed: "Failed to fetch user information from the identity provider.",
  no_subject: "Identity provider did not return a user identifier.",
  user_not_found: "Your account was not found. Please contact an administrator.",
  auto_provision_disabled: "Automatic account creation is disabled. Please contact an administrator.",
  access_denied: "You denied the authorization request.",
};

export default function SsoCallback() {
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const processedRef = useRef(false);

  useEffect(() => {
    if (processedRef.current) return;
    processedRef.current = true;

    // Parse token from URL fragment: #token=xxx&redirect=/path
    const hash = window.location.hash.slice(1);
    const hashParams = new URLSearchParams(hash);
    const token = hashParams.get("token");
    const redirect = hashParams.get("redirect") || "/";

    // Parse error from URL query string: ?error=reason
    const queryParams = new URLSearchParams(window.location.search);
    const errCode = queryParams.get("error");

    if (errCode) {
      setError(ERROR_MESSAGES[errCode] || `SSO login failed: ${errCode}`);
      return;
    }

    if (token) {
      setToken(token);
      // Clear the hash before navigating so the token doesn't linger in history.
      window.history.replaceState(null, "", redirect);
      navigate(redirect, { replace: true });
    } else {
      setError("No token received. Please try logging in again.");
    }
  }, [navigate]);

  if (error) {
    return (
      <div className="login-page">
        <div className="login-card">
          <h1 className="login-title">SSO Login Failed</h1>
          <p className="login-error">{error}</p>
          <a href="/login" className="btn btn-primary" style={{ display: "inline-block", marginTop: 16 }}>
            Back to Login
          </a>
        </div>
      </div>
    );
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <h1 className="login-title">Signing in...</h1>
        <p style={{ color: "var(--text-secondary)", marginTop: 8 }}>
          Please wait while we complete your sign-in.
        </p>
      </div>
    </div>
  );
}
