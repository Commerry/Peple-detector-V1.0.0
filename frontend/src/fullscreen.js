import { ref, computed } from 'vue'

// kiosk=1 in the URL hides all chrome permanently (station displays)
export const isKiosk =
  new URLSearchParams(location.search).get('kiosk') === '1'

function fsElement() {
  return document.fullscreenElement || document.webkitFullscreenElement || null
}

export const isFullscreen = ref(!!fsElement())

function sync() {
  isFullscreen.value = !!fsElement()
}
document.addEventListener('fullscreenchange', sync)
document.addEventListener('webkitfullscreenchange', sync)

// F11 sets no fullscreenElement and fires no event — compare the window size
// against the screen so kiosk chrome hides for it too.
function checkF11() {
  const nearFull = Math.abs(window.innerHeight - screen.height) <= 2
  if (!fsElement()) isFullscreen.value = nearFull
}
window.addEventListener('resize', checkF11)
checkF11()

export const chromeHidden = computed(() => isKiosk || isFullscreen.value)

export function toggleFullscreen() {
  const el = document.documentElement
  if (!fsElement()) {
    const req = el.requestFullscreen || el.webkitRequestFullscreen
    // rejects when blocked (iframe without allow="fullscreen", non-gesture call)
    try {
      Promise.resolve(req?.call(el)).catch(() => {})
    } catch {
      /* not supported — leave the page as-is */
    }
  } else {
    const exit = document.exitFullscreen || document.webkitExitFullscreen
    try {
      Promise.resolve(exit?.call(document)).catch(() => {})
    } catch {
      /* ignore */
    }
  }
}
