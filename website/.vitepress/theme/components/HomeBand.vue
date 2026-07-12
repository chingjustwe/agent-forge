<template>
  <div class="home-band">
    <div class="home-band-inner">
      <div class="terminal">
        <div class="terminal-bar">
          <span class="dot"></span><span class="dot"></span><span class="dot"></span>
          <span class="terminal-title">quickstart.sh</span>
        </div>
        <pre><code># Start the API gateway
uvicorn src.main:create_app --reload --port 8000

# Stream a chat (SSE)
curl -N http://localhost:8000/api/v1/chat \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"agent":"assistant","message":"hello"}'</code></pre>
      </div>
      <div class="stats">
        <div class="stat">
          <span class="stat-num">3</span>
          <span class="stat-label">architectural layers</span>
        </div>
        <div class="stat">
          <span class="stat-num">4</span>
          <span class="stat-label">RBAC roles</span>
        </div>
        <div class="stat">
          <span class="stat-num">7</span>
          <span class="stat-label">build phases</span>
        </div>
        <div class="stat">
          <span class="stat-num">404</span>
          <span class="stat-label">tests</span>
        </div>
      </div>
    </div>
  </div>
</template>
