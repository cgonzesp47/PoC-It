import { FolderInput, Plus, Sparkles } from 'lucide-react'
import { useEffect, useState } from 'react'
import { listRuns } from '../api'
import Badge from './ui/Badge'
import Button from './ui/Button'
import Card from './ui/Card'
import StatusBadge from './StatusBadge'

function formatDate(ts) {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString()
}

export default function Dashboard({ onNewRun, onOpenRun }) {
  const [runs, setRuns] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    async function poll() {
      try {
        const data = await listRuns()
        if (!cancelled) setRuns(data)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    poll()
    const id = setInterval(poll, 4000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  return (
    <div className="mx-auto max-w-4xl px-6 py-10">
      <div className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">Runs</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Historial de PoCs generadas con PoC-it.
          </p>
        </div>
        <Button icon={Plus} onClick={onNewRun}>
          Nueva PoC
        </Button>
      </div>

      {loading && <p className="text-sm text-slate-500 dark:text-slate-400">Cargando…</p>}

      {!loading && runs.length === 0 && (
        <Card className="border-dashed p-10 text-center text-sm text-slate-500 dark:text-slate-400">
          Todavía no hay ninguna PoC generada.
        </Card>
      )}

      <div className="space-y-3">
        {runs.map((run) => (
          <Card key={run.id} as="button" interactive onClick={() => onOpenRun(run.id)} className="flex w-full items-center justify-between gap-4 p-4">
            <div className="flex min-w-0 items-center gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand-50 text-brand-600 dark:bg-brand-900/30 dark:text-brand-300">
                {run.source === 'filesystem' ? <FolderInput size={16} /> : <Sparkles size={16} />}
              </span>
              <div className="min-w-0">
                <p className="truncate font-medium text-slate-900 dark:text-slate-100">{run.nombre}</p>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  {formatDate(run.created_at)}
                </p>
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              {run.source === 'filesystem' && <Badge tone="brand">Importado</Badge>}
              {run.result?.publish_url && <Badge tone="success">Publicado</Badge>}
              <StatusBadge status={run.status} />
            </div>
          </Card>
        ))}
      </div>
    </div>
  )
}
