import { useEffect, useImperativeHandle, useRef, useState, type RefObject } from 'react'
import { useT } from '../../i18n'
import { decisionKey, decisionTone, PRE_ROLL_OPTIONS, type ReviewMarker } from '../../utils/review'
import { formatVideoTime } from '../../utils/videoAnalysis'
import styles from './ReviewDialog.module.css'

export type ReviewVideoHandle = {
  seek: (sec: number) => void
  getTime: () => number
}

/**
 * Playback clock lives in its own component so timeupdate only re-renders this
 * text instead of the whole review dialog (which holds dozens of result cards).
 */
function Timecode({ target, duration }: { target: RefObject<HTMLVideoElement | null>; duration: number }) {
  const [time, setTime] = useState(0)
  useEffect(() => {
    const video = target.current
    if (!video) return
    const update = () => setTime(video.currentTime)
    video.addEventListener('timeupdate', update)
    video.addEventListener('seeked', update)
    return () => {
      video.removeEventListener('timeupdate', update)
      video.removeEventListener('seeked', update)
    }
  }, [target])
  return <span className={styles.clock}>{formatVideoTime(time)} / {formatVideoTime(duration)}</span>
}

export function ReviewVideo({ src, markers, preRoll, onPreRoll, follow, onFollow, onJump, onStep, transport, videoRef }: {
  src: string
  markers: ReviewMarker[]
  preRoll: number
  onPreRoll: (value: number) => void
  follow: boolean
  onFollow: (value: boolean) => void
  onJump: (marker: ReviewMarker) => void
  onStep: (delta: -1 | 1) => void
  transport: boolean
  videoRef: RefObject<ReviewVideoHandle | null>
}) {
  const t = useT()
  const element = useRef<HTMLVideoElement | null>(null)
  const [duration, setDuration] = useState(0)
  const lastTime = useRef(0)
  const lastSrc = useRef(src)

  useEffect(() => {
    const video = element.current
    if (!video) return
    const remember = () => { lastTime.current = video.currentTime }
    video.addEventListener('timeupdate', remember)
    video.addEventListener('seeked', remember)
    // Keep the playhead when the source swaps (original -> playback proxy).
    const resume = lastSrc.current !== src ? lastTime.current : 0
    lastSrc.current = src
    let restore: (() => void) | null = null
    if (resume > 0.5) {
      restore = () => { try { video.currentTime = resume } catch { /* Not seekable yet. */ } }
      if (video.readyState >= 1) restore()
      else video.addEventListener('loadedmetadata', restore, { once: true })
    }
    return () => {
      video.removeEventListener('timeupdate', remember)
      video.removeEventListener('seeked', remember)
      if (restore) video.removeEventListener('loadedmetadata', restore)
    }
  }, [src])

  useImperativeHandle(videoRef, () => ({
    seek(sec: number) {
      const video = element.current
      if (!video) return
      // Keep the current play/pause state: pausing here forced users to press
      // play again and the decoder restarted from scratch on every jump.
      try { video.currentTime = Math.max(0, sec) } catch { /* Metadata not ready yet. */ }
    },
    getTime() {
      return element.current?.currentTime ?? 0
    },
  }), [])

  return <>
    {src ? <video
      ref={element}
      className={styles.video}
      src={src}
      controls
      playsInline
      preload="metadata"
      onLoadedMetadata={event => setDuration(Number.isFinite(event.currentTarget.duration) ? event.currentTarget.duration : 0)}
    /> : <p className={styles.empty}>{t('review.loading')}</p>}
    {transport && <div className={styles.transport}>
      <label className={styles.small}>{t('review.preRoll')}
        <select value={preRoll} onChange={event => onPreRoll(Number(event.target.value))}>
          {PRE_ROLL_OPTIONS.map(value => <option key={value} value={value}>{`${value}s`}</option>)}
        </select>
      </label>
      <button type="button" disabled={!markers.length} onClick={() => onStep(-1)}>{`⇤ ${t('review.prev')}`}</button>
      <button type="button" disabled={!markers.length} onClick={() => onStep(1)}>{`${t('review.next')} ⇥`}</button>
      <label className={styles.small}>
        <input type="checkbox" checked={follow} onChange={event => onFollow(event.target.checked)} />{t('review.follow')}
      </label>
      <Timecode target={element} duration={duration} />
    </div>}
    {transport && <div className={styles.timeline} role="group" aria-label={t('review.timeline')}>
      <div className={styles.track} />
      {duration > 0 && markers.map(marker => <button
        key={marker.resultId}
        type="button"
        className={[styles.tick, styles[decisionTone(marker)]].join(' ')}
        style={{ left: `${Math.min(100, Math.max(0, (marker.triggerSec / duration) * 100))}%` }}
        title={`#${marker.trackId} · ${marker.triggerSec.toFixed(2)}s · ${t(decisionKey(marker))}`}
        aria-label={`${t('review.jump')} #${marker.trackId} ${marker.triggerSec.toFixed(2)}s`}
        onClick={() => onJump(marker)}
      />)}
      {duration > 0 && !markers.length && <span className={styles.hint}>{t('review.noMarkers')}</span>}
    </div>}
  </>
}
