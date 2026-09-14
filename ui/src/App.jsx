import { Sparkles } from 'lucide-react'
import { useState } from 'react'
import Dashboard from './components/Dashboard'
import NewRunForm from './components/NewRunForm'
import ProxyStatus from './components/ProxyStatus'
import RunView from './components/RunView'
import Settings from './components/Settings'

export default function App() {
  const [view, setView] = useState({ name: 'dashboard' })

  return (
    <div className="min-h-full bg-slate-50 dark:bg-slate-950">
      <header className="border-b border-slate-200 bg-white/80 backdrop-blur dark:border-slate-800 dark:bg-slate-950/80">
        <div className="mx-auto flex max-w-4xl items-center justify-between px-6 py-4">
          <button
            type="button"
            onClick={() => setView({ name: 'dashboard' })}
            className="flex items-center gap-2 text-lg font-semibold text-slate-900 dark:text-slate-100"
          >
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-brand-500 to-fuchsia-500 text-white shadow-sm">
              <Sparkles size={16} />
            </span>
            PoC-it
          </button>
          <div className="flex items-center gap-4">
            <ProxyStatus />
            <button
              type="button"
              onClick={() => setView({ name: 'settings' })}
              className="text-sm text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200"
            >
              Ajustes
            </button>
          </div>
        </div>
      </header>

      <main>
        {view.name === 'settings' && <Settings />}
        {view.name === 'dashboard' && (
          <Dashboard
            onNewRun={() => setView({ name: 'new' })}
            onOpenRun={(id) => setView({ name: 'run', id })}
          />
        )}
        {view.name === 'new' && (
          <NewRunForm
            onCreated={(id) => setView({ name: 'run', id })}
            onCancel={() => setView({ name: 'dashboard' })}
          />
        )}
        {view.name === 'run' && (
          <RunView runId={view.id} onBack={() => setView({ name: 'dashboard' })} />
        )}
      </main>
    </div>
  )
}
