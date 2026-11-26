import { useSyncExternalStore } from 'react'
import dictionary from './messages.en.json'

export type Language = 'zh' | 'en'
const storageKey = 'railscope-next.language'
const preferred = localStorage.getItem(storageKey)
let language: Language = preferred === 'zh' || preferred === 'en'
  ? preferred : navigator.language.toLowerCase().startsWith('zh') ? 'zh' : 'en'
const listeners = new Set<() => void>()
const english: Record<string, string> = dictionary

function updateDocumentLanguage() {
  document.documentElement.lang = language === 'zh' ? 'zh-CN' : 'en'
}

updateDocumentLanguage()

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

export function useLanguage() {
  return useSyncExternalStore(subscribe, () => language)
}

export function setLanguage(value: Language) {
  if (language === value) return
  language = value
  localStorage.setItem(storageKey, value)
  updateDocumentLanguage()
  for (const listener of listeners) listener()
}

export function t(message: string, ...values: unknown[]) {
  const translated = language === 'en' && Object.hasOwn(english, message) ? english[message] : message
  return values.length ? translated.replace(/\{(\d+)\}/g, (_, index) => String(values[Number(index)] ?? '')) : translated
}
