import { createRouter, createWebHistory } from 'vue-router'
import StartupView from '../views/StartupView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [{ path: '/', component: StartupView }],
})
