<script setup>
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'

const props = defineProps({
  cameraId: { type: Number, required: true },
  cameraName: { type: String, default: '' },
  online: { type: Boolean, default: false },
  running: { type: Boolean, default: false },
  fps: { type: Number, default: 0 },
  statusNote: { type: String, default: null }, // worker-reported problem
  refreshToken: { type: Number, default: 0 }, // parent bumps this after Save
})

const imgEl = ref(null)
const reload = ref(0)
let retryTimer = null
let alive = true

// Any of these means "connect a fresh stream": camera switch, settings saved,
// error retry, or the worker coming back online after a restart.
const streamKey = computed(
  () => `${props.cameraId}-${props.refreshToken}-${reload.value}`
)

function detach() {
  // Detaching or re-keying the <img> does not reliably abort a
  // multipart/x-mixed-replace response; clearing src does. Without this the
  // server keeps counting us as a viewer and keeps annotating + encoding.
  if (imgEl.value) imgEl.value.src = ''
}

async function attach() {
  detach()
  await nextTick()
  if (!alive || !imgEl.value || !props.running || document.hidden) return
  imgEl.value.src = `/video/${props.cameraId}?r=${streamKey.value}`
}

function onImgError() {
  if (!alive) return
  clearTimeout(retryTimer)
  retryTimer = setTimeout(() => reload.value++, 3000)
}

function onVisibility() {
  if (document.hidden) detach()   // idle tab must not burn server CPU
  else reload.value++
}

watch(streamKey, attach)
watch(
  () => props.running,
  (r) => (r ? attach() : detach())
)
watch(
  () => props.online,
  (now, before) => {
    if (now && !before) reload.value++ // worker restarted -> reconnect
  }
)

onMounted(() => {
  document.addEventListener('visibilitychange', onVisibility)
  attach()
})
onUnmounted(() => {
  alive = false
  clearTimeout(retryTimer)
  document.removeEventListener('visibilitychange', onVisibility)
  detach()
})
</script>

<template>
  <div class="player">
    <img v-show="running" ref="imgEl" alt="live stream" @error="onImgError" />
    <div v-if="!running" class="placeholder">
      <svg width="46" height="46" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6">
        <rect x="2" y="5" width="20" height="14" rx="3" />
        <circle cx="12" cy="12" r="3" />
        <path d="M3 4l18 16" stroke-linecap="round" />
      </svg>
      <p>Camera disabled</p>
      <p class="muted">Enable it in Settings</p>
    </div>

    <div v-if="running" class="hud">
      <span class="badge live" :class="{ off: !online }">
        <span class="pulse" v-if="online"></span>
        {{ online ? 'LIVE' : 'OFFLINE' }}
      </span>
      <span class="badge name">{{ cameraName }}</span>
      <span v-if="online" class="badge fps">{{ fps }} FPS</span>
    </div>

    <div v-if="running && !online" class="overlay">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
        <path d="M12 9v4M12 17h.01" />
      </svg>
      <span>{{ statusNote || 'Camera offline — reconnecting...' }}</span>
    </div>
  </div>
</template>

<style scoped>
.player {
  /* fills whatever space the parent gives it; img letterboxes via contain */
  position: relative;
  background: #09090b;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
  width: 100%;
  height: 100%;
  min-height: 0;
  box-shadow: var(--shadow);
}
img { width: 100%; height: 100%; object-fit: contain; display: block; }
.placeholder {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--text-dim);
}
.hud {
  position: absolute;
  top: 12px;
  left: 12px;
  right: 12px;
  display: flex;
  gap: 8px;
  align-items: center;
  pointer-events: none;
}
.badge.live { background: rgba(225, 29, 72, 0.92); color: #fff; }
.badge.live.off { background: rgba(107, 114, 128, 0.85); color: #fff; }
.pulse {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #fff;
  animation: pulse 1.6s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.25; }
}
.badge.name { background: rgba(15, 23, 42, 0.72); color: #f9fafb; }
.badge.fps { margin-left: auto; background: rgba(15, 23, 42, 0.72); color: #6ee7b7; }
.overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  background: rgba(9, 9, 11, 0.78);
  color: var(--amber);
  font-weight: 600;
}
</style>
