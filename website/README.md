# Agent Forge Documentation Site

VitePress documentation site for the Agent Forge project. Published at `https://chingjustwe.github.io/agent-forge/`.

## Local Development

```bash
cd website
npm install
npm run dev
```

## Build

```bash
npm run build
```

Output goes to `website/.vitepress/dist/`.

## Directory Layout

```
website/
├── index.md              # Product landing page (VitePress home layout)
├── .vitepress/
│   ├── config.mts         # VitePress configuration (nav, sidebar, base path)
│   └── theme/
│       ├── custom.css     # Design system (light/dark tokens)
│       └── index.ts       # Theme entry
├── guide/                 # Documentation pages
│   ├── getting-started.md
│   ├── architecture.md
│   ├── configuration.md
│   ├── rbac.md
│   ├── deployment.md
│   └── api-reference.md
└── public/
    └── logo.svg
```

## Deployment

Pushing to `main` with changes under `website/**` triggers the GitHub Actions workflow
(`.github/workflows/deploy-docs.yml`), which builds and deploys the site to GitHub Pages.

The site is served from the `/agent-forge/` base path (project repository name).
