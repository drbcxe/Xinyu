import { useEffect, useRef, useState } from 'react'
import type { PrivacyStatus } from '../relationshipApi'
import PrivacyPanel from './PrivacyPanel'

type Props = {
  value: PrivacyStatus
  onClose: () => void
  onRefresh: () => Promise<unknown>
  onSaved: (allowed: boolean) => void
}

export default function PrivacyDialog({ value, onClose, onRefresh, onSaved }: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    const dialog = dialogRef.current
    const previous = document.activeElement as HTMLElement | null
    const overflow = document.body.style.overflow
    dialog?.showModal()
    document.body.style.overflow = 'hidden'
    return () => {
      dialog?.close()
      document.body.style.overflow = overflow
      if (previous?.isConnected) previous.focus()
    }
  }, [])

  return <dialog
    ref={dialogRef}
    className="privacy-dialog"
    aria-labelledby="privacy-dialog-title"
    onCancel={event => { event.preventDefault(); if (!busy) onClose() }}
    onClick={event => { if (event.target === event.currentTarget && !busy) onClose() }}
  >
    <button type="button" className="privacy-dialog-close" aria-label="关闭数据使用说明" disabled={busy} onClick={onClose}>×</button>
    <div className="privacy-dialog-scroll">
      <PrivacyPanel value={value} onRefresh={onRefresh} onBusyChange={setBusy} onSaved={onSaved} />
    </div>
    <div className="privacy-dialog-footer"><button type="button" disabled={busy} onClick={onClose}>{value.accepted ? '返回页面' : '暂不开启，继续浏览'}</button></div>
  </dialog>
}
