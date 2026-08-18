<script setup>
import { ref, computed, nextTick, onMounted, onUnmounted, watch } from 'vue'

// Counting-line editor: drag the two endpoints (or click empty space to move
// the nearest one). Green arrow shows which side counts as IN; the swap button
// flips it (bound to the camera's invert_direction).
const props = defineProps({
  cameraId: { type: Number, required: true },
  line: { type: Object, required: true },
  invert: { type: Boolean, default: false },
  countMode: { type: String, default: 'both' }, // both | in_only | out_only
})
const emit = defineEmits(['update:line', 'update:invert'])

const imgEl = ref(null)
const error = ref('')
const svgEl = ref(null)
const dragging = ref(null) // 'p1' | 'p2' | null
let retryTimer = null
let alive = true

function stopStream() {
  // release the MJPEG connection explicitly — detaching the <img> alone does
  // not reliably abort multipart/x-mixed-replace, and the server keeps
  // annotating + encoding for a viewer that is gone
  if (imgEl.value) imgEl.value.src = ''
}

// Probe the camera first: /api/frame fails fast (404 not running, 503 no frame
// within 5s) so the user gets a real message instead of a black box, then the
// live stream is attached for positioning.
async function loadFrame() {
  clearTimeout(retryTimer)
  error.value = ''
  try {
    const res = await fetch(`/api/frame/${props.cameraId}?t=${Date.now()}`)
    if (!alive) return
    if (res.status === 404) {
      error.value = 'Enable this camera and press Save & apply first'
      return
    }
    if (!res.ok) throw new Error(String(res.status))
    stopStream()
    await nextTick()
    if (imgEl.value) imgEl.value.src = `/video/${props.cameraId}?edit=${Date.now()}`
  } catch {
    if (!alive) return
    error.value = 'Waiting for camera...'
    retryTimer = setTimeout(loadFrame, 3000)
  }
}

function onImgError() {
  if (!alive) return
  error.value = 'Waiting for camera...'
  clearTimeout(retryTimer)
  retryTimer = setTimeout(loadFrame, 3000)
}

onMounted(loadFrame)
watch(() => props.cameraId, () => {
  stopStream()
  loadFrame()
})
onUnmounted(() => {
  alive = false
  clearTimeout(retryTimer)
  stopStream()
})

function toNorm(e) {
  const rect = svgEl.value.getBoundingClientRect()
  return {
    x: Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width)),
    y: Math.min(1, Math.max(0, (e.clientY - rect.top) / rect.height)),
  }
}

function startDrag(which, e) {
  dragging.value = which
  // capture the pointer so dragging past the frame edge keeps working —
  // without this the drag silently ends the moment the cursor leaves
  e.target.setPointerCapture?.(e.pointerId)
  e.preventDefault()
}

function onMove(e) {
  if (!dragging.value) return
  const p = toNorm(e)
  const ln = { ...props.line }
  if (dragging.value === 'p1') {
    ln.x1 = p.x
    ln.y1 = p.y
  } else {
    ln.x2 = p.x
    ln.y2 = p.y
  }
  emit('update:line', ln)
}

function stopDrag(e) {
  if (dragging.value) {
    dragging.value = null
    justDragged = true // suppress the click that follows the release
    setTimeout(() => (justDragged = false), 0)
  }
  e?.target?.releasePointerCapture?.(e.pointerId)
}

let justDragged = false

function onClickEmpty(e) {
  if (justDragged) return
  // move the nearest endpoint to the clicked position
  const p = toNorm(e)
  const d1 = (p.x - props.line.x1) ** 2 + (p.y - props.line.y1) ** 2
  const d2 = (p.x - props.line.x2) ** 2 + (p.y - props.line.y2) ** 2
  const ln = { ...props.line }
  if (d1 <= d2) {
    ln.x1 = p.x
    ln.y1 = p.y
  } else {
    ln.x2 = p.x
    ln.y2 = p.y
  }
  emit('update:line', ln)
}

