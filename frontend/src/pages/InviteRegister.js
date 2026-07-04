import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { getInvite, acceptInvite } from "../api";
export default function InviteRegister() {
    const navigate = useNavigate();
    const [searchParams] = useSearchParams();
    const token = searchParams.get("token") || "";
    const [status, setStatus] = useState("loading");
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
            .catch((err) => {
            if (err.message.includes("EXPIRED"))
                setStatus("expired");
            else if (err.message.includes("GONE"))
                setStatus("already_used");
            else if (err.message.includes("already registered"))
                setStatus("already_registered");
            else
                setStatus("invalid");
        });
    }, [token]);
    const handleSubmit = async (e) => {
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
        }
        catch (err) {
            setError(err instanceof Error ? err.message : "Failed to accept invite");
        }
        finally {
            setSubmitting(false);
        }
    };
    if (status === "done") {
        return (_jsx("div", { className: "login-page", children: _jsxs("div", { className: "login-card", style: { textAlign: "center" }, children: [_jsxs("div", { className: "login-brand", children: [_jsx("div", { className: "login-brand-icon", children: "A" }), _jsx("span", { className: "login-brand-text", children: "Agent Platform" })] }), _jsx("h1", { className: "login-title", children: "Account Activated!" }), _jsx("p", { style: { color: "var(--text-secondary)", margin: "16px 0 24px" }, children: "Your account has been set up successfully. You are now being redirected." }), _jsx("button", { className: "btn btn-primary", onClick: () => navigate("/"), children: "Go to Dashboard" })] }) }));
    }
    return (_jsx("div", { className: "login-page", children: _jsxs("div", { className: "login-card", children: [_jsxs("div", { className: "login-brand", children: [_jsx("div", { className: "login-brand-icon", children: "A" }), _jsx("span", { className: "login-brand-text", children: "Agent Platform" })] }), status === "loading" && _jsx("p", { className: "login-title", style: { textAlign: "center" }, children: "Validating invite link..." }), status === "invalid" && (_jsxs(_Fragment, { children: [_jsx("h1", { className: "login-title", children: "Invalid Invite Link" }), _jsx("p", { style: { color: "var(--text-secondary)", textAlign: "center" }, children: "This invite link is not valid. Please check the link or contact your administrator." })] })), status === "expired" && (_jsxs(_Fragment, { children: [_jsx("h1", { className: "login-title", children: "Invite Expired" }), _jsx("p", { style: { color: "var(--text-secondary)", textAlign: "center" }, children: "This invitation has expired (valid for 7 days). Please ask your administrator to send a new invite." })] })), status === "already_used" && (_jsxs(_Fragment, { children: [_jsx("h1", { className: "login-title", children: "Invite Already Used" }), _jsx("p", { style: { color: "var(--text-secondary)", textAlign: "center" }, children: "This invitation has already been used. Please sign in with your existing account." }), _jsx("button", { className: "btn btn-primary", style: { width: "100%" }, onClick: () => navigate("/login"), children: "Go to Sign In" })] })), status === "already_registered" && (_jsxs(_Fragment, { children: [_jsx("h1", { className: "login-title", children: "Already Registered" }), _jsx("p", { style: { color: "var(--text-secondary)", textAlign: "center" }, children: "This account has already been set up. Please sign in." }), _jsx("button", { className: "btn btn-primary", style: { width: "100%" }, onClick: () => navigate("/login"), children: "Go to Sign In" })] })), status === "ready" && (_jsxs(_Fragment, { children: [_jsx("h1", { className: "login-title", children: "Accept Invitation" }), _jsxs("p", { style: { color: "var(--text-secondary)", textAlign: "center", marginBottom: 20 }, children: ["You've been invited as ", _jsx("strong", { children: role }), " to join ", _jsx("strong", { children: email })] }), error && _jsx("div", { className: "alert alert-error", children: error }), _jsxs("form", { className: "login-form", onSubmit: handleSubmit, children: [_jsx("input", { type: "text", value: name, onChange: (e) => setName(e.target.value), placeholder: "Full name", required: true }), _jsx("input", { type: "password", value: password, onChange: (e) => setPassword(e.target.value), placeholder: "Password (min 6 characters)", minLength: 6, required: true }), _jsx("input", { type: "password", value: confirm, onChange: (e) => setConfirm(e.target.value), placeholder: "Confirm password", required: true }), _jsx("button", { type: "submit", className: "btn btn-primary", disabled: submitting, children: submitting ? "Please wait..." : "Set Up Account" })] })] }))] }) }));
}
