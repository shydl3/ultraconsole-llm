import { RuleSnapshotView } from '../vlm/RuleSnapshotView'
import { QualityWarnings } from '../vlm/QualityWarnings'
import { useEffect, useRef, useState } from 'react'
import { apiLibrary, apiLibraryMedia } from '../../api/client'
import { useT } from '../../i18n'
import { libraryPath, type LibraryDetail as Detail, type LibraryItem } from '../../utils/library'
import styles from './LibraryPage.module.css'

function Media({ url, image = false, name }: { url: string; image?: boolean; name: string }) {
  const t = useT()
  const [source, setSource] = useState('')
  const [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    let resource = ''
    void apiLibraryMedia(url, controller.signal).then(value => {
      resource = value
      if (controller.signal.aborted) { if (value.startsWith('blob:')) URL.revokeObjectURL(value); return }
      setSource(value)
    }).catch(e => { if (!controller.signal.aborted) setError(String(e)) })
    return () => { controller.abort(); if (resource.startsWith('blob:')) URL.revokeObjectURL(resource) }
  }, [url])
  if (error && !source) return <p role="alert" className={styles.error}>{t('library.mediaError')} {error}</p>
  if (!source) return <p>{t('library.loading')}</p>
  return image ? <img className={styles.evidence} src={source} alt={name} /> : <>
    <video className={styles.video} src={source} controls preload="metadata" onError={() => setError(t('library.playbackError'))} />
    <a className={styles.link} href={source} download={name.endsWith('.mp4') ? name : `${name}.mp4`}>{t('library.download')}</a>
    {error && <p role="alert" className={styles.error}>{error}</p>}
  </>
}

export function LibraryDetail({ item, onClose, revision }: { item: LibraryItem; onClose: () => void; revision: number }) {
  const t = useT()
  const [detail, setDetail] = useState<Detail | null>(null)
  const [offset, setOffset] = useState(0)
  const [videoId, setVideoId] = useState('')
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState('')
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => { heading.current?.focus() }, [])
  useEffect(() => {
    const controller = new AbortController()
    void apiLibrary<Detail>(`${libraryPath(item)}?offset=${offset}&limit=20`, { signal: controller.signal }).then(value => {
      if (!controller.signal.aborted) { setDetail(value); setError('') }
    }).catch(e => { if (!controller.signal.aborted) setError(String(e)) })
    return () => controller.abort()
  }, [item, offset, revision])
  const videos = detail?.videos || []
  const video = videos.find(v => v.id === videoId) || videos[0]
  return <aside className={styles.detail} aria-label={t('library.details')}>
    <div className={styles.headingRow}>
      <h2 ref={heading} tabIndex={-1}>{detail?.item.name || item.name}</h2>
      <button onClick={onClose} aria-label={t('library.close')}>×</button>
    </div>
    <p className={styles.muted}>{t(`library.${item.kind}`)} · {item.id}</p>
    {error && <p role="alert" className={styles.error}>{error}</p>}
    {!detail && !error && <p>{t('library.loading')}</p>}
    {videos.length > 1 && <select aria-label={t('library.sourceVideo')} value={video?.id || ''} onChange={e => setVideoId(e.target.value)}>
      {videos.map(v => <option key={v.id} value={v.id}>{v.name}</option>)}
    </select>}
    {video && <Media key={video.url} url={video.url} name={video.name} />}
    {detail && item.kind !== 'video' && <>
      <h3>{t('library.results')} · {detail.total}</h3>
      {detail.metadata.error ? <p className={styles.error}>{String(detail.metadata.error)}</p> : null}
      {!detail.results.length && <p className={styles.muted}>{t('library.noResults')}</p>}
      {detail.results.map(result => {
        const semantic = 'resultId' in result
        const id = semantic ? result.resultId : result.eventId
        const time = semantic ? result.triggerSec : (result.triggerSec ?? result.startSec)
        const images = semantic
          ? (result.segments || []).flatMap(s => s.previewPath ? [`/api/vlm/runs/${item.id}/artifacts/${s.previewPath}`] : [])
          : (result.keyframes || []).map(f => `/api/rules/runs/${item.id}/frames/${f.path}`)
        return <article className={styles.result} key={id}>
          <div className={styles.headingRow}>
            <strong>{semantic ? `#${result.trackId} · ${result.poiId}` : result.ruleId}</strong>
            <span className={styles.badge}>{time?.toFixed(2)}s</span>
          </div>
          {semantic && <p>{result.description}</p>}
          <p>{semantic ? t(`vlm.${result.decision}`) : result.status}</p>
          <p className={styles.muted}>{semantic ? result.reason : result.reasons?.join(' · ')}</p>
          {semantic && result.modelDecisionConfidence != null && <p>{t('vlm.confidence')}: {(result.modelDecisionConfidence * 100).toFixed(0)}%</p>}
          {semantic && <QualityWarnings warnings={result.qualityWarnings} />}
          {images.length > 0 && <button onClick={() => setExpanded(expanded === id ? '' : id)}>{t('library.evidence')}</button>}
          {expanded === id && images.map((url, index) => <Media key={`${url}-${index}`} url={url} image name={t('library.evidence')} />)}
        </article>
      })}
      {detail.total > 20 && <div className={styles.pager}>
        <button disabled={offset === 0} onClick={() => { setOffset(offset - 20); setExpanded('') }}>←</button>
        <span>{offset + 1}–{Math.min(offset + 20, detail.total)} / {detail.total}</span>
        <button disabled={offset + 20 >= detail.total} onClick={() => { setOffset(offset + 20); setExpanded('') }}>→</button>
      </div>}
    </>}
    {detail?.ruleSnapshot && <RuleSnapshotView snapshot={detail.ruleSnapshot} />}
    {detail && <details className={styles.raw}><summary>{t('library.metadata')}</summary><pre>{JSON.stringify(detail.metadata, null, 2)}</pre></details>}
  </aside>
}
