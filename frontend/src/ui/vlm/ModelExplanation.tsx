import { useT } from '../../i18n'
import styles from './ModelExplanation.module.css'

export function ModelExplanation({ videoDescription, reason }: { videoDescription?: string | null; reason?: string }) {
  const t = useT()
  return <div className={styles.explanation}>
    {videoDescription && <div>
      <strong>{t('vlm.videoDescription')}</strong>
      <p>{videoDescription}</p>
    </div>}
    {reason && <div>
      <strong>{t('vlm.judgmentReason')}</strong>
      <p>{reason}</p>
    </div>}
  </div>
}
