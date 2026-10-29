import { useT } from '../../i18n'

export function QualityWarnings({ warnings }: { warnings?: string[] }) {
  const t = useT()
  if (!warnings?.length) return null
  return <div>{t('vlm.qualityNotes')}: {warnings.map(w => t(`vlm.quality.${w}`)).join('；')}</div>
}
