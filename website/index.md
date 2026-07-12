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
  image:
    src: /hero.svg
    alt: Agent Forge — Gateway, Harness, Runtime, Telemetry, LLM API
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
      svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg>'
  - title: Observability
    details: Traces, metrics, and quota tracking via OpenTelemetry. Dashboards for requests, sessions, cost, errors, and live SSE events.
    icon:
      svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>'
  - title: Agent Harness
    details: Guardrail pipeline, sandboxing, tool management, retry, and circuit breaker behind a single execution interface.
    icon:
      svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>'
  - title: Admin Dashboard
    details: User CRUD, workspace management, invitation flow, API key management, audit log viewer, and usage statistics.
    icon:
      svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>'
  - title: React SPA Frontend
    details: Custom CSS design system (no UI library), dark/light theme, SSE streaming chat, recharts analytics, shared Modal/Toast/Confirm components.
    icon:
      svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>'
---

<!-- How it works -->
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

<!-- Quickstart terminal + stats band -->
<HomeBand />
