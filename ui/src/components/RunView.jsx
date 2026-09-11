import { ArrowLeft, ExternalLink, UploadCloud } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { getRun, publishRun, streamRun } from '../api'
import ArtifactExplorer from './ArtifactExplorer'
import Button from './ui/Button'
import Card from './ui/Card'
import StatusBadge from './StatusBadge'

const STEP_RE = /^\[(\d+)\/(\d+)\]\s*(.*)$/

function parseLastStep(lines) {
  let current = null
  for (const line of lines) {
    const match = STEP_RE.exec(line)
    if (match) {
      current = { idx: Number(match[1]), total: Number(match[2]), title: match[3] }
    }
  }
  return current
}

function ResultRow({ label, children }) {
  return (
    <>
      <dt className="text-slate-500 dark:text-slate-400">{label}</dt>
      <dd className="text-slate-900 dark:text-slate-100">{children}</dd>
    </>
  )
}

export default function RunView({ runId, onBack }) {
  const [run, setRun] = useState(null)
  const [lines, setLines] = useState([])
  const [publishing, setPublishing] = useState(false)
  const [publishError, setPublishError] = useState(null)
  const logRef = useRef(null)

  useEffect(() => {
    let cancelled = false
    getRun(runId).then((r) => {
      if (!cancelled) setRun(r)
    })

    const stop = streamRun(runId, {
      onLine: (line) => setLines((prev) => [...prev, line]),
      onDone: () => {
        getRun(runId).then((r) => {
          if (!cancelled) setRun(r)
        })
      },
    })

    return () => {
      cancelled = true
      stop()
    }
  }, [runId])

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight })
  }, [lines])

  async function handlePublish() {
    setPublishing(true)
    setPublishError(null)
    try {
      const updated = await publishRun(runId)
      setRun(updated)
    } catch (err) {
      setPublishError(err.message)
    } finally {
      setPublishing(false)
    }
  }

  const isImported = run?.source === 'filesystem'
  const lastStep = parseLastStep(lines)
  const progressPct = lastStep ? Math.round((lastStep.idx / lastStep.total) * 100) : 0
  const result = run?.result

  return (
    <div className="mx-auto max-w-4xl px-6 py-10">
      <button
        type="button"
        onClick={onBack}
        className="mb-6 inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200"
      >
        <ArrowLeft size={14} />
        Volver al historial
      </button>

      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">
            {run?.nombre || 'Cargando…'}
          </h1>
          <p className="text-xs text-slate-500 dark:text-slate-400">Run {runId}</p>
        </div>
        {run && <StatusBadge status={run.status} />}
      </div>

      {!isImported && (
        <>
          {lastStep && (
            <div className="mb-6">
              <div className="mb-1 flex justify-between text-xs text-slate-500 dark:text-slate-400">
                <span>
                  Paso {lastStep.idx} de {lastStep.total}: {lastStep.title}
                </span>
                <span>{progressPct}%</span>
              </div>
              <div className="h-2 w-full overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-brand-500 to-fuchsia-500 transition-all"
                  style={{ width: `${progressPct}%` }}
                />
              </div>
            </div>
          )}

          <Card
            ref={logRef}
            className="mb-6 h-64 overflow-y-auto bg-slate-50 p-4 font-mono text-xs text-slate-700 dark:bg-slate-950 dark:text-slate-300"
          >
            {lines.length === 0 && <p className="text-slate-400">Esperando actividad…</p>}
            {lines.map((line, i) => (
              <div key={i} className="whitespace-pre-wrap">
                {line}
              </div>
            ))}
          </Card>
        </>
      )}

      {result && (
        <Card className="p-5">
          <h2 className="mb-3 text-sm font-semibold text-slate-900 dark:text-slate-100">Resultado</h2>
          {result.error ? (
            <p className="text-sm text-red-600 dark:text-red-400">{result.error}</p>
          ) : (
            <dl className="grid grid-cols-2 gap-3 text-sm">
              <ResultRow label="Modo">{result.modo ?? '—'}</ResultRow>
              <ResultRow label="Estado final">{result.estado_final ?? '—'}</ResultRow>
              <ResultRow label="Tests">
                {result.pytest_ok === undefined ? '—' : result.pytest_ok ? 'OK' : 'Fallidos'}
              </ResultRow>
              <ResultRow label="Publicable">{result.generacion_exitosa ? 'Sí' : 'No'}</ResultRow>
              {result.publish_url && (
                <ResultRow label="GitLab">
                  <a
                    className="inline-flex items-center gap-1 text-brand-600 hover:underline dark:text-brand-400"
                    href={result.publish_url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {result.publish_url}
                    <ExternalLink size={12} />
                  </a>
                </ResultRow>
              )}
            </dl>
          )}

          {!result.error && result.generacion_exitosa && !result.publish_url && (
            <div className="mt-4">
              <Button icon={UploadCloud} onClick={handlePublish} disabled={publishing}>
                {publishing ? 'Publicando…' : 'Publicar en GitLab'}
              </Button>
              {publishError && (
                <p className="mt-2 text-sm text-red-600 dark:text-red-400">{publishError}</p>
              )}
            </div>
          )}
        </Card>
      )}

      {run?.status === 'done' && result?.nombre_proyecto && (
        <div className="mt-6">
          <h2 className="mb-3 text-sm font-semibold text-slate-900 dark:text-slate-100">
            Ficheros generados
          </h2>
          <ArtifactExplorer runId={runId} />
        </div>
      )}
    </div>
  )
}
