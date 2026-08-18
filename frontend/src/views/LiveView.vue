<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { useStatsStore } from '../stores/stats'
import { chromeHidden } from '../fullscreen'
import { api } from '../api'
import StreamPlayer from '../components/StreamPlayer.vue'
import CountPanel from '../components/CountPanel.vue'
import SettingsDrawer from '../components/SettingsDrawer.vue'

const route = useRoute()
const stats = useStatsStore()

const settings = ref(null)
const showSettings = ref(
  new URLSearchParams(location.search).get('settings') === '1'
)
// ?video=0 — counts only, no live picture. A wall display costs the server
// nothing this way: no annotation, no JPEG encoding, no MJPEG connection.
const showVideo = new URLSearchParams(location.search).get('video') !== '0'
const selectedId = ref(null)
const events = ref([])
let eventsTimer = null

// URL ?camera=1 locks the page to a camera — open two windows, one per camera.
// Hash-history keeps its own query after '#', so read location.search too:
// http://host/?camera=2 must work, not just http://host/#/?camera=2.
let alive = true

onMounted(async () => {
  try {
    settings.value = await api.getSettings()
  } catch {
    settings.value = { cameras: [] }
  }
  if (!alive) return // navigated away mid-fetch: don't start an orphan timer
  const q = Number(
    new URLSearchParams(location.search).get('camera') ?? route.query.camera
  )
  const cams = settings.value.cameras ?? []
  selectedId.value = cams.some((c) => c.id === q) ? q : cams[0]?.id ?? null
  // The kiosk launcher finds each wall window by its title to place it on the
  // right monitor, so say which camera this window is showing.
  const shown = cams.find((c) => c.id === selectedId.value)
  if (shown) document.title = `Camera ${shown.id} ${shown.name} — People Counter`
  await loadEvents()
  if (!alive) return
  eventsTimer = setInterval(loadEvents, 10000)
})
onUnmounted(() => {
  alive = false
  clearInterval(eventsTimer)
})

async function loadEvents() {
  if (selectedId.value == null || document.hidden) return
  try {
    const rows = await api.recentEvents(selectedId.value, 8)
    if (alive) events.value = rows
  } catch {
    /* transient API error — keep the previous list rather than blanking it */
  }
}

watch(selectedId, (id) => {
  if (id != null) {
    // keep the real query string in sync (bookmarkable, survives refresh)
    const url = new URL(location.href)
    if (url.searchParams.get('camera') !== String(id)) {
      url.searchParams.set('camera', id)
      history.replaceState(null, '', url)
    }
  }
  loadEvents()
})

const camCfg = computed(
  () => settings.value?.cameras.find((c) => c.id === selectedId.value) ?? null
)
const camStats = computed(
  () =>
    stats.byId(selectedId.value) ?? {
      camera_id: selectedId.value,
      online: false,
      fps: 0,
      in: 0,
      out: 0,
      inside: 0,
      error: null,
    }
)

// The counts shown are always the site total — IN from the entrance camera,
// OUT from the exit camera — so both station screens and the cloud dashboard
// display the same numbers no matter which camera the screen is watching.
const shownCounts = computed(() => ({
  ...camStats.value,
  in: stats.site.in,
  out: stats.site.out,
  inside: stats.site.inside,
}))

function fmtTime(ts) {
  return ts.slice(11, 19)
}

const refreshToken = ref(0)

function closeSettings() {
  showSettings.value = false
  // strip ?settings=1 so a page refresh doesn't reopen the modal
  const url = new URL(location.href)
  if (url.searchParams.has('settings')) {
    url.searchParams.delete('settings')
    history.replaceState(null, '', url)
  }
}

async function onSaved() {
  try {
    settings.value = await api.getSettings()
  } catch {
    return
  }
  const cams = settings.value.cameras ?? []
  // the selected camera may have just been deleted — fall back instead of
  // leaving the page pointed at an id that no longer exists
  if (!cams.some((c) => c.id === selectedId.value)) {
    selectedId.value = cams[0]?.id ?? null
  }
  refreshToken.value++ // re-mount the stream so the new counting line shows
  loadEvents()
}
</script>

