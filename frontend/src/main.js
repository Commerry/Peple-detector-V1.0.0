import { createApp } from 'vue'
import { createPinia } from 'pinia'
import '@fontsource/geist-sans/400.css'
import '@fontsource/geist-sans/500.css'
import '@fontsource/geist-sans/600.css'
import '@fontsource/geist-sans/700.css'
import '@fontsource/geist-mono/500.css'
import '@fontsource/geist-mono/600.css'
import App from './App.vue'
import router from './router'
import { applyTheme } from './theme'
import './styles.css'

applyTheme()
createApp(App).use(createPinia()).use(router).mount('#app')
