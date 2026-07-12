import { defineConfig } from 'vitepress'

export default defineConfig({
  base: '/agent-forge/',
  lang: 'en-US',
  title: 'Agent Forge',
  description:
    'Self-hostable, multi-tenant AI agent platform with RBAC, observability, quota management, and an admin dashboard.',
  head: [
    ['link', { rel: 'icon', href: '/agent-forge/logo.svg', type: 'image/svg+xml' }],
    ['link', { rel: 'preconnect', href: 'https://fonts.googleapis.com' }],
    [
      'link',
      {
        rel: 'preconnect',
        href: 'https://fonts.gstatic.com',
        crossorigin: '',
      },
    ],
    [
      'link',
      {
        href: 'https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700;800&family=Geist+Mono:wght@400;500;600;700&display=swap',
        rel: 'stylesheet',
      },
    ],
  ],
  themeConfig: {
    logo: {
      src: '/logo.svg',
      width: 28,
      height: 28,
    },
    nav: [
      { text: 'Home', link: '/' },
      { text: 'Guide', link: '/guide/getting-started' },
      {
        text: 'GitHub',
        link: 'https://github.com/chingjustwe/agent-forge',
      },
    ],
    sidebar: {
      '/guide/': [
        {
          text: 'Guide',
          items: [
            { text: 'Getting Started', link: '/guide/getting-started' },
            { text: 'Architecture', link: '/guide/architecture' },
            { text: 'Configuration', link: '/guide/configuration' },
            { text: 'RBAC & Permissions', link: '/guide/rbac' },
            { text: 'Deployment', link: '/guide/deployment' },
            { text: 'API Reference', link: '/guide/api-reference' },
          ],
        },
      ],
    },
    socialLinks: [
      {
        icon: 'github',
        link: 'https://github.com/chingjustwe/agent-forge',
      },
    ],
    footer: {
      message: 'Released under the MIT License.',
      copyright: `Copyright © ${new Date().getFullYear()} Agent Forge`,
    },
  },
})
