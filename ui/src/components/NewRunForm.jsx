import { Rocket } from 'lucide-react'
import { useState } from 'react'
import { createRun } from '../api'
import { FIELD_CLASS } from '../lib/styles'
import Button from './ui/Button'
import Card from './ui/Card'

const FIELDS = [
  { name: 'nombre', label: 'Nombre de la PoC', type: 'input' },
  { name: 'problema', label: '¿Qué problema resuelve?', type: 'textarea' },
  { name: 'usuarios', label: '¿Quién utilizará el sistema?', type: 'textarea' },
  { name: 'funcionalidades', label: '¿Qué debería poder hacer el sistema?', type: 'textarea' },
  { name: 'limites', label: '¿Hay reglas o límites importantes?', type: 'textarea' },
  { name: 'tecnologias', label: '¿Qué tecnologías/integraciones necesita?', type: 'textarea' },
]

const EMPTY = Object.fromEntries(FIELDS.map((f) => [f.name, '']))

export default function NewRunForm({ onCreated, onCancel }) {
  const [values, setValues] = useState(EMPTY)
  const [publish, setPublish] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)

  function update(name, value) {
    setValues((v) => ({ ...v, [name]: value }))
  }

  async function handleSubmit(e) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      const run = await createRun({ ...values, publish })
      onCreated(run.id)
    } catch (err) {
      setError(err.message)
      setSubmitting(false)
    }
  }

  const canSubmit = FIELDS.every((f) => values[f.name].trim().length > 0)

  return (
    <div className="mx-auto max-w-2xl px-6 py-10">
      <h1 className="mb-1 text-2xl font-semibold text-slate-900 dark:text-slate-100">Nueva PoC</h1>
      <p className="mb-8 text-sm text-slate-500 dark:text-slate-400">
        Rellena la plantilla de definición. Sustituye a las preguntas que hoy se hacen por terminal.
      </p>

      <Card className="p-6">
        <form onSubmit={handleSubmit} className="space-y-5">
          {FIELDS.map((field) => (
            <div key={field.name}>
              <label
                htmlFor={field.name}
                className="mb-1 block text-sm font-medium text-slate-700 dark:text-slate-300"
              >
                {field.label}
              </label>
              {field.type === 'textarea' ? (
                <textarea
                  id={field.name}
                  rows={3}
                  value={values[field.name]}
                  onChange={(e) => update(field.name, e.target.value)}
                  className={FIELD_CLASS}
                />
              ) : (
                <input
                  id={field.name}
                  type="text"
                  value={values[field.name]}
                  onChange={(e) => update(field.name, e.target.value)}
                  className={FIELD_CLASS}
                />
              )}
            </div>
          ))}

          <label className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
            <input
              type="checkbox"
              checked={publish}
              onChange={(e) => setPublish(e.target.checked)}
              className="h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
            />
            Publicar en GitLab si el resultado es publicable
          </label>

          {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

          <div className="flex items-center gap-3 pt-2">
            <Button type="submit" icon={Rocket} disabled={!canSubmit || submitting}>
              {submitting ? 'Enviando…' : 'Generar PoC'}
            </Button>
            <Button type="button" variant="ghost" onClick={onCancel}>
              Cancelar
            </Button>
          </div>
        </form>
      </Card>
    </div>
  )
}
