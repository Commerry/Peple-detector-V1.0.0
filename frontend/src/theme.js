import { ref } from 'vue'

// Priority: ?theme= URL param (kiosk windows) > saved preference > dark
const urlTheme = new URLSearchParams(location.search).get('theme')
const saved = localStorage.getItem('theme')
const initial =
  urlTheme === 'light' || urlTheme === 'dark'
    ? urlTheme
    : saved === 'light' || saved === 'dark'
      ? saved
      : 'dark'
export const theme = ref(initial)

export function applyTheme() {
  document.documentElement.dataset.theme = theme.value
}

export function toggleTheme() {
  theme.value = theme.value === 'dark' ? 'light' : 'dark'
  localStorage.setItem('theme', theme.value)
  applyTheme()
}
