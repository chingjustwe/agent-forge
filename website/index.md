---
layout: home

title: Agent Forge
titleTemplate: Multi-tenant AI Agent Platform

hero:
  name: Agent Forge
  text: Self-hostable, multi-tenant AI agent platform
  tagline: |
    RBAC · Observability · Quota management · Admin dashboard
    Pluggable agent framework adapters · OpenAI-compatible LLM streaming
  actions:
    - theme: brand
      text: Get Started
      link: /guide/getting-started
    - theme: alt
      text: View on GitHub
      link: https://github.com/chingjustwe/agent-forge

features:
  - title: Multi-Tenant + RBAC
    details: Workspace-scoped RBAC driven by permissions.yaml. Roles from viewer to tenant_admin, with scoped admin access.
    icon:
      svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>'
  - title: Pluggable Runtime
    details: Agent Runtime behind an AgentRuntime ABC. DirectLLMAdapter (DeepSeek) today; ADK and LangGraph adapters plug in with zero gateway changes.
    icon:
      svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg>'
  - title: Observability
    details: Traces, metrics, and quota tracking via OpenTelemetry. Dashboards for requests, sessions, cost, errors, and live SSE events.
    icon:
      svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 20V10M12 20V4M6 20v-6"/></svg>'
  - title: Agent Harness
    details: Guardrail pipeline, sandboxing, tool management, retry, and circuit breaker behind a single execution interface.
    icon:
      svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 9h.01M15 15h.01M9 15h.01M15 9h.01"/></svg>'
  - title: Admin Dashboard
    details: User CRUD, workspace management, invitation flow, API key management, audit log viewer, and usage statistics.
    icon:
      svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>'
  - title: React SPA Frontend
    details: Custom CSS design system (no UI library), dark/light theme, SSE streaming chat, recharts analytics, shared Modal/Toast/Confirm components.
    icon:
      svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>'
---

<!-- Custom landing section between hero and features -->
<div class="home-section">
  <h2>How it works</h2>
  <p class="subtitle">
    A thin API Gateway routes authenticated, authorized requests to a pluggable
    Agent Runtime — governance and observability wrap every execution.
  </p>
  <div class="flow-grid">
    <div class="flow-step">
      <span class="flow-badge">Gateway</span>
      <p>FastAPI handles JWT auth, RBAC permission checks, audit logging, and quota enforcement.</p>
    </div>
    <div class="flow-step">
      <span class="flow-badge">Harness</span>
      <p>Guardrail pipeline and sandbox wrap tool execution with retry and circuit breaking.</p>
    </div>
    <div class="flow-step">
      <span class="flow-badge">Runtime</span>
      <p>An AgentRuntime adapter calls the LLM, streaming events back over SSE.</p>
    </div>
    <div class="flow-step">
      <span class="flow-badge">Telemetry</span>
      <p>Spans, metrics, logs, and quota usage are exported via OpenTelemetry.</p>
    </div>
  </div>
</div>
