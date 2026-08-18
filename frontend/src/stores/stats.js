import { defineStore } from 'pinia'

// Live stats via WebSocket with auto-reconnect.
export const useStatsStore = defineStore('stats', {
  state: () => ({
    cameras: [],
    // site-wide totals: IN from the entrance camera, OUT from the exit camera
    site: { in: 0, out: 0, inside: 0, in_camera: 1, out_camera: 2 },
    cpu: 0,
    mem: 0,
    connected: false,
    _ws: null,
    _retry: null,
  }),
  getters: {
    byId: (state) => (id) => state.cameras.find((c) => c.camera_id === id),
  },
  actions: {
    connect() {
      if (this._ws) return
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      const ws = new WebSocket(`${proto}://${location.host}/ws`)
      this._ws = ws
      ws.onopen = () => (this.connected = true)
      ws.onmessage = (e) => {
        const data = JSON.parse(e.data)
        this.cameras = data.cameras
        if (data.site) this.site = data.site
        this.cpu = data.cpu
        this.mem = data.mem
      }
      ws.onclose = () => {
        this.connected = false
        this._ws = null
        this._retry = setTimeout(() => this.connect(), 2000)
      }
      ws.onerror = () => ws.close()
    },
  },
})
