import type {
  DeviceType, ModelInfo, NotifyRequest, NotifyResult, Params,
  SystemStats, WebhookConfig,
} from './contracts'

export type * from './contracts'

const API_TOKEN = String(import.meta.env.VITE_ULTRA_API_TOKEN ?? '').trim()

function apiFetch(input: RequestInfo | URL, init: RequestInit = {}) {
  const headers = new Headers(init.headers)
  if (API_TOKEN) headers.set('authorization', `Bearer ${API_TOKEN}`)
  return fetch(input, { ...init, headers })
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(text || `HTTP ${res.status}`)
  }
  return (await res.json()) as T
}

export async function apiHealth() {
  const res = await apiFetch('/api/health')
  return json<{ ok: boolean; ts: number }>(res)
}

export async function apiListModels() {
  const res = await apiFetch('/api/models')
  return json<ModelInfo[]>(res)
}

export async function apiSelectModel(modelId: string) {
  const res = await apiFetch('/api/models/select', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ modelId }),
  })
  return json<ModelInfo>(res)
}

export async function apiSelectEngine(device: DeviceType) {
  const res = await apiFetch('/api/engine/select', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ device }),
  })
  return json<{ ok: boolean; device: DeviceType; warming: boolean }>(res)
}

export async function apiGetParams() {
  const res = await apiFetch('/api/params')
  return json<Params>(res)
}

export async function apiUpdateParams(p: Params) {
  const res = await apiFetch('/api/params', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(p),
  })
  return json<Params>(res)
}

export async function apiExportLogsCsv() {
  const res = await apiFetch('/api/logs/export.csv')
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return await res.text()
}

export async function apiSystemStats() {
  const res = await apiFetch('/api/system/stats')
  return json<SystemStats>(res)
}

export async function apiGetWebhook() {
  const res = await apiFetch('/api/webhook')
  return json<WebhookConfig>(res)
}

export async function apiSetWebhook(cfg: WebhookConfig) {
  const res = await apiFetch('/api/webhook', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(cfg),
  })
  return json<WebhookConfig>(res)
}

export async function apiTestWebhook() {
  const res = await apiFetch('/api/webhook/test', { method: 'POST' })
  return json<NotifyResult>(res)
}

export async function apiNotify(req: NotifyRequest) {
  const res = await apiFetch('/api/notify', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(req),
  })
  return json<NotifyResult>(res)
}

export function wsInferUrl() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${location.host}/ws/infer`
}

export function wsAuthProtocols(): string[] | undefined {
  if (!API_TOKEN) return undefined
  const bytes = new TextEncoder().encode(API_TOKEN)
  let binary = ''
  for (const byte of bytes) binary += String.fromCharCode(byte)
  const encoded = btoa(binary).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', '')
  return [`ultra-token.${encoded}`]
}

export async function apiVlm<T>(path: string, init: RequestInit = {}): Promise<T> {
  return json<T>(await apiFetch(`/api/vlm${path}`, init))
}

export async function apiVlmMedia(path: string): Promise<string> {
  const url = `/api/vlm${path}`
  if (!API_TOKEN) return url
  const response = await apiFetch(url)
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return URL.createObjectURL(await response.blob())
}

export type VideoPreviewStatus = { state: 'uploading' | 'converting' | 'ready' | 'failed' | 'cancelled'; progress: number; error?: string }

export async function apiCreateVideoPreview(file: File) {
  const body = new FormData()
  body.append('file', file)
  return json<{ id: string }>(await apiFetch('/api/video/previews', { method: 'POST', body }))
}

export async function apiVideoPreviewStatus(id: string, signal: AbortSignal) {
  return json<VideoPreviewStatus>(await apiFetch(`/api/video/previews/${id}`, { signal }))
}

export async function apiDeleteVideoPreview(id: string) {
  await apiFetch(`/api/video/previews/${id}`, { method: 'DELETE' })
}

export async function apiVideoPreviewUrl(id: string, signal: AbortSignal) {
  const url = `/api/video/previews/${id}/source`
  if (!API_TOKEN) return url
  const response = await apiFetch(url, { signal })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return URL.createObjectURL(await response.blob())
}

export async function apiLibrary<T>(path = '', init: RequestInit = {}): Promise<T> {
  return json<T>(await apiFetch(`/api/library${path}`, init))
}

export async function apiLibraryMedia(url: string, signal?: AbortSignal): Promise<string> {
  if (!API_TOKEN) return url
  const response = await apiFetch(url, { signal })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return URL.createObjectURL(await response.blob())
}
