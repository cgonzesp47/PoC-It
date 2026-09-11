import { PlugZap, Unplug } from 'lucide-react'
import { useEffect, useState } from 'react'
import { getProxyStatus, startProxy } from '../api'
import Button from './ui/Button'

export default function ProxyStatus() {
  const [status, setStatus] = useState(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let cancelled = false
    async function poll() {
      try {
        const s = await getProxyStatus()
        if (!cancelled) setStatus(s)
      } catch {
        if (!cancelled) setStatus(null)
      }
    }
    poll()
    const id = setInterval(poll, 5000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  async function handleStart() {
    setBusy(true)
    try {
      const s = await startProxy()
      setStatus(s)
    } finally {
      setBusy(false)
    }
  }

  const running = status?.running
  const Icon = running ? PlugZap : Unplug

  return (
    <div className="flex items-center gap-2 text-sm">
      <Icon size={15} className={running ? 'text-emerald-500' : 'text-red-500'} />
      <span className="text-slate-500 dark:text-slate-400">
        {status === null ? 'Sin datos' : running ? 'Proxy LiteLLM activo' : 'Proxy LiteLLM caído'}
      </span>
      {status && !running && (
        <Button variant="secondary" size="sm" onClick={handleStart} disabled={busy}>
          {busy ? 'Arrancando…' : 'Arrancar'}
        </Button>
      )}
    </div>
  )
}