<template>
  <div class="live" :class="{ fill: chromeHidden }">
    <div v-if="!chromeHidden" class="toolbar">
      <label class="select-cam">
        <span class="muted">Camera</span>
        <select v-model.number="selectedId">
          <option v-for="c in settings?.cameras ?? []" :key="c.id" :value="c.id">
            {{ c.name }} {{ c.enabled ? '' : '(disabled)' }}
          </option>
        </select>
      </label>
      <button style="margin-left: auto" @click="showSettings = true">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 01-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06a1.65 1.65 0 001.82.33H9a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z" />
        </svg>
        Settings
      </button>
    </div>

    <div class="grid" :class="{ 'no-video': !showVideo }">
      <div class="main">
        <CountPanel :cam="shownCounts" :big="!showVideo" />
        <StreamPlayer
          v-if="selectedId != null && showVideo"
          class="stream"
          :camera-id="selectedId"
          :camera-name="camCfg?.name ?? ''"
          :online="camStats.online"
          :running="camCfg?.enabled ?? false"
          :fps="camStats.fps"
          :status-note="camStats.error"
          :refresh-token="refreshToken"
        />
      </div>

      <aside class="side card feed">
        <div class="feed-head">
          <h3>Recent Events</h3>
          <router-link class="muted" to="/dashboard">View all →</router-link>
        </div>
        <p v-if="!events.length" class="muted">No events today</p>
        <ul>
          <li v-for="(e, i) in events" :key="i">
            <span class="badge" :class="e.direction === 'IN' ? 'b-in' : 'b-out'">
              {{ e.direction === 'IN' ? '↓ In' : '↑ Out' }}
            </span>
            <span class="time">{{ fmtTime(e.timestamp) }}</span>
            <a v-if="e.snapshot" :href="`/snapshots/${e.snapshot}`" target="_blank">Photo</a>
          </li>
        </ul>
      </aside>
    </div>

    <SettingsDrawer
      v-if="showSettings"
      @close="closeSettings"
      @saved="onSaved"
    />
  </div>
</template>

<style scoped>
/* Viewport-fit always: the page never scrolls, the stream absorbs the
   remaining height at every window size. */
.live {
  display: flex;
  flex-direction: column;
  gap: 14px;
  height: calc(100vh - 128px); /* topbar + margins */
  min-height: 320px;
}
.live.fill { height: calc(100vh - 66px); }
.toolbar { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
.select-cam { display: flex; align-items: center; gap: 10px; }
.select-cam select { width: auto; min-width: 240px; }

.grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) clamp(240px, 21vw, 360px);
  gap: 16px;
  align-items: stretch;
  flex: 1;
  min-height: 0;
}
.main {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
  min-height: 0;
}
.main :deep(.player) { flex: 1; min-height: 0; }
/* counts-only wall display: the three tiles fill the screen */
.grid.no-video { grid-template-columns: minmax(0, 1fr); }
.grid.no-video .side { display: none; }
.grid.no-video .main :deep(.counts) { flex: 1; align-items: stretch; }
.grid.no-video .main :deep(.stat) { flex-direction: column; justify-content: center; gap: 4px; }
.grid.no-video .main :deep(.icon) { display: none; }
.grid.no-video .main :deep(.label) { font-size: clamp(14px, 1.8vw, 28px); }
.grid.no-video .main :deep(.value) { font-size: clamp(60px, 13vw, 220px); }
/* events log stretches to the same height as stats + stream */
.side {
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.feed ul { flex: 1; min-height: 0; overflow-y: auto; }
.time { font-family: var(--font-mono); }

@media (max-width: 900px) {
  .grid { grid-template-columns: 1fr; grid-template-rows: 1fr auto; }
  .side { max-height: 30vh; }
}

.feed h3 { font-size: 14px; font-weight: 600; }
.feed-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.feed-head a { text-decoration: none; }
.feed-head a:hover { color: var(--brand-blue); }
.feed ul { list-style: none; display: flex; flex-direction: column; gap: 8px; }
.feed li {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13.5px;
  padding: 7px 10px;
  background: var(--bg-elevated);
  border-radius: 9px;
}
.b-in { background: rgba(16, 185, 129, 0.14); color: var(--green); }
.b-out { background: rgba(244, 63, 94, 0.14); color: var(--red); }
.time { color: var(--text-dim); font-variant-numeric: tabular-nums; }
.feed a { margin-left: auto; color: var(--brand-blue); font-size: 12.5px; }

</style>
