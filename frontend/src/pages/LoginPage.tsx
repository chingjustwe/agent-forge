import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { loginUser, registerUser } from "../api";

export default function LoginPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      if (mode === "login") {
        await loginUser(email, password);
      } else {
        await registerUser(email, password, name);
      }
      // P2-1: honor ?redirect= so invitees return to the accept page after login.
      const redirect = searchParams.get("redirect");
      navigate(redirect || "/");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-brand">
          <div className="login-brand-icon">A</div>
          <span className="login-brand-text">Agent Platform</span>
        </div>

        <h1 className="login-title">
          {mode === "login" ? "Sign in to your account" : "Create an account"}
        </h1>

        {error && <div className="alert alert-error">{error}</div>}

        <form className="login-form" onSubmit={handleSubmit}>
          {mode === "register" && (
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Full name"
              required
            />
          )}
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="Email address"
            required
          />
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Password"
            required
          />
          <button type="submit" className="btn btn-primary" disabled={loading}>
            {loading ? "Please wait..." : mode === "login" ? "Sign In" : "Register"}
          </button>
        </form>

        <div className="login-toggle">
          {mode === "login" ? (
            <>
              Don't have an account?{" "}
              <button onClick={() => setMode("register")}>Register</button>
            </>
          ) : (
            <>
              Already have an account?{" "}
              <button onClick={() => setMode("login")}>Sign In</button>
            </>
          )}
        </div>

        <hr className="login-divider" />

        <div className="login-sso-label">Single sign-on</div>
        <div className="login-sso-buttons">
          <a href="/api/v1/auth/login?provider=google" className="login-sso-btn">Google</a>
          <a href="/api/v1/auth/login?provider=azure" className="login-sso-btn">Azure AD</a>
          <a href="/api/v1/auth/login?provider=okta" className="login-sso-btn">Okta</a>
        </div>
      </div>
    </div>
  );
}