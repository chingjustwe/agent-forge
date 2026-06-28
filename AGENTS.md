# Agent Development Workflow

## Agent Architecture

```
.opencode/agents/
├── orchestrator.md       ← Phase coordinator — dispatches tasks, routes feedback
├── py-backend.md         ← Python backend (FastAPI, SQLAlchemy, pytest)
├── py-adapter.md         ← AI framework integration (ADK, LangGraph, Harness)
├── react-frontend.md     ← React frontend (Vite, TypeScript, recharts)
├── spec-reviewer.md      ← Spec compliance review (read-only)
└── code-reviewer.md      ← Code quality review (read-only)
```

## Execution Model

Each Phase is executed in an isolated git worktree, managed by the orchestrator agent.

### Phase Workflow

```
Orchestrator reads phase spec
  └── For each task (in dependency order):
        ├── Dispatch implementer agent
        │     ├── Implement code + unit tests
        │     ├── Run tests: ALL must pass
        │     ├── Run lint/typecheck
        │     └── Commit
        ├── Dispatch spec-reviewer
        │     ├── ✓ PASS → proceed to code review
        │     └── ✗ FAIL → route exact feedback to implementer → re-dispatch
        └── Dispatch code-reviewer
              ├── ✓ PASS → mark task completed
              └── ✗ FAIL → route exact feedback to implementer → re-dispatch

All tasks complete → final code-reviewer (holistic) → report to human
```

### Test Coverage Rules

**Mandatory for every code change:**

1. Every new function/method/route MUST have corresponding unit tests
2. Every bug fix MUST add a test that reproduces the bug before fixing
3. All tests MUST pass before marking a task done
4. Test types by layer:

   | Layer | Tool | Coverage |
   |-------|------|----------|
   | Pydantic models | pytest | Validation, defaults, edge cases |
   | Adapters | pytest + pytest-httpx | Mock external APIs, verify StreamEvent output |
   | Gateway routes | pytest + httpx ASGITransport | Status codes, response format, auth enforcement |
   | Frontend | vitest (if added) | Component rendering, SSE consumption |
   | Harness | pytest-asyncio | Guardrails, tool execution, retry, circuit breaker |

5. Mock external dependencies (LLM APIs, OIDC providers, OTel collectors) — never make real network calls in tests
6. Async tests use `pytest-asyncio` with `@pytest.mark.asyncio`
7. FastAPI integration tests use `httpx.AsyncClient` with `ASGITransport`

### Regression Prevention

- Run the FULL test suite after every change, not just the new tests
- Existing tests must continue to pass — if a change breaks them, either the change needs adjustment OR the old test needs updating (with documented reasoning)
- Code-reviewer checks for regression risk: mocking patterns, shared state, test isolation
- Parallel tasks (frontend + backend) run their tests independently to avoid false failures

### Review Cycle Rules

- No limit on review iterations — loop until both reviewers pass
- spec-reviewer and code-reviewer are read-only: they identify issues but do NOT write fixes
- The orchestrator routes exact reviewer feedback to the implementer — feedback is not summarized or filtered
- spec-reviewer must pass BEFORE code-reviewer runs (wrong order = restart the task)

## Agent Responsibilities

### Implementer (py-backend, py-adapter, react-frontend)

- Write code + tests
- Run tests before reporting DONE
- Self-review: check for edge cases, error handling
- Report: what was implemented, test results, any concerns
- If blocked, explain why

### Spec Reviewer

- Compare implementation against phase spec acceptance criteria
- Check: all required functionality present? Nothing extra?
- Report: PASS with evidence, or FAIL with specific gaps (file + line + exact spec quote)

### Code Quality Reviewer

- Review: error handling, type hints, test coverage, security, conventions
- Check: are existing tests still passing? Any regression risk?
- Report: PASS or FAIL with severity (CRITICAL / MAJOR / MINOR) per issue

## Getting Started

To start a new phase:

```bash
# Load the subagent-driven-development skill
# The orchestrator will:
#   1. Read the phase spec
#   2. Create a git worktree for isolation
#   3. Dispatch tasks in order
#   4. Coordinate review cycles
```
