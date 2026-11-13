import { Card } from '../primitives/Card'
import { useT } from '../../i18n'
import { VideoTab } from './tabs/VideoTab'
import styles from './ViewerPanel.module.css'

export function ViewerPanel() {
  const t = useT()
  return (
    <div className={styles.wrap}>
      <Card title={t('cards.video')}>
        <VideoTab />
      </Card>
    </div>
  )
}