// Both directions are drawn: crossing toward the IN arrow counts as entering,
// toward the OUT arrow as leaving. Whichever the camera does not count (per
// its count mode) is dimmed, so it is obvious which way this camera measures.
const arrows = computed(() => {
  const { x1, y1, x2, y2 } = props.line
  const mx = ((x1 + x2) / 2) * 100
  const my = ((y1 + y2) / 2) * 100
  let nx = -(y2 - y1)
  let ny = x2 - x1
  const len = Math.hypot(nx, ny) || 1
  const dir = props.invert ? -1 : 1
  const ux = (nx / len) * 13 * dir
  const uy = (ny / len) * 13 * dir
  return {
    mx,
    my,
    in: { tx: mx + ux, ty: my + uy },
    out: { tx: mx - ux, ty: my - uy },
  }
})

const countsIn = computed(() => props.countMode !== 'out_only')
const countsOut = computed(() => props.countMode !== 'in_only')
</script>

<template>
  <div class="editor">
    <div class="tools">
      <span class="muted">
        Drag the endpoints. Blue arrow = counted as IN, orange = counted as OUT.
        <template v-if="countMode === 'in_only'">This camera counts IN only.</template>
        <template v-else-if="countMode === 'out_only'">This camera counts OUT only.</template>
      </span>
      <button type="button" @click="emit('update:invert', !invert)">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M7 16V4M7 4L3 8M7 4l4 4M17 8v12m0 0l4-4m-4 4l-4-4" />
        </svg>
        Swap In/Out
      </button>
      <button type="button" @click="loadFrame">Refresh frame</button>
    </div>
    <div class="frame">
      <img ref="imgEl" @error="onImgError" alt="camera frame" />
      <div v-if="error" class="err">{{ error }}</div>
      <svg
        ref="svgEl"
        class="overlay"
        viewBox="0 0 100 100"
        preserveAspectRatio="none"
        @pointermove="onMove"
        @pointerup="stopDrag"
        @pointercancel="stopDrag"
        @click.self="onClickEmpty"
      >
        <line
          :x1="line.x1 * 100" :y1="line.y1 * 100"
          :x2="line.x2 * 100" :y2="line.y2 * 100"
          stroke="#10b981" stroke-width="0.8" vector-effect="non-scaling-stroke"
          style="stroke-width: 2.5px" pointer-events="none"
        />
        <!-- IN direction -->
        <g :opacity="countsIn ? 1 : 0.25" pointer-events="none">
          <line
            :x1="arrows.mx" :y1="arrows.my" :x2="arrows.in.tx" :y2="arrows.in.ty"
            stroke="#3b82f6" style="stroke-width: 2px" vector-effect="non-scaling-stroke"
          />
          <circle :cx="arrows.in.tx" :cy="arrows.in.ty" r="1.6" fill="#3b82f6" />
          <text
            :x="arrows.in.tx + 2.5" :y="arrows.in.ty + 1.5"
            fill="#3b82f6" font-size="4.5" font-weight="700"
          >IN</text>
        </g>
        <!-- OUT direction -->
        <g :opacity="countsOut ? 1 : 0.25" pointer-events="none">
          <line
            :x1="arrows.mx" :y1="arrows.my" :x2="arrows.out.tx" :y2="arrows.out.ty"
            stroke="#f97316" style="stroke-width: 2px" vector-effect="non-scaling-stroke"
          />
          <circle :cx="arrows.out.tx" :cy="arrows.out.ty" r="1.6" fill="#f97316" />
          <text
            :x="arrows.out.tx + 2.5" :y="arrows.out.ty + 1.5"
            fill="#f97316" font-size="4.5" font-weight="700"
          >OUT</text>
        </g>
        <circle
          class="handle" :cx="line.x1 * 100" :cy="line.y1 * 100" r="2.4"
          @pointerdown="startDrag('p1', $event)"
        />
        <circle
          class="handle" :cx="line.x2 * 100" :cy="line.y2 * 100" r="2.4"
          @pointerdown="startDrag('p2', $event)"
        />
      </svg>
    </div>
  </div>
</template>

<style scoped>
.tools {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.tools .muted { flex: 1; min-width: 160px; }
.frame {
  position: relative;
  aspect-ratio: 16 / 9;
  background: #09090b;
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
}
img { width: 100%; height: 100%; object-fit: fill; display: block; }
svg.overlay { position: absolute; inset: 0; width: 100%; height: 100%; cursor: crosshair; touch-action: none; }
.handle {
  fill: #f59e0b;
  stroke: #fff;
  stroke-width: 0.6;
  cursor: grab;
}
.handle:active { cursor: grabbing; }
.err {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--amber);
  padding: 20px;
  text-align: center;
  pointer-events: none;
}
</style>
