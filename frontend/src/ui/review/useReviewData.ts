import { useCallback, useEffect, useState } from 'react'
import { apiLibrary, apiLibraryMedia, apiVlm, apiVlmMedia } from '../../api/client'
import type { RuleSnapshot } from '../../utils/eventRules'
import { EMPTY_FACETS, reviewQuery, type ReviewFacets, type ReviewFilter, type ReviewMarker, type ReviewPage } from '../../utils/review'
import { activeVlm, type VlmResult, type VlmRun } from '../../utils/vlm'

export type ReviewTarget =
  | { kind: 'vlm'; id: string; name?: string }
  | { kind: 'video'; id: string; name: string }

export type PlaybackStatus = {
  /** missing | running | ready | failed | unknown */
  state: string
  progress: number
  error?: string | null
  bytes?: number | null
}

export type ReviewData = {
  run: VlmRun | null
  snapshot: RuleSnapshot | null
  items: VlmResult[]
  total: number
  facets: ReviewFacets
  markers: ReviewMarker[]
  videoUrl: string
  metadata: Record<string, unknown> | null
  name: string
  loading: boolean
  error: string
}

type VideoDetail = {
  item?: { name: string }
  metadata?: Record<string, unknown>
  videos?: { id: string; name: string; url: string }[]
}

const INITIAL: ReviewData = {
  run: null, snapshot: null, items: [], total: 0, facets: EMPTY_FACETS,
  markers: [], videoUrl: '', metadata: null,
  name: '', loading: true, error: '',
}

/**
 * Loads one run (or one converted video) for the review window.
 * Live runs poll until they reach a terminal state; closed windows abort everything.
 */
export function useReviewData(target: ReviewTarget, filter: ReviewFilter, offset: number, limit: number) {
  const [data, setData] = useState<ReviewData>(INITIAL)
  const [playback, setPlayback] = useState<PlaybackStatus>({ state: 'missing', progress: 0 })
  const [nonce, setNonce] = useState(0)
  const [playbackNonce, setPlaybackNonce] = useState(0)
  const query = reviewQuery(filter, offset, limit)
  const { kind, id } = target
  const providedName = target.name ?? ''
  const reload = useCallback(() => setNonce(value => value + 1), [])

  useEffect(() => {
    let alive = true
    const controller = new AbortController()
    let timer: number | undefined
    setData(previous => ({ ...previous, loading: true, error: '', items: [], total: 0, facets: EMPTY_FACETS, markers: [] }))

    if (kind === 'video') {
      void apiLibrary<VideoDetail>(`/video/${encodeURIComponent(id)}`, { signal: controller.signal }).then(async detail => {
        const source = detail.videos?.[0]?.url
        const url = source ? await apiLibraryMedia(source, controller.signal) : ''
        if (!alive) return
        setData({
          run: null, snapshot: null, items: [], total: 0, facets: EMPTY_FACETS, markers: [],
          videoUrl: url, metadata: detail.metadata ?? null, name: detail.item?.name || providedName || id,
          loading: false, error: '',
        })
      }).catch(error => {
        if (alive && !controller.signal.aborted) setData(previous => ({ ...previous, loading: false, error: String(error) }))
      })
      return () => { alive = false; controller.abort() }
    }

    const load = async () => {
      try {
        const [run, page, rules] = await Promise.all([
          apiVlm<VlmRun>(`/runs/${id}`, { signal: controller.signal }),
          apiVlm<ReviewPage>(`/runs/${id}/results?${query}`, { signal: controller.signal }),
          apiVlm<RuleSnapshot>(`/runs/${id}/rules`, { signal: controller.signal }),
        ])
        if (!alive) return
        setData(previous => ({
          ...previous, run, snapshot: rules, items: page.items, total: page.total,
          facets: page.facets, markers: page.markers, name: providedName || run.fileName || id,
          loading: false, error: '',
        }))
        // Background tabs do not need 2.5s polling; keep just enough to stay in sync.
        if (activeVlm(run.state)) timer = window.setTimeout(() => void load(), document.visibilityState === 'hidden' ? 10000 : 2500)
      } catch (error) {
        if (!alive || controller.signal.aborted) return
        setData(previous => ({ ...previous, loading: false, error: String(error) }))
        timer = window.setTimeout(() => void load(), 5000)
      }
    }
    void load()
    return () => { alive = false; controller.abort(); window.clearTimeout(timer) }
  }, [kind, id, providedName, query, nonce])

  const videoId = data.run?.videoId

  // Playback proxy: a one-off faststart + 1s-GOP copy makes seeking near-instant.
  useEffect(() => {
    setPlayback({ state: 'missing', progress: 0 })
  }, [videoId])

  useEffect(() => {
    if (kind !== 'vlm' || !videoId) return
    let alive = true
    const controller = new AbortController()
    let timer: number | undefined
    const poll = async () => {
      try {
        const status = await apiVlm<PlaybackStatus>(`/videos/${videoId}/playback`, { signal: controller.signal })
        if (!alive) return
        setPlayback(status)
        if (status.state === 'running') timer = window.setTimeout(() => void poll(), 2000)
      } catch (error) {
        if (!alive || controller.signal.aborted) return
        setPlayback({ state: 'unknown', progress: 0, error: String(error) })
      }
    }
    void poll()
    return () => { alive = false; controller.abort(); window.clearTimeout(timer) }
  }, [kind, videoId, playbackNonce])

  const startPlayback = useCallback(() => {
    if (!videoId) return
    setPlayback({ state: 'running', progress: 0 })
    void apiVlm<PlaybackStatus>(`/videos/${videoId}/playback`, { method: 'POST' }).then(status => {
      setPlayback(status)
      setPlaybackNonce(value => value + 1)
    }).catch(error => setPlayback({ state: 'failed', progress: 0, error: String(error) }))
  }, [videoId])

  useEffect(() => {
    if (kind !== 'vlm' || !videoId) return
    let alive = true
    let resource = ''
    const path = playback.state === 'ready' ? `/videos/${videoId}/playback/source` : `/videos/${videoId}/source`
    void apiVlmMedia(path).then(url => {
      resource = url
      if (alive) setData(previous => ({ ...previous, videoUrl: url }))
    }).catch(error => {
      if (alive) setData(previous => ({ ...previous, error: previous.error || String(error) }))
    })
    return () => { alive = false; if (resource.startsWith('blob:')) URL.revokeObjectURL(resource) }
  }, [kind, videoId, playback.state])

  return { ...data, playback, name: data.name || providedName || id, reload, startPlayback }
}
