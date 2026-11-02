import { Card } from '../primitives/Card'
import { VideoTab } from './tabs/VideoTab'
import styles from './ViewerPanel.module.css'

export function ViewerPanel() {
  return (
    <div className={styles.wrap}>
      <Card title="Video">
        <VideoTab />
      </Card>
    </div>
  )
}
