<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
import LineEditor from './LineEditor.vue'

const emit = defineEmits(['close', 'saved'])

const cfg = ref(null)
const saving = ref(false)
const message = ref('')
const editingLineCam = ref(null)
const tab = ref('cameras')

const TABS = [
  { id: 'cameras', label: 'Cameras' },
  { id: 'counting', label: 'Counting' },
  { id: 'display', label: 'Display' },
  { id: 'server', label: 'Server' },
  { id: 'system', label: 'System' },
]

// Every section the template binds to must exist, or one missing key in a
// hand-edited config blanks the whole modal.
function withDefaults(c) {
  c.cameras ??= []
  c.model ??= {}
  c.model.imgsz ??= 416
  c.model.device ??= 'CPU'
  c.jpeg_quality ??= 70
  c.snapshots ??= {}
  c.snapshots.enabled ??= false
  c.snapshots.keep_days ??= 7
  c.tracking ??= {}
  c.tracking.lost_seconds ??= 3
  c.upload ??= {}
  c.upload.enabled ??= false
  c.upload.base_url ??= 'http://10.1.100.200'
  c.upload.interval_s ??= 60
  c.upload.api_keys ??= {}
  for (const cam of c.cameras) c.upload.api_keys[cam.id] ??= ''
  c.counting ??= {}
  c.counting.cooldown_s ??= 2.0
  c.counting.deadband_frac ??= 0.008
  c.counting.margin_frac ??= 0.15
  c.counting.min_track_age ??= 2
  for (const cam of c.cameras) cam.count_mode ??= 'both'
  return c
}

const devices = ref([{ id: 'CPU', name: 'CPU' }])
const uploadStatus = ref({})

async function refreshUpload() {
  try {
    uploadStatus.value = await api.uploadStatus()
  } catch {
    uploadStatus.value = { last_error: 'could not read status' }
  }
}

async function load() {
  message.value = ''
  try {
    cfg.value = withDefaults(await api.getSettings())
  } catch (e) {
    message.value = `Could not load settings: ${e.message}`
    return
  }
  try {
    devices.value = await api.getDevices()
  } catch {
    /* keep the CPU-only fallback list */
  }
  refreshUpload()
}

onMounted(load)

async function save() {
  if (!cfg.value || saving.value) return
  saving.value = true
  message.value = ''
  try {
    await api.saveSettings(cfg.value)
    message.value = 'Saved — cameras are restarting'
    emit('saved')
    // re-read so this modal never keeps saving a stale snapshot over
    // whatever another window changed in the meantime
    cfg.value = withDefaults(await api.getSettings())
  } catch (e) {
    // strip the FastAPI JSON envelope so the user sees the plain reason
    const m = /"detail":"([^"]+)"/.exec(e.message)
    message.value = m ? m[1] : `Error: ${e.message}`
  } finally {
    saving.value = false
  }
}

function addCamera() {
  // never reuse a freed id: events in the database are keyed by camera_id, so
  // a recycled id would inherit the deleted camera's history
  const used = cfg.value.cameras.map((c) => c.id)
  const nextId = Math.max(0, cfg.value.next_camera_id ?? 0, ...used) + 1
  cfg.value.next_camera_id = nextId
  editingLineCam.value = null
  cfg.value.cameras.push({
    id: nextId,
    name: `Camera ${nextId}`,
    source: '',
    enabled: false,
    line: { x1: 0.1, y1: 0.5, x2: 0.9, y2: 0.5 },
    invert_direction: false,
    count_mode: 'both',
    detect_every_n: 3,
    conf: 0.35,
  })
}

function removeCamera(i) {
  const cam = cfg.value.cameras[i]
  if (!confirm(`Remove "${cam.name}"? Its recorded history stays in the database.`)) {
    return
  }
  cfg.value.next_camera_id = Math.max(
    cfg.value.next_camera_id ?? 0,
    ...cfg.value.cameras.map((c) => c.id)
  )
  if (editingLineCam.value === cam.id) editingLineCam.value = null
  cfg.value.cameras.splice(i, 1)
}

function openDisplay(camId, kiosk) {
  const url = `${location.origin}/?camera=${camId}${kiosk ? '&kiosk=1' : ''}`
  window.open(url, `display-cam-${camId}`, 'noopener')
}
</script>

