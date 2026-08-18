import { createRouter, createWebHashHistory } from 'vue-router'
import LiveView from './views/LiveView.vue'
import DashboardView from './views/DashboardView.vue'

export default createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', component: LiveView },
    { path: '/dashboard', component: DashboardView },
  ],
})
