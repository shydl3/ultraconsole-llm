import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  apiRulesGetRun, apiRulesListRuns, apiRulesRunStatus, apiRulesStartRun,
  rulesFrameUrl, rulesSourceUrl,
  type RulesEvent, type RulesRunDetail, type RulesRunListItem,
} from '../../api/client'
import { Card } from '../primitives/Card'
import styles from './RulesTab.module.css'

const RULE_COLORS: Record<string, string> = {
  'passage.checked': '#35c46a',
  'passage.passed_without_observed_check': '#ff5c5c',
  'passage.returned': '#ffb020',
  'passage.returned_after_check': '#ffd166',
  'passage.incomplete_at_end': '#8a93a3',
  'passage.track_lost': '#8a93a3',
  'passage.never_entered_check': '#8a93a3',
  'post.vacant': '#ff5c5c',
  'access.crossing': '#4aa3ff',
  'pose.slumped': '#c77dff',
  'pose.hand_near_ear': '#ffd166',
}

const colorOf = (ruleId: string) => RULE_COLORS[ruleId] || '#8a93a3'

const fmtT = (s: number | null | undefined) => (s == null ? '-' : `${s.toFixed(1)}s`)

function badgeFor(ev: RulesEvent): { cls: string; text: string } {
  if (ev.ruleId === 'passage.passed_without_observed_check') return { cls: styles.bad, text: '未观察到检查' }
  if (ev.ruleId === 'passage.checked') return { cls: styles.ok, text: '观察到检查' }
  if (ev.ruleId === 'passage.returned_after_check') return { cls: styles.warn, text: '检查后折返' }
  if (ev.ruleId === 'passage.returned') return { cls: styles.warn, text: '折返' }
  if (ev.ruleId === 'post.vacant') return { cls: styles.bad, text: '岗位空缺' }
  if (ev.ruleId.startsWith('pose.')) return { cls: styles.warn, text: '姿态线索' }
  if (ev.ruleId === 'access.crossing') return { cls: styles.info, text: '越界' }
  return { cls: styles.info, text: ev.status }
}

