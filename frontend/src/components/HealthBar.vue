<script setup>
import { useStatsStore } from '../stores/stats'
const stats = useStatsStore()
</script>

<template>
  <div class="health">
    <span class="chip" :class="stats.connected ? 'ok' : 'bad'">
      <span class="dot" :class="stats.connected ? 'on' : 'off'"></span>
      {{ stats.connected ? 'Connected' : 'Disconnected' }}
    </span>
    <span class="chip">
      <span class="meter"><i :style="{ width: Math.min(100, stats.cpu) + '%' }"></i></span>
      CPU {{ stats.cpu.toFixed(0) }}%
    </span>
    <span class="chip">
      <span class="meter"><i :style="{ width: Math.min(100, stats.mem) + '%' }"></i></span>
      RAM {{ stats.mem.toFixed(0) }}%
    </span>
  </div>
</template>

<style scoped>
.health { display: flex; gap: 8px; align-items: center; }
.chip {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
  font-weight: 500;
  color: var(--text-dim);
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 6px 13px;
}
.chip.ok { color: var(--green); border-color: rgba(16, 185, 129, 0.35); }
.chip.bad { color: var(--red); border-color: rgba(244, 63, 94, 0.4); }
.meter {
  width: 44px;
  height: 5px;
  border-radius: 999px;
  background: var(--bg-elevated);
  overflow: hidden;
}
.meter i {
  display: block;
  height: 100%;
  border-radius: 999px;
  background: var(--grad);
}
</style>
