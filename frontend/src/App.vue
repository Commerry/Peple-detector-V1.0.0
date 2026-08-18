<script setup>
import { onMounted } from 'vue'
import { useStatsStore } from './stores/stats'
import { theme, toggleTheme } from './theme'
import { chromeHidden, isFullscreen, toggleFullscreen } from './fullscreen'
import HealthBar from './components/HealthBar.vue'
import logoUrl from './logo/logo.png'

const stats = useStatsStore()
onMounted(() => stats.connect())
</script>

<template>
  <div class="layout" :class="{ kiosk: chromeHidden }">
    <!-- Floating logo when chrome is hidden (fullscreen / kiosk) -->
    <div v-if="chromeHidden" class="float-logo">
      <img :src="logoUrl" alt="Factory Box" />
    </div>
    <button
      v-if="chromeHidden"
      class="float-exit icon-btn"
      :title="isFullscreen ? 'Exit fullscreen (Esc)' : 'Enter fullscreen'"
      @click="toggleFullscreen"
    >
      <svg v-if="isFullscreen" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M8 3v3a2 2 0 01-2 2H3m18 0h-3a2 2 0 01-2-2V3m0 18v-3a2 2 0 012-2h3M3 16h3a2 2 0 012 2v3" />
      </svg>
      <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M8 3H5a2 2 0 00-2 2v3m18 0V5a2 2 0 00-2-2h-3m0 18h3a2 2 0 002-2v-3M3 16v3a2 2 0 002 2h3" />
      </svg>
    </button>

    <header v-if="!chromeHidden" class="topbar">
      <div class="brand">
        <img class="logo-img" :src="logoUrl" alt="Factory Box" />
        <span class="sub">People Counter</span>
      </div>

      <nav>
        <router-link to="/">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <rect x="2" y="5" width="20" height="14" rx="3" /><circle cx="12" cy="12" r="3" />
          </svg>
          Live View
        </router-link>
        <router-link to="/dashboard">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />
          </svg>
          Dashboard
        </router-link>
      </nav>

      <div class="right">
        <HealthBar />
        <button class="icon-btn" :title="theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'" @click="toggleTheme">
          <svg v-if="theme === 'dark'" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <circle cx="12" cy="12" r="4" />
            <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
          </svg>
          <svg v-else width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z" />
          </svg>
        </button>
        <button class="icon-btn" title="Fullscreen — toolbars hide, Esc to exit" @click="toggleFullscreen">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M8 3H5a2 2 0 00-2 2v3m18 0V5a2 2 0 00-2-2h-3m0 18h3a2 2 0 002-2v-3M3 16v3a2 2 0 002 2h3" />
          </svg>
        </button>
      </div>
    </header>
    <main>
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.layout {
  max-width: 1600px;
  margin: 0 auto;
  padding: 0 clamp(12px, 2vw, 24px) 40px;
}
.layout.kiosk {
  padding-top: 54px;
  padding-bottom: 0;
  max-width: none;
  overflow: hidden;
  height: 100vh;
}

.float-logo {
  position: fixed;
  top: 14px;
  left: 18px;
  z-index: 40;
  pointer-events: none;
}
.float-logo img {
  height: 30px;
  opacity: 0.95;
  filter: var(--logo-filter);
}
.float-exit {
  position: fixed;
  top: 12px;
  right: 14px;
  z-index: 40;
  opacity: 0.35;
}
.float-exit:hover { opacity: 1; }

.topbar {
  display: flex;
  align-items: center;
  gap: clamp(12px, 2vw, 26px);
  padding: 14px 0;
  border-bottom: 1px solid var(--border);
  margin-bottom: 20px;
  flex-wrap: wrap;
}
.brand { display: flex; align-items: center; gap: 12px; }
.logo-img { height: 34px; display: block; filter: var(--logo-filter); }
.sub {
  font-size: 12px;
  color: var(--text-dim);
  font-weight: 600;
  border-left: 1px solid var(--border-bright);
  padding-left: 12px;
}
nav {
  display: flex;
  gap: 4px;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 4px;
}
nav a {
  display: flex;
  align-items: center;
  gap: 7px;
  color: var(--text-dim);
  text-decoration: none;
  padding: 7px 16px;
  border-radius: 9px;
  font-size: 14px;
  font-weight: 600;
  font-family: var(--font-display);
}
nav a.router-link-active {
  color: #fff;
  background: var(--grad);
  box-shadow: 0 2px 12px rgba(99, 102, 241, 0.3);
}
.right { display: flex; align-items: center; gap: 8px; margin-left: auto; }
main { display: block; }

@media (max-width: 760px) {
  .right { margin-left: 0; width: 100%; justify-content: flex-start; }
}
</style>
