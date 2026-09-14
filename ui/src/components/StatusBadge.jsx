import { CheckCircle2, CircleDashed, Loader2, XCircle } from 'lucide-react'
import Badge from './ui/Badge'

const CONFIG = {
  queued: { tone: 'neutral', label: 'En cola', icon: CircleDashed },
  running: { tone: 'warning', label: 'En curso', icon: Loader2 },
  done: { tone: 'success', label: 'Completado', icon: CheckCircle2 },
  error: { tone: 'danger', label: 'Error', icon: XCircle },
}

export default function StatusBadge({ status }) {
  const { tone, label, icon: Icon } = CONFIG[status] || CONFIG.queued
  return (
    <Badge tone={tone}>
      <Icon size={12} className={status === 'running' ? 'animate-spin' : ''} />
      {label}
    </Badge>
  )
}
