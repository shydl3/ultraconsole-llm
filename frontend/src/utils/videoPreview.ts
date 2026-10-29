import { apiCreateVideoPreview, apiDeleteVideoPreview, apiVideoPreviewStatus, apiVideoPreviewUrl } from '../api/client'

export async function prepareVideoPreview(file: File, signal: AbortSignal, progress: (value: number) => void) {
  // Let upload return an id even after cancellation so the server job can be removed.
  const { id } = await apiCreateVideoPreview(file)
  let url = ''
  const release = () => {
    if (url.startsWith('blob:')) URL.revokeObjectURL(url)
    void apiDeleteVideoPreview(id).catch(() => {})
  }
  try {
    for (;;) {
      signal.throwIfAborted()
      const status = await apiVideoPreviewStatus(id, signal)
      if (status.state === 'failed' || status.state === 'cancelled') throw new Error(status.error || status.state)
      progress(status.progress)
      if (status.state === 'ready') break
      await new Promise<void>((resolve, reject) => {
        const onAbort = () => {
          clearTimeout(timer)
          reject(signal.reason)
        }
        const timer = setTimeout(() => {
          signal.removeEventListener('abort', onAbort)
          resolve()
        }, 1000)
        signal.addEventListener('abort', onAbort, { once: true })
        if (signal.aborted) onAbort()
      })
    }
    url = await apiVideoPreviewUrl(id, signal)
    signal.throwIfAborted()
    return { url, release }
  } catch (error) {
    release()
    throw error
  }
}
