<script setup>
defineProps({
  cam: { type: Object, default: null },
  big: { type: Boolean, default: false }, // counts-only wall display
})
</script>

<template>
  <div class="counts" v-if="cam">
    <div class="stat in">
      <span class="icon">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 19V5M5 12l7 7 7-7" />
        </svg>
      </span>
      <div class="body">
        <span class="label">In</span>
        <span class="value">{{ cam.in }}</span>
      </div>
    </div>
    <div class="stat out">
      <span class="icon">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 5v14M5 12l7-7 7 7" />
        </svg>
      </span>
      <div class="body">
        <span class="label">Out</span>
        <span class="value">{{ cam.out }}</span>
      </div>
    </div>
    <div class="stat inside">
      <span class="icon">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M17 21v-2a4 4 0 00-4-4H7a4 4 0 00-4 4v2" />
          <circle cx="10" cy="7" r="4" />
          <path d="M21 21v-2a4 4 0 00-3-3.87" />
          <path d="M16 3.13a4 4 0 010 7.75" />
        </svg>
      </span>
      <div class="body">
        <span class="label">Occupancy</span>
        <span class="value">{{ cam.inside }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.counts { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.stat {
  position: relative;
  display: flex;
  align-items: center;
  gap: 14px;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px 18px;
  overflow: hidden;
  box-shadow: var(--shadow-sm);
}
.stat::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 4px;
}
.in::before { background: var(--green); }
.out::before { background: var(--red); }
.inside::before { background: var(--purple); }

.icon {
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  border-radius: 12px;
  flex-shrink: 0;
}
.in .icon { color: var(--green); background: rgba(16, 185, 129, 0.12); }
.out .icon { color: var(--red); background: rgba(244, 63, 94, 0.12); }
.inside .icon { color: var(--purple); background: rgba(139, 92, 246, 0.12); }

.body { display: flex; flex-direction: column; }
.label {
  color: var(--text-dim);
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.6px;
}
.value {
  font-family: var(--font-display);
  font-size: clamp(22px, 2.3vw, 40px);
  font-weight: 700;
  line-height: 1.1;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.02em;
}
.in .value { color: var(--green); }
.out .value { color: var(--red); }
.inside .value { color: var(--purple); }
</style>
