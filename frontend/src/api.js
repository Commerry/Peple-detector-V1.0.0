async function handle(res) {
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
  return res.json()
}

export const api = {
  getSettings: () => fetch('/api/settings').then(handle),
  getDevices: () => fetch('/api/devices').then(handle),
  uploadStatus: () => fetch('/api/upload/status').then(handle),
  saveSettings: (cfg) =>
    fetch('/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cfg),
    }).then(handle),
  hourly: (day, cameraId) => {
    const p = new URLSearchParams()
    if (day) p.set('day', day)
    if (cameraId != null) p.set('camera_id', cameraId)
    return fetch(`/api/history/hourly?${p}`).then(handle)
  },
  daily: (days, cameraId) => {
    const p = new URLSearchParams({ days })
    if (cameraId != null) p.set('camera_id', cameraId)
    return fetch(`/api/history/daily?${p}`).then(handle)
  },
  recentEvents: (cameraId, limit = 20) => {
    const p = new URLSearchParams({ limit })
    if (cameraId != null) p.set('camera_id', cameraId)
    return fetch(`/api/events/recent?${p}`).then(handle)
  },
  // `kind` picks the file: 'csv' for the raw rows, 'xlsx' for the report with
  // totals and bar charts drawn from its own cells.
  exportUrl: (dateFrom, dateTo, cameraId, kind = 'csv') => {
    const p = new URLSearchParams({ date_from: dateFrom, date_to: dateTo })
    if (cameraId != null) p.set('camera_id', cameraId)
    return `/api/export.${kind}?${p}`
  },
}
