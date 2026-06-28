import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { loginUser, registerUser } from "../api";

export default function LoginPage() {
  const navigate = useNavigate();
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
      navigate("/");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ maxWidth: 400, margin: "100px auto", padding: 16 }}>
      <h1>Agent Platform</h1>
      <h2>{mode === "login" ? "Sign In" : "Create Account"}</h2>

      <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {mode === "register" && (
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Name"
            required
            style={{ padding: 8 }}
          />
        )}
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="Email"
          required
          style={{ padding: 8 }}
        />
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Password"
          required
          style={{ padding: 8 }}
        />
        <button type="submit" disabled={loading} style={{ padding: 8 }}>
          {loading ? "Please wait..." : mode === "login" ? "Sign In" : "Register"}
        </button>
      </form>

      {error && <p style={{ color: "red" }}>{error}</p>}

      <p style={{ marginTop: 16 }}>
        {mode === "login" ? (
          <>
            Don't have an account?{" "}
            <button onClick={() => setMode("register")} style={{ background: "none", border: "none", color: "blue", cursor: "pointer", textDecoration: "underline" }}>
              Register
            </button>
          </>
        ) : (
          <>
            Already have an account?{" "}
            <button onClick={() => setMode("login")} style={{ background: "none", border: "none", color: "blue", cursor: "pointer", textDecoration: "underline" }}>
              Sign In
            </button>
          </>
        )}
      </p>

      <hr style={{ margin: "24px 0" }} />
      <p style={{ fontSize: "0.9em", color: "#666" }}>
        OIDC SSO providers (configured in backend):
      </p>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <a href="/api/v1/auth/login?provider=google">
          <button style={{ padding: "8px 16px" }}>Google</button>
        </a>
        <a href="/api/v1/auth/login?provider=azure">
          <button style={{ padding: "8px 16px" }}>Azure AD</button>
        </a>
        <a href="/api/v1/auth/login?provider=okta">
          <button style={{ padding: "8px 16px" }}>Okta</button>
        </a>
      </div>
    </div>
  );
}
