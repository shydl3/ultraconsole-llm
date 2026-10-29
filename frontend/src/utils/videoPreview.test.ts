import { afterEach, expect, it, vi } from 'vitest'
import { apiCreateVideoPreview, apiDeleteVideoPreview, apiVideoPreviewStatus, apiVideoPreviewUrl } from '../api/client'
import { prepareVideoPreview } from './videoPreview'

vi.mock('../api/client', () => ({
  apiCreateVideoPreview: vi.fn(), apiDeleteVideoPreview: vi.fn(),
  apiVideoPreviewStatus: vi.fn(), apiVideoPreviewUrl: vi.fn(),
}))

afterEach(() => vi.resetAllMocks())

it('loads a converted preview and releases the server file', async () => {
  vi.mocked(apiCreateVideoPreview).mockResolvedValue({ id: 'id' })
  vi.mocked(apiVideoPreviewStatus).mockResolvedValue({ state: 'ready', progress: 100 })
  vi.mocked(apiVideoPreviewUrl).mockResolvedValue('/api/video/previews/id/source')
  vi.mocked(apiDeleteVideoPreview).mockResolvedValue(undefined)
  const progress = vi.fn()
  const preview = await prepareVideoPreview({ name: 'test.mp4' } as File, new AbortController().signal, progress)
  expect(preview.url).toBe('/api/video/previews/id/source')
  expect(progress).toHaveBeenCalledWith(100)
  preview.release()
  expect(apiDeleteVideoPreview).toHaveBeenCalledWith('id')
})

it('cleans up a job if cancelled while uploading', async () => {
  const controller = new AbortController()
  vi.mocked(apiCreateVideoPreview).mockImplementation(async () => {
    controller.abort()
    return { id: 'cancelled' }
  })
  vi.mocked(apiDeleteVideoPreview).mockResolvedValue(undefined)
  await expect(prepareVideoPreview({} as File, controller.signal, vi.fn())).rejects.toThrow()
  expect(apiDeleteVideoPreview).toHaveBeenCalledWith('cancelled')
  expect(apiVideoPreviewStatus).not.toHaveBeenCalled()
})

it('surfaces conversion failure and removes the job', async () => {
  vi.mocked(apiCreateVideoPreview).mockResolvedValue({ id: 'bad' })
  vi.mocked(apiVideoPreviewStatus).mockResolvedValue({ state: 'failed', progress: 0, error: 'No video stream' })
  vi.mocked(apiDeleteVideoPreview).mockResolvedValue(undefined)
  await expect(prepareVideoPreview({} as File, new AbortController().signal, vi.fn())).rejects.toThrow('No video stream')
  expect(apiDeleteVideoPreview).toHaveBeenCalledWith('bad')
})
