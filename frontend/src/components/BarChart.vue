<script setup>
import { ref, computed } from 'vue'

// Grouped bar chart, two series (In/Out), hand-rolled SVG — no chart lib.
// Series colors come from --chart-in / --chart-out theme vars; both palettes
// validated for CVD + contrast (dark #3987e5/#d95926, light #2a78d6/#eb6834).
const props = defineProps({
  buckets: { type: Array, required: true }, // [{ label, in, out }]
  title: { type: String, default: '' },
})

const W = 900
const H = 260
const PAD = { top: 14, right: 10, bottom: 26, left: 38 }

const maxVal = computed(() =>
  Math.max(1, ...props.buckets.flatMap((b) => [b.in, b.out]))
)

const yTicks = computed(() => {
  const m = maxVal.value
  const step = Math.max(1, Math.ceil(m / 4))
  const ticks = []
  for (let v = 0; v <= m; v += step) ticks.push(v)
  return ticks
})

const plotW = W - PAD.left - PAD.right
const plotH = H - PAD.top - PAD.bottom

function y(v) {
  return PAD.top + plotH - (v / maxVal.value) * plotH
}

const groups = computed(() => {
  const n = props.buckets.length
  const groupW = plotW / n
  const barW = Math.max(2, Math.min(14, groupW / 2 - 2))
  return props.buckets.map((b, i) => {
    const cx = PAD.left + groupW * i + groupW / 2
    return {
      ...b,
      i,
      labelX: cx,
      inX: cx - barW - 1,
      outX: cx + 1,
      barW,
      inY: y(b.in),
      outY: y(b.out),
    }
  })
})

const hover = ref(null)

function onHover(g, evt) {
  const svgRect = evt.currentTarget.ownerSVGElement.getBoundingClientRect()
  hover.value = {
    ...g,
    px: ((g.labelX / W) * svgRect.width),
    py: Math.min(g.inY, g.outY) / H * svgRect.height,
  }
}
</script>

<template>
  <div class="chart card">
    <div class="head">
      <h3>{{ title }}</h3>
      <div class="legend">
        <span><i class="sw sw-in"></i> In</span>
        <span><i class="sw sw-out"></i> Out</span>
      </div>
    </div>
    <div class="plot-wrap" @mouseleave="hover = null">
      <svg :viewBox="`0 0 ${W} ${H}`" preserveAspectRatio="none">
        <g v-for="t in yTicks" :key="t">
          <line
            :x1="PAD.left" :x2="W - PAD.right" :y1="y(t)" :y2="y(t)"
            class="grid-line"
          />
          <text :x="PAD.left - 6" :y="y(t) + 4" text-anchor="end" class="tick">
            {{ t }}
          </text>
        </g>
        <g v-for="g in groups" :key="g.i">
          <rect
            :x="g.inX" :y="g.inY" :width="g.barW"
            :height="PAD.top + plotH - g.inY"
            class="bar-in" rx="3"
          />
          <rect
            :x="g.outX" :y="g.outY" :width="g.barW"
            :height="PAD.top + plotH - g.outY"
            class="bar-out" rx="3"
          />
          <text
            v-if="groups.length <= 31"
            :x="g.labelX" :y="H - 8" text-anchor="middle" class="tick"
          >
            {{ g.label }}
          </text>
          <rect
            :x="g.labelX - (plotW / groups.length) / 2" :y="PAD.top"
            :width="plotW / groups.length" :height="plotH"
            fill="transparent"
            @mousemove="onHover(g, $event)"
          />
        </g>
      </svg>
      <div
        v-if="hover"
        class="tooltip"
        :style="{ left: hover.px + 'px', top: hover.py + 'px' }"
      >
        <strong>{{ hover.label }}</strong>
        <span>In {{ hover.in }}</span>
        <span>Out {{ hover.out }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
h3 { font-size: 14px; font-weight: 600; }
.legend { display: flex; gap: 14px; font-size: 13px; color: var(--text-dim); }
.legend span { display: flex; align-items: center; gap: 6px; }
.sw { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }
.sw-in { background: var(--chart-in); }
.sw-out { background: var(--chart-out); }
.bar-in { fill: var(--chart-in); }
.bar-out { fill: var(--chart-out); }
.grid-line { stroke: var(--border); stroke-width: 1; }
.plot-wrap { position: relative; }
svg { width: 100%; height: auto; display: block; }
.tick { fill: var(--text-dim); font-size: 11px; }
.tooltip {
  position: absolute;
  transform: translate(-50%, calc(-100% - 8px));
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 6px 10px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 13px;
  pointer-events: none;
  white-space: nowrap;
  box-shadow: var(--shadow-sm);
}
</style>
