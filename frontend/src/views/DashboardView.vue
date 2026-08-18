<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { api } from '../api'
import BarChart from '../components/BarChart.vue'

const settings = ref(null)
const cameraId = ref(null) // null = all cameras
// local date, not UTC — toISOString() shows yesterday before ~07:00 ICT
const day = ref(new Date().toLocaleDateString('sv-SE'))
const hourly = ref([])
const daily = ref([])
const events = ref([])
const showTable = ref(true)

const error = ref('')
const loading = ref(false)
let reqSeq = 0

async function refresh() {
  const cid = cameraId.value
  const seq = ++reqSeq
  loading.value = true
  try {
    const [h, d, e] = await Promise.all([
      api.hourly(day.value, cid),
      api.daily(14, cid),
      api.recentEvents(cid, 20),
    ])
    if (seq !== reqSeq) return // a newer request already answered
    hourly.value = h
    daily.value = d
    events.value = e
    error.value = ''
  } catch (err) {
    if (seq !== reqSeq) return
    // never leave the previous day's numbers under a new date label
    hourly.value = []
    daily.value = []
    events.value = []
    error.value = `Could not load data: ${err.message}`
  } finally {
    if (seq === reqSeq) loading.value = false
  }
}

onMounted(async () => {
  try {
    settings.value = await api.getSettings()
  } catch {
    settings.value = { cameras: [] }
  }
  await refresh()
})
watch([cameraId, day], refresh)

const hourlyBuckets = computed(() =>
  hourly.value.map((h) => ({ label: `${h.hour}`, in: h.in, out: h.out }))
)
// The API omits days with no events; render the full 14-day range so the axis
// stays evenly spaced and a closed weekend reads as a gap, not as missing time.
const dailyBuckets = computed(() => {
  const found = new Map(daily.value.map((d) => [d.date, d]))
  const end = new Date(`${day.value}T00:00:00`)
  if (Number.isNaN(end.getTime())) return []
  const out = []
  for (let i = 13; i >= 0; i--) {
    const d = new Date(end)
    d.setDate(end.getDate() - i)
    const key = d.toLocaleDateString('sv-SE')
    const hit = found.get(key)
    out.push({ label: key.slice(5), in: hit?.in ?? 0, out: hit?.out ?? 0 })
  }
  return out
})
const totals = computed(() =>
  hourly.value.reduce(
    (acc, h) => ({ in: acc.in + h.in, out: acc.out + h.out }),
    { in: 0, out: 0 }
  )
)

// Export exactly the 14-day window the charts show, ending on the selected
// day — deriving the start from the data made older dates export nothing.
function exportFile(kind) {
  const end = new Date(`${day.value}T00:00:00`)
  if (Number.isNaN(end.getTime())) return
  const start = new Date(end)
  start.setDate(end.getDate() - 13)
  window.open(
    api.exportUrl(start.toLocaleDateString('sv-SE'), day.value, cameraId.value, kind),
    '_blank'
  )
}
</script>

<template>
  <div class="dash">
    <div class="filters">
      <label>
        Camera
        <select v-model="cameraId">
          <option :value="null">All cameras</option>
          <option v-for="c in settings?.cameras ?? []" :key="c.id" :value="c.id">
            {{ c.name }}
          </option>
        </select>
      </label>
      <label>
        Date
        <input type="date" v-model="day" />
      </label>
      <button style="margin-left: auto" @click="exportFile('xlsx')">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M15 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V7zM15 2v5h5M9 13l6 5M15 13l-6 5" />
        </svg>
        Export Excel
      </button>
      <button class="ghost" @click="exportFile('csv')">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3" />
        </svg>
        CSV
      </button>
    </div>

    <p v-if="error" class="banner">{{ error }}</p>

    <div class="tiles">
      <div class="card tile t-in">
        <span class="label">In ({{ day }})</span>
        <span class="big">{{ totals.in }}</span>
      </div>
      <div class="card tile t-out">
        <span class="label">Out ({{ day }})</span>
        <span class="big">{{ totals.out }}</span>
      </div>
      <div class="card tile t-inside">
        <span class="label">Occupancy</span>
        <span class="big">{{ Math.max(0, totals.in - totals.out) }}</span>
      </div>
    </div>

    <BarChart :buckets="hourlyBuckets" :title="`Hourly — ${day}`" />
    <BarChart :buckets="dailyBuckets" title="Last 14 days" />

    <div class="card">
      <div class="head">
        <h3>Recent Events</h3>
        <button @click="showTable = !showTable">
          {{ showTable ? 'Hide' : 'Show' }}
        </button>
      </div>
      <table v-if="showTable">
        <thead>
          <tr>
            <th>Time</th><th>Camera</th><th>Direction</th><th>Track</th><th>Snapshot</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(e, i) in events" :key="i">
            <td>{{ e.timestamp.replace('T', ' ') }}</td>
            <td>{{ e.camera_id }}</td>
            <td :class="e.direction === 'IN' ? 'dir-in' : 'dir-out'">
              {{ e.direction === 'IN' ? 'In' : 'Out' }}
            </td>
            <td>#{{ e.track_id }}</td>
            <td>
              <a v-if="e.snapshot" :href="`/snapshots/${e.snapshot}`" target="_blank">View</a>
              <span v-else class="muted">-</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.dash { display: flex; flex-direction: column; gap: 14px; }
.banner {
  background: rgba(244, 63, 94, 0.12);
  border: 1px solid rgba(244, 63, 94, 0.35);
  color: var(--red);
  border-radius: 10px;
  padding: 10px 14px;
  font-size: 13.5px;
}
.filters { display: flex; gap: 16px; align-items: flex-end; flex-wrap: wrap; }
/* the raw-rows download sits beside the report as the quieter option */
.filters button.ghost {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-dim);
}
.filters button.ghost:hover { color: var(--text); border-color: var(--brand-blue); }
.filters label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; font-weight: 500; color: var(--text-dim); }
.filters select, .filters input { width: auto; min-width: 180px; }
.tiles { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.tile {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 4px;
  text-align: center;
  overflow: hidden;
}
.tile::before {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  top: 0;
  height: 3px;
}
.label {
  color: var(--text-dim);
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.6px;
}
.t-in::before { background: var(--green); }
.t-out::before { background: var(--red); }
.t-inside::before { background: var(--purple); }
.t-in .big { color: var(--green); }
.t-out .big { color: var(--red); }
.t-inside .big { color: var(--purple); }
.big {
  font-family: var(--font-display);
  font-size: clamp(26px, 2.4vw, 40px);
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.02em;
}
.head { display: flex; justify-content: space-between; align-items: center; }
h3 { font-size: 14px; font-weight: 600; }
table { width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 14px; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); }
th { color: var(--text-dim); font-weight: 500; font-size: 13px; }
.dir-in { color: var(--chart-in); font-weight: 600; }
.dir-out { color: var(--chart-out); font-weight: 600; }
a { color: var(--brand-blue); }
</style>
