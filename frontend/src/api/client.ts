import type {
  ConsoleBootstrap, DeviceType, ModelInfo, Params,
  SystemStats,
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

export async function apiGetState() {
  const res = await apiFetch('/api/state')
  return json<ConsoleBootstrap>(res)
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

let sessionReady: Promise<void> | null = null

/**
 * Establish the HttpOnly auth cookie once so <video>/<img> elements can stream
 * media via Range requests. Without it, multi-gigabyte previews would have to be
 * downloaded entirely into browser memory as blobs.
 */
export function apiEnsureSession(): Promise<void> {
  if (!API_TOKEN) return Promise.resolve()
  if (!sessionReady) {
    sessionReady = apiFetch('/api/auth/session', { method: 'POST' })
      .then(res => { if (!res.ok) throw new Error(`HTTP ${res.status}`) })
      .catch(() => { sessionReady = null })
  }
  return sessionReady
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

export type UploadProgress = { uploaded: number; total: number }

/**
 * Chunked, resumable upload for large videos. Splits the file into 16 MiB
 * parts, uploads them sequentially with per-part retry, and reports byte-level
 * progress. Aborting the signal cleans up the server-side pending upload.
 */
export async function apiVlmUploadChunked(
  file: File,
  onProgress: (p: UploadProgress) => void,
  signal?: AbortSignal,
): Promise<{ videoId: string }> {
  const init = await apiVlm<{ uploadId: string; chunkSize: number }>('/videos/init', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ filename: file.name, size: file.size }),
    signal,
  })
  const { uploadId, chunkSize } = init
  const total = file.size
  let uploaded = 0
  const cleanup = () => { void apiFetch(`/api/vlm/videos/${uploadId}`, { method: 'DELETE' }).catch(() => {}) }
  try {
    for (let offset = 0, index = 0; offset < total; offset += chunkSize, index++) {
      if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
      const blob = file.slice(offset, Math.min(offset + chunkSize, total))
      let attempt = 0
      for (;;) {
        try {
          await apiFetch(`/api/vlm/videos/${uploadId}/parts/${index}`, {
            method: 'PUT',
            headers: { 'content-type': 'application/octet-stream' },
            body: blob,
            signal,
          })
          break
        } catch (e) {
          if (signal?.aborted) throw e
          if (++attempt > 3) throw e
          await new Promise(r => setTimeout(r, 500 * attempt))
        }
      }
      uploaded += blob.size
      onProgress({ uploaded, total })
    }
    const meta = await apiVlm<{ videoId: string }>(`/videos/${uploadId}/complete`, { method: 'POST', signal })
    onProgress({ uploaded: total, total })
    return meta
  } catch (e) {
    cleanup()
    throw e
  }
}

export async function apiVlmMedia(path: string): Promise<string> {
  // Cookie auth lets media elements stream directly; never blob whole videos.
  await apiEnsureSession()
  return `/api/vlm${path}`
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
  await apiEnsureSession()
  if (signal.aborted) throw new DOMException('Aborted', 'AbortError')
  return `/api/video/previews/${id}/source`
}

export async function apiLibrary<T>(path = '', init: RequestInit = {}): Promise<T> {
  return json<T>(await apiFetch(`/api/library${path}`, init))
}

export async function apiLibraryMedia(url: string, signal?: AbortSignal): Promise<string> {
  await apiEnsureSession()
  if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
  return url
}