<template>
  <div class="backdrop" @click.self="emit('close')">
    <div class="modal">
      <header>
        <h2>Settings</h2>
        <button class="icon-btn plain" @click="emit('close')">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <path d="M18 6L6 18M6 6l12 12" />
          </svg>
        </button>
      </header>

      <nav class="tabs">
        <button
          v-for="t in TABS" :key="t.id"
          class="tab" :class="{ active: tab === t.id }"
          @click="tab = t.id"
        >{{ t.label }}</button>
      </nav>

      <div class="body" v-if="cfg">
        <!-- ============ CAMERAS ============ -->
        <template v-if="tab === 'cameras'">
          <section v-for="(cam, i) in cfg.cameras" :key="cam.id" class="block">
            <div class="row head">
              <span class="cam-id">#{{ cam.id }}</span>
              <input class="cam-name" v-model="cam.name" placeholder="Camera name" />
              <label class="switch">
                <input type="checkbox" v-model="cam.enabled" /><i></i>
              </label>
              <button class="icon-btn plain danger-text" title="Remove" @click="removeCamera(i)">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M3 6h18M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2m3 0v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6" />
                </svg>
              </button>
            </div>
            <label>
              RTSP URL
              <input v-model="cam.source" placeholder="rtsp://user:pass@ip:554/stream2" />
            </label>
            <div class="grid-3">
              <label>
                Count mode
                <select v-model="cam.count_mode">
                  <option value="both">Both directions</option>
                  <option value="in_only">In only</option>
                  <option value="out_only">Out only</option>
                </select>
              </label>
              <label>
                Confidence — {{ cam.conf }}
                <input type="range" min="0.1" max="0.9" step="0.05" v-model.number="cam.conf" />
              </label>
              <label>
                Detect every {{ cam.detect_every_n }} frames
                <input type="range" min="1" max="10" v-model.number="cam.detect_every_n" />
              </label>
            </div>
            <button class="subtle" @click="editingLineCam = editingLineCam === cam.id ? null : cam.id">
              {{ editingLineCam === cam.id ? 'Hide line editor' : 'Edit counting line' }}
            </button>
            <LineEditor
              v-if="editingLineCam === cam.id"
              :camera-id="cam.id"
              :line="cam.line"
              :invert="cam.invert_direction"
              :count-mode="cam.count_mode"
              @update:line="cam.line = $event"
              @update:invert="cam.invert_direction = $event"
            />
          </section>
          <button class="subtle" @click="addCamera">+ Add camera</button>
        </template>

        <!-- ============ COUNTING ============ -->
        <template v-if="tab === 'counting'">
          <section class="block">
            <p class="muted">Applies to every camera. Defaults suit walking speed.</p>
            <div class="grid-2">
              <label>
                Re-count cooldown — {{ cfg.counting.cooldown_s }}s
                <input type="range" min="0.5" max="10" step="0.5" v-model.number="cfg.counting.cooldown_s" />
              </label>
              <label>
                Jitter deadband — {{ (cfg.counting.deadband_frac * 100).toFixed(1) }}%
                <input type="range" min="0.002" max="0.03" step="0.001" v-model.number="cfg.counting.deadband_frac" />
              </label>
              <label>
                Line end margin — {{ (cfg.counting.margin_frac * 100).toFixed(0) }}%
                <input type="range" min="0" max="0.5" step="0.05" v-model.number="cfg.counting.margin_frac" />
              </label>
              <label>
                Minimum track age — {{ cfg.counting.min_track_age }} updates
                <input type="range" min="1" max="10" v-model.number="cfg.counting.min_track_age" />
              </label>
              <label>
                Track memory — {{ cfg.tracking.lost_seconds }}s
                <input type="range" min="1" max="10" step="0.5" v-model.number="cfg.tracking.lost_seconds" />
              </label>
            </div>
          </section>
        </template>

        <!-- ============ DISPLAY ============ -->
        <template v-if="tab === 'display'">
          <section class="block">
            <p class="muted">
              Open a dedicated window per camera. Kiosk hides all toolbars —
              press Esc to leave fullscreen.
            </p>
            <div v-for="cam in cfg.cameras" :key="cam.id" class="row display-row">
              <span class="disp-name">{{ cam.name }}</span>
              <button class="subtle" @click="openDisplay(cam.id, false)">Window</button>
              <button class="subtle" @click="openDisplay(cam.id, true)">Kiosk</button>
            </div>
          </section>
          <section class="block">
            <p class="muted">Direct URLs for station PCs (add &theme=light or &theme=dark to force a theme):</p>
            <code v-for="cam in cfg.cameras" :key="cam.id">
              http://&lt;server-ip&gt;:8000/?camera={{ cam.id }}&amp;kiosk=1
            </code>
          </section>
        </template>

        <!-- ============ SERVER (FactoryBox upload) ============ -->
        <template v-if="tab === 'server'">
          <section class="block">
            <label class="switch labeled">
              <input type="checkbox" v-model="cfg.upload.enabled" /><i></i>
              Send counts to the FactoryBox dashboard
            </label>
            <p class="muted">
              Numbers only — no images are ever uploaded. If the server or the
              network is down the counts are buffered on disk and sent later.
            </p>
            <div class="grid-2">
              <label>
                Server URL
                <input v-model="cfg.upload.base_url" placeholder="http://10.1.100.200" />
              </label>
              <label>
                Send every {{ cfg.upload.interval_s }} s
                <input type="range" min="10" max="600" step="10" v-model.number="cfg.upload.interval_s" />
              </label>
            </div>
            <label v-for="cam in cfg.cameras" :key="cam.id">
              API key — {{ cam.name }}
              <input v-model="cfg.upload.api_keys[cam.id]" placeholder="fbx_..." />
            </label>
          </section>
          <section class="block">
            <h4>Status</h4>
            <div class="grid-2">
              <span class="muted">Sent: <b>{{ uploadStatus.sent ?? 0 }}</b></span>
              <span class="muted">Waiting to send: <b>{{ uploadStatus.queued ?? 0 }}</b></span>
              <span class="muted">Last success: <b>{{ uploadStatus.last_ok || '-' }}</b></span>
              <span class="muted">Last error: <b>{{ uploadStatus.last_error || 'none' }}</b></span>
            </div>
            <button class="subtle" style="max-width:160px" @click="refreshUpload">Refresh</button>
          </section>
        </template>

        <!-- ============ SYSTEM ============ -->
        <template v-if="tab === 'system'">
          <section class="block">
            <div class="grid-2">
              <label>
                Processing device
                <select v-model="cfg.model.device">
                  <option v-for="d in devices" :key="d.id" :value="d.id">
                    {{ d.id }} — {{ d.name }}
                  </option>
                </select>
                <span class="hint">
                  On low-power PCs the integrated GPU is often faster and frees the CPU
                </span>
              </label>
              <label>
                Inference size
                <select v-model.number="cfg.model.imgsz">
                  <option :value="320">320 — fastest</option>
                  <option :value="416">416 — balanced</option>
                  <option :value="640">640 — most accurate</option>
                </select>
              </label>
              <label>
                Stream JPEG quality — {{ cfg.jpeg_quality }}
                <input type="range" min="40" max="95" v-model.number="cfg.jpeg_quality" />
              </label>
            </div>
          </section>
          <section class="block">
            <label class="switch labeled">
              <input type="checkbox" v-model="cfg.snapshots.enabled" /><i></i>
              Save a photo on every count (audit trail)
            </label>
            <label style="max-width: 200px">
              Keep snapshots (days)
              <input type="number" min="1" max="90" v-model.number="cfg.snapshots.keep_days" />
            </label>
          </section>
        </template>
      </div>
      <div v-else class="body muted">
        <p>{{ message || 'Loading...' }}</p>
        <button v-if="message" class="subtle" style="max-width: 140px" @click="load">
          Try again
        </button>
      </div>

      <footer>
        <span class="muted">{{ message }}</span>
        <button class="plain" @click="emit('close')">Cancel</button>
        <button class="save" :disabled="saving || !cfg" @click="save">
          {{ saving ? 'Saving...' : 'Save & apply' }}
        </button>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.backdrop {
  position: fixed;
  inset: 0;
  background: rgba(3, 7, 18, 0.62);
  z-index: 50;
  display: grid;
  place-items: center;
  padding: 3vh 3vw;
}
.modal {
  /* fixed frame — every tab shows the same window, only the content changes */
  width: min(760px, 100%);
  height: min(680px, 92vh);
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 12px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: 0 24px 70px rgba(0, 0, 0, 0.45);
}

