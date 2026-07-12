import type { App } from 'vue'
import DefaultTheme from 'vitepress/theme'
import './custom.css'
import HomeBand from './components/HomeBand.vue'

export default {
  extends: DefaultTheme,
  enhanceApp({ app }: { app: App }) {
    app.component('HomeBand', HomeBand)
  },
}
