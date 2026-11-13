import { useEffect, useRef, type ReactNode } from 'react'
import styles from './ConfirmDialog.module.css'

/** Small modal for destructive confirmations; native <dialog> keeps focus/escape handling. */
export function ConfirmDialog({ title, children, confirmLabel, cancelLabel, danger, busy, onConfirm, onClose }: {
  title: string; children: ReactNode; confirmLabel: string; cancelLabel: string
  danger?: boolean; busy: boolean; onConfirm: () => void; onClose: () => void
}) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => { ref.current?.showModal() }, [])
  return <dialog ref={ref} className={styles.dialog} aria-labelledby="confirm-dialog-title"
    onCancel={e => { e.preventDefault(); if (!busy) onClose() }}>
    <h2 id="confirm-dialog-title">{title}</h2>
    <div className={styles.body}>{children}</div>
    <div className={styles.actions}>
      <button type="button" disabled={busy} onClick={onClose}>{cancelLabel}</button>
      <button type="button" className={danger ? styles.danger : undefined} disabled={busy} onClick={onConfirm}>{confirmLabel}</button>
    </div>
  </dialog>
}