header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px 10px;
}
h2 { font-size: 16px; font-weight: 600; letter-spacing: 0.01em; }

.tabs {
  display: flex;
  gap: 2px;
  padding: 0 20px;
  border-bottom: 1px solid var(--border);
}
.tab {
  background: none;
  border: none;
  border-radius: 0;
  padding: 9px 14px;
  color: var(--text-dim);
  font-size: 13.5px;
  font-weight: 500;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
}
.tab.active {
  color: var(--text);
  border-bottom-color: var(--brand-blue);
}
.tab:hover { border-color: transparent; border-bottom-color: var(--border-bright); }
.tab.active:hover { border-bottom-color: var(--brand-blue); }

.body {
  padding: 18px 20px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
  flex: 1;
  min-height: 0;
}
footer { margin-top: auto; }

.block {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border);
}
.block:last-child { border-bottom: none; padding-bottom: 0; }

.row { display: flex; align-items: center; gap: 10px; }
.row.head .cam-name { flex: 1; font-weight: 500; }
.cam-id { color: var(--text-dim); font-family: var(--font-mono); font-size: 13px; }

label {
  display: flex;
  flex-direction: column;
  gap: 5px;
  font-size: 12.5px;
  font-weight: 500;
  color: var(--text-dim);
}
.grid-2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px 18px; }
.grid-3 { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px 18px; }

.display-row { padding: 6px 0; }
.disp-name { flex: 1; font-weight: 500; }
code {
  display: block;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px 12px;
  font-size: 12px;
  font-family: var(--font-mono);
  word-break: break-all;
}

/* flat, quiet buttons */
button.subtle {
  background: transparent;
  border: 1px solid var(--border-bright);
  font-size: 13px;
  justify-content: center;
}
button.plain { background: transparent; border: none; }
button.plain:hover { color: var(--text); }
.danger-text { color: var(--text-dim); }
.danger-text:hover { color: var(--red); border: none; }
.switch.labeled { flex-direction: row; align-items: center; color: var(--text); font-size: 13.5px; }

footer {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 20px;
  border-top: 1px solid var(--border);
}
footer .muted { flex: 1; }
button.save {
  background: var(--brand-blue);
  border: none;
  color: #fff;
  font-weight: 600;
  padding: 8px 22px;
}
button.save:disabled { opacity: 0.6; }
</style>
