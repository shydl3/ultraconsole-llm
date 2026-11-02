import { useConsoleStore } from '../../store/useConsoleStore'
import { useLocaleStore } from '../../i18n/store'
import { useT } from '../../i18n'
import styles from './SystemStatusBar.module.css'

function fmtFps(x?: number) {
  if (x === undefined || x === null || Number.isNaN(x)) return '--'
  return x.toFixed(1)
}

export function SystemStatusBar() {
  const t = useT()
  const modelId = useConsoleStore((s) => s.currentModelId)
  const device = useConsoleStore((s) => s.engine.device)
  const fps = useConsoleStore((s) => s.telemetrySummary.fps)
  const locale = useLocaleStore((s) => s.locale)
  const setLocale = useLocaleStore((s) => s.setLocale)

  return (
    <div className={styles.bar}>
      <div className={styles.left}>
        <a className={styles.libraryLink} href="/library" target="ultraconsole-library" onClick={e => {
          const popup = window.open('/library', 'ultraconsole-library', 'popup,width=1440,height=960')
          if (popup) { e.preventDefault(); popup.focus() }
        }}>{t('library.title')} ↗</a>
        <span className={styles.item}>
          Model <span className={styles.value}>{modelId || '(none)'}</span>
        </span>
        <span className={styles.sep} />
        <span className={styles.item}>
          Device <span className={styles.value}>{device.toUpperCase()}</span>
        </span>
      </div>

      <div className={styles.right}>
        <span className={styles.item}>
          FPS <span className={styles.fps}>{fmtFps(fps)}</span>
        </span>
        <span className={styles.sep} />
        <button
          className={styles.langBtn}
          onClick={() => setLocale(locale === 'zh' ? 'en' : 'zh')}
          title={locale === 'zh' ? 'Switch to English' : '切换到中文'}
        >
          {locale === 'zh' ? '中/EN' : 'EN/中'}
        </button>
      </div>
    </div>
  )
}

