import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { getInvite, acceptInvite } from "../api";

type Status = "loading" | "invalid" | "expired" | "already_used" | "already_registered" | "ready" | "done";

export default function InviteRegister() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token") || "";

  const [status, setStatus] = useState<Status>("loading");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!token) {
      setStatus("invalid");
      return;
    }
    getInvite(token)
      .then((info) => {
        setEmail(info.email);
        setRole(info.role);
        setName(info.email.split("@")[0]);
        setStatus("ready");
      })
      .catch((err: Error) => {
        if (err.message.includes("EXPIRED")) setStatus("expired");
        else if (err.message.includes("GONE")) setStatus("already_used");
        else if (err.message.includes("already registered")) setStatus("already_registered");
        else setStatus("invalid");
      });
  }, [token]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (password !== confirm) {
      setError("Passwords do not match");
      return;
    }
    if (password.length < 6) {
      setError("Password must be at least 6 characters");
      return;
    }
    setError("");
    setSubmitting(true);
    try {
      await acceptInvite({ token, password, name });
      setStatus("done");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to accept invite");
    } finally {
      setSubmitting(false);
    }
  };

  if (status === "done") {
    return (
      <div className="login-page">
        <div className="login-card" style={{ textAlign: "center" }}>
          <div className="login-brand">
            <div className="login-brand-icon">A</div>
            <span className="login-brand-text">Agent Platform</span>
          </div>
          <h1 className="login-title">Account Activated!</h1>
          <p style={{ color: "var(--text-secondary)", margin: "16px 0 24px" }}>
            Your account has been set up successfully. You are now being redirected.
          </p>
          <button className="btn btn-primary" onClick={() => navigate("/")}>
            Go to Dashboard
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-brand">
          <div className="login-brand-icon">A</div>
          <span className="login-brand-text">Agent Platform</span>
        </div>

        {status === "loading" && <p className="login-title" style={{ textAlign: "center" }}>Validating invite link...</p>}

        {status === "invalid" && (
          <>
            <h1 className="login-title">Invalid Invite Link</h1>
            <p style={{ color: "var(--text-secondary)", textAlign: "center" }}>
              This invite link is not valid. Please check the link or contact your administrator.
            </p>
          </>
        )}

        {status === "expired" && (
          <>
            <h1 className="login-title">Invite Expired</h1>
            <p style={{ color: "var(--text-secondary)", textAlign: "center" }}>
              This invitation has expired (valid for 7 days). Please ask your administrator to send a new invite.
            </p>
          </>
        )}

        {status === "already_used" && (
          <>
            <h1 className="login-title">Invite Already Used</h1>
            <p style={{ color: "var(--text-secondary)", textAlign: "center" }}>
              This invitation has already been used. Please sign in with your existing account.
            </p>
            <button className="btn btn-primary" style={{ width: "100%" }} onClick={() => navigate("/login")}>
              Go to Sign In
            </button>
          </>
        )}

        {status === "already_registered" && (
          <>
            <h1 className="login-title">Already Registered</h1>
            <p style={{ color: "var(--text-secondary)", textAlign: "center" }}>
              This account has already been set up. Please sign in.
            </p>
            <button className="btn btn-primary" style={{ width: "100%" }} onClick={() => navigate("/login")}>
              Go to Sign In
            </button>
          </>
        )}

        {status === "ready" && (
          <>
            <h1 className="login-title">Accept Invitation</h1>
            <p style={{ color: "var(--text-secondary)", textAlign: "center", marginBottom: 20 }}>
              You've been invited as <strong>{role}</strong> to join <strong>{email}</strong>
            </p>

            {error && <div className="alert alert-error">{error}</div>}

            <form className="login-form" onSubmit={handleSubmit}>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Full name"
                required
              />
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Password (min 6 characters)"
                minLength={6}
                required
              />
              <input
                type="password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                placeholder="Confirm password"
                required
              />
              <button type="submit" className="btn btn-primary" disabled={submitting}>
                {submitting ? "Please wait..." : "Set Up Account"}
              </button>
            </form>
          </>
        )}
      </div>
    </div>
  );
}
