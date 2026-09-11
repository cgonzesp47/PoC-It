import { PlugZap, Save, Unplug } from 'lucide-react'
import { useEffect, useState } from 'react'
import { getProxyStatus, getSettings, startProxy, stopProxy, updateSettings } from '../api'
import { FIELD_CLASS } from '../lib/styles'
import Button from './ui/Button'
import Card from './ui/Card'

const GROUP_LABELS = {
  llm: 'Proveedores LLM',
  gitlab: 'Publicación en GitLab',
}

export default function Settings() {
  const [fields, setFields] = useState([])
  const [draft, setDraft] = useState({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saveMessage, setSaveMessage] = useState(null)
  const [proxy, setProxy] = useState(null)
  const [proxyBusy, setProxyBusy] = useState(false)

  function load() {
    setLoading(true)
    Promise.all([getSettings(), getProxyStatus()])
      .then(([settingsData, proxyData]) => {
        setFields(settingsData)
        setProxy(proxyData)
      })
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  function handleChange(key, value) {
    setDraft((d) => ({ ...d, [key]: value }))
  }

  async function handleSave(e) {
    e.preventDefault()
    setSaving(true)
    setSaveMessage(null)
    try {
      const updated = await updateSettings(draft)
      setFields(updated)
      setDraft({})
      setSaveMessage('Guardado.')
    } catch (err) {
      setSaveMessage(`Error: ${err.message}`)
    } finally {
      setSaving(false)
    }
  }

  async function handleProxyToggle() {
    setProxyBusy(true)
    try {
      const result = proxy?.running ? await stopProxy() : await startProxy()
      setProxy(result)
    } finally {
      setProxyBusy(false)
    }
  }

  if (loading) {
    return (
      <div className="mx-auto max-w-2xl px-6 py-10">
        <p className="text-sm text-slate-500 dark:text-slate-400">Cargando…</p>
      </div>
    )
  }

  const groups = ['llm', 'gitlab']
  const ProxyIcon = proxy?.running ? PlugZap : Unplug

  return (
    <div className="mx-auto max-w-2xl px-6 py-10">
      <h1 className="mb-1 text-2xl font-semibold text-slate-900 dark:text-slate-100">Ajustes</h1>
      <p className="mb-8 text-sm text-slate-500 dark:text-slate-400">
        Se guardan en el <code>.env</code> del proyecto.
      </p>

      <Card className="mb-8 p-5">
        <h2 className="mb-3 text-sm font-semibold text-slate-900 dark:text-slate-100">
          Proxy LiteLLM
        </h2>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
            <ProxyIcon size={16} className={proxy?.running ? 'text-emerald-500' : 'text-red-500'} />
            <div>
              <p>Estado: {proxy?.running ? 'activo' : 'caído'}</p>
              <p className="text-xs text-slate-400">{proxy?.base_url}</p>
            </div>
          </div>
          <Button variant="secondary" onClick={handleProxyToggle} disabled={proxyBusy}>
            {proxyBusy ? 'Aplicando…' : proxy?.running ? 'Detener' : 'Arrancar'}
          </Button>
        </div>
      </Card>

      <Card as="form" onSubmit={handleSave} className="space-y-8 p-6">
        {groups.map((group) => {
          const groupFields = fields.filter((f) => f.group === group)
          if (groupFields.length === 0) return null
          return (
            <fieldset key={group}>
              <legend className="mb-3 text-sm font-semibold text-slate-900 dark:text-slate-100">
                {GROUP_LABELS[group]}
              </legend>
              <div className="space-y-4">
                {groupFields.map((field) => (
                  <div key={field.key}>
                    <label
                      htmlFor={field.key}
                      className="mb-1 block text-sm font-medium text-slate-700 dark:text-slate-300"
                    >
                      {field.label}
                      {field.secret && field.is_set && (
                        <span className="ml-2 text-xs font-normal text-slate-400">
                          (definido: {field.masked})
                        </span>
                      )}
                    </label>
                    <input
                      id={field.key}
                      type={field.secret ? 'password' : 'text'}
                      value={draft[field.key] ?? (field.secret ? '' : field.value ?? '')}
                      onChange={(e) => handleChange(field.key, e.target.value)}
                      placeholder={field.secret && field.is_set ? 'Dejar en blanco para no cambiar' : ''}
                      className={FIELD_CLASS}
                    />
                  </div>
                ))}
              </div>
            </fieldset>
          )
        })}

        <div className="flex items-center gap-3">
          <Button type="submit" icon={Save} disabled={saving}>
            {saving ? 'Guardando…' : 'Guardar cambios'}
          </Button>
          {saveMessage && <p className="text-sm text-slate-500 dark:text-slate-400">{saveMessage}</p>}
        </div>
      </Card>
    </div>
  )
}
