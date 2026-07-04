import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { loginUser, registerUser } from "../api";
export default function LoginPage() {
    const navigate = useNavigate();
    const [searchParams] = useSearchParams();
    const [mode, setMode] = useState("login");
    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");
    const [name, setName] = useState("");
    const [error, setError] = useState("");
    const [loading, setLoading] = useState(false);
    async function handleSubmit(e) {
        e.preventDefault();
        setError("");
        setLoading(true);
        try {
            if (mode === "login") {
                await loginUser(email, password);
            }
            else {
                await registerUser(email, password, name);
            }
            // P2-1: honor ?redirect= so invitees return to the accept page after login.
            const redirect = searchParams.get("redirect");
            navigate(redirect || "/");
        }
        catch (err) {
            setError(err instanceof Error ? err.message : "An error occurred");
        }
        finally {
            setLoading(false);
        }
    }
    return (_jsx("div", { className: "login-page", children: _jsxs("div", { className: "login-card", children: [_jsxs("div", { className: "login-brand", children: [_jsx("div", { className: "login-brand-icon", children: "A" }), _jsx("span", { className: "login-brand-text", children: "Agent Platform" })] }), _jsx("h1", { className: "login-title", children: mode === "login" ? "Sign in to your account" : "Create an account" }), error && _jsx("div", { className: "alert alert-error", children: error }), _jsxs("form", { className: "login-form", onSubmit: handleSubmit, children: [mode === "register" && (_jsx("input", { type: "text", value: name, onChange: (e) => setName(e.target.value), placeholder: "Full name", required: true })), _jsx("input", { type: "email", value: email, onChange: (e) => setEmail(e.target.value), placeholder: "Email address", required: true }), _jsx("input", { type: "password", value: password, onChange: (e) => setPassword(e.target.value), placeholder: "Password", required: true }), _jsx("button", { type: "submit", className: "btn btn-primary", disabled: loading, children: loading ? "Please wait..." : mode === "login" ? "Sign In" : "Register" })] }), _jsx("div", { className: "login-toggle", children: mode === "login" ? (_jsxs(_Fragment, { children: ["Don't have an account?", " ", _jsx("button", { onClick: () => setMode("register"), children: "Register" })] })) : (_jsxs(_Fragment, { children: ["Already have an account?", " ", _jsx("button", { onClick: () => setMode("login"), children: "Sign In" })] })) }), _jsx("hr", { className: "login-divider" }), _jsx("div", { className: "login-sso-label", children: "Single sign-on" }), _jsxs("div", { className: "login-sso-buttons", children: [_jsx("a", { href: "/api/v1/auth/login?provider=google", className: "login-sso-btn", children: "Google" }), _jsx("a", { href: "/api/v1/auth/login?provider=azure", className: "login-sso-btn", children: "Azure AD" }), _jsx("a", { href: "/api/v1/auth/login?provider=okta", className: "login-sso-btn", children: "Okta" })] })] }) }));
}