export function RulesTab() {
  const [runs, setRuns] = useState<RulesRunListItem[]>([])
  const [runId, setRunId] = useState<string | null>(null)
  const [detail, setDetail] = useState<RulesRunDetail | null>(null)
  const [events, setEvents] = useState<RulesEvent[]>([])
  const [videoId, setVideoId] = useState<string | null>(null)
  const [selected, setSelected] = useState<RulesEvent | null>(null)
  const [ruleFilter, setRuleFilter] = useState<string>('')
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState<string>('')
  const videoRef = useRef<HTMLVideoElement | null>(null)

  const refreshRuns = useCallback(async () => {
    try {
      const list = await apiRulesListRuns()
      setRuns(list)
      return list
    } catch (e) {
      setStatus(`加载运行列表失败: ${e}`)
      return []
    }
  }, [])

  useEffect(() => {
    let alive = true
    void apiRulesListRuns().then(list => { if (alive) setRuns(list) }).catch(e => {
      if (alive) setStatus(`加载运行列表失败: ${e}`)
    })
    return () => { alive = false }
  }, [])

  // 选中 run → 拉详情
  useEffect(() => {
    if (!runId) return
    let alive = true
    void (async () => {
      try {
        const d = await apiRulesGetRun(runId)
        if (!alive) return
        setDetail(d)
        setEvents(d.events || [])
        const first = (d.videos || [])[0]
        setVideoId(first ? first.videoId : null)
        setSelected(null)
      } catch (e) {
        if (alive) setStatus(`加载运行详情失败: ${e}`)
      }
    })()
    return () => {
      alive = false
    }
  }, [runId])

  const startRun = useCallback(async () => {
    setBusy(true)
    setStatus('已提交分析任务…')
    try {
      const r = await apiRulesStartRun()
      setStatus(`分析运行中: ${r.runId}`)
      // 轮询直到完成
      const timer = window.setInterval(async () => {
        try {
          const st = await apiRulesRunStatus(r.runId)
          if (st.done) {
            window.clearInterval(timer)
            setStatus('')
            await refreshRuns()
            setRunId(r.runId)
          }
        } catch {
          /* ignore */
        }
      }, 3000)
    } catch (e) {
      setStatus(`启动失败: ${e}`)
    } finally {
      setBusy(false)
    }
  }, [refreshRuns])

  const videoReport = useMemo(
    () => (detail?.videos || []).find((v) => v.videoId === videoId) || null,
    [detail, videoId],
  )
  const duration = videoReport?.durationSec || videoReport?.analyzableSec || 1

  const filteredEvents = useMemo(() => {
    let list = events
    if (videoId) list = list.filter((e) => e.videoId === videoId)
    if (ruleFilter) list = list.filter((e) => e.ruleId === ruleFilter)
    return [...list].sort((a, b) => a.startSec - b.startSec)
  }, [events, videoId, ruleFilter])

  const ruleOptions = useMemo(() => Array.from(new Set(events.map((e) => e.ruleId))).sort(), [events])

  const seekTo = useCallback((t: number) => {
    const v = videoRef.current
    if (v) v.currentTime = Math.max(0, t)
  }, [])

  const onTimelineClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const frac = (e.clientX - rect.left) / rect.width
    seekTo(frac * duration)
  }

  const cursorT = useRef<number>(0)
  const [cursor, setCursor] = useState<number>(0)
  useEffect(() => {
    const v = videoRef.current
    if (!v) return
    const onTime = () => {
      cursorT.current = v.currentTime
      setCursor(v.currentTime)
    }
    v.addEventListener('timeupdate', onTime)
    return () => v.removeEventListener('timeupdate', onTime)
  }, [videoId, runId])

  return (
    <div className={styles.wrap}>
      <div className={styles.toolbar}>
        <button className={styles.btn} disabled={busy} onClick={() => void startRun()}>
          {busy ? '提交中…' : '新建分析'}
        </button>
        <button className={styles.btn} onClick={() => void refreshRuns()}>刷新</button>
        {status && <span className={styles.muted}>{status}</span>}
      </div>

      <div className={styles.row}>
        {/* 运行列表 */}
        <div className={`${styles.col} ${styles.runsCol}`}>
          <Card title="Runs">
            <div className={styles.list}>
              {runs.length === 0 && <div className={styles.muted}>暂无运行结果</div>}
              {runs.map((r) => (
                <button
                  key={r.runId}
                  className={`${styles.item} ${r.runId === runId ? styles.on : ''}`}
                  onClick={() => setRunId(r.runId)}
                >
                  <div>{r.runId}</div>
                  <div className={styles.sub}>
                    {r.videoCount} 视频 · {r.eventCount} 事件
                  </div>
                </button>
              ))}
            </div>
          </Card>
        </div>

        {/* 主视图：播放器 + 时间轴 */}
        <div className={`${styles.col} ${styles.mainCol}`}>
          <Card
            title={videoReport ? `${videoReport.videoId} · ${videoReport.cameraProfileId || '?'}` : '预览'}
            right={
              <select className={styles.select} value={videoId || ''} onChange={(e) => setVideoId(e.target.value || null)}>
                {(detail?.videos || []).map((v) => (
                  <option key={v.videoId} value={v.videoId}>
                    {v.videoId} ({v.durationSec.toFixed(1)}s, {v.eventCount} ev)
                  </option>
                ))}
              </select>
            }
          >
            <div className={styles.player}>
              {runId && videoId ? (
                <video ref={videoRef} controls preload="metadata" src={rulesSourceUrl(runId, videoId)} />
              ) : (
                <div className={styles.muted}>选择一个运行与视频</div>
              )}
            </div>
            <div className={styles.timeline} onClick={onTimelineClick} title="点击跳转">
              {filteredEvents.map((e) => {
                const left = (e.startSec / duration) * 100
                const width = Math.max(0.6, ((e.endSec - e.startSec) / duration) * 100)
                return (
                  <div
                    key={e.eventId}
                    className={styles.tlEvent}
                    style={{ left: `${left}%`, width: `${width}%`, background: colorOf(e.ruleId) }}
                    title={`${e.ruleId} ${fmtT(e.startSec)}-${fmtT(e.endSec)}`}
                  />
                )
              })}
              <div className={styles.tlCursor} style={{ left: `${(cursor / duration) * 100}%` }} />
            </div>
            <div className={styles.toolbar}>
              <select className={styles.select} value={ruleFilter} onChange={(e) => setRuleFilter(e.target.value)}>
                <option value="">全部规则</option>
                {ruleOptions.map((r) => (
                  <option key={r} value={r}>{r}</option>
                ))}
              </select>
              <span className={styles.muted}>
                {filteredEvents.length} 事件 · 时长 {duration.toFixed(1)}s · 采样 {videoReport?.sampledFrames ?? '-'} 帧
              </span>
            </div>
          </Card>
        </div>

        {/* 事件列表 */}
        <div className={`${styles.col} ${styles.eventsCol}`}>
          <Card title="Events">
            <div className={styles.evList}>
              {filteredEvents.length === 0 && <div className={styles.muted}>无事件</div>}
              {filteredEvents.map((e) => {
                const b = badgeFor(e)
                const on = selected?.eventId === e.eventId
                return (
                  <div
                    key={e.eventId}
                    className={`${styles.ev} ${on ? styles.on : ''}`}
                    onClick={() => {
                      setSelected(e)
                      seekTo(e.triggerSec ?? e.startSec)
                    }}
                  >
                    <div className={styles.rid}>
                      {e.ruleId}
                      <span className={`${styles.badge} ${b.cls}`}>{b.text}</span>
                    </div>
                    <div className={styles.meta}>
                      {fmtT(e.startSec)} → {fmtT(e.endSec)} · {e.subjectIds.join(',')} · {e.status}
                    </div>
                    {on && (
                      <>
                        <div className={styles.kv}>
                          <span className={styles.k}>trigger</span><span>{fmtT(e.triggerSec)}</span>
                          <span className={styles.k}>evidence</span><span>{e.evidenceLevel}</span>
                          {Object.entries(e.metrics || {}).slice(0, 8).map(([k, v]) => [
                            <span key={`k-${k}`} className={styles.k}>{k}</span>,
                            <span key={`v-${k}`}>{typeof v === 'number' ? v.toFixed(2) : String(v)}</span>,
                          ])}
                        </div>
                        <div className={styles.muted}>{(e.reasons || []).join(' | ')}</div>
                        {runId && (e.keyframes || []).length > 0 && (
                          <div className={styles.evidence}>
                            {(e.keyframes || []).map((kf, i) => (
                              <img key={i} src={rulesFrameUrl(runId, kf.path)} alt={kf.phase || 'evidence'} loading="lazy" />
                            ))}
                          </div>
                        )}
                      </>
                    )}
                  </div>
                )
              })}
            </div>
          </Card>
        </div>
      </div>
    </div>
  )
}
