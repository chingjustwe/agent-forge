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
  - title: Agents — pluggable runtime
    details: One AgentRuntime interface, many frameworks. DirectLLM (DeepSeek) ships today; Google ADK and LangGraph adapters plug in with zero gateway changes.
    icon:
      svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="8" width="16" height="12" rx="2"/><path d="M12 4v4"/><circle cx="12" cy="4" r="1.6"/><path d="M9 14h.01M15 14h.01"/><path d="M9.5 17h5"/></svg>'
  - title: Skills
    details: Reusable skill packages across three layers — user, project, and per-workspace. RBAC-guarded, audited CRUD wired straight into the harness.
    icon:
      svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l1.8 4.8L18.6 9.6 13.8 11.4 12 16.2 10.2 11.4 5.4 9.6 10.2 7.8z"/><path d="M18.5 14.5l.8 2.1 2.2.8-2.2.8-.8 2.1-.8-2.1-2.2-.8 2.2-.8z"/></svg>'
  - title: MCP integrations
    details: Workspace-scoped MCP server registry with CRUD, live tool discovery, and health checks — extend agents with external tools and context.
    icon:
      svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 2v5"/><path d="M15 2v5"/><path d="M6.5 7h11a1.5 1.5 0 0 1 1.5 1.5V12a5.5 5.5 0 0 1-11 0V8.5A1.5 1.5 0 0 1 6.5 7z"/><path d="M12 17.5V22"/><path d="M9.5 22h5"/></svg>'
  - title: Global usage control
    details: Tenant-wide quota management with token and cost tracking. QuotaGuardrail enforces limits per workspace and feeds the usage dashboards.
    icon:
      svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><rect x="7" y="11" width="3" height="6" rx="1"/><rect x="12" y="7" width="3" height="10" rx="1"/><rect x="17" y="13" width="3" height="4" rx="1"/></svg>'
  - title: Multi-tenant + RBAC
    details: Workspace-scoped RBAC driven by permissions.yaml — from viewer to tenant_admin, with scoped admin access and a single source of truth.
    icon:
      svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg>'
  - title: Observability
    details: Traces, metrics, and quota signals exported via OpenTelemetry — live dashboards for requests, sessions, cost, errors, and SSE events.
    icon:
      svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>'
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
