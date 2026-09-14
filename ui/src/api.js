const BASE = '/api'

async function handle(res) {
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail || detail
    } catch {
      // ignore body-parse errors, keep statusText
    }
    throw new Error(detail)
  }
  return res.json()
}

export function listRuns() {
  return fetch(`${BASE}/runs`).then(handle)
}

export function getRun(id) {
  return fetch(`${BASE}/runs/${id}`).then(handle)
}

export function createRun(payload) {
  return fetch(`${BASE}/runs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then(handle)
}

export function streamRun(id, { onLine, onDone, onError }) {
  const source = new EventSource(`${BASE}/runs/${id}/stream`)
  source.onmessage = (evt) => onLine?.(evt.data)
  source.addEventListener('done', () => {
    onDone?.()
    source.close()
  })
  source.onerror = (evt) => {
    onError?.(evt)
    source.close()
  }
  return () => source.close()
}

export function getFileTree(id) {
  return fetch(`${BASE}/runs/${id}/files`).then(handle)
}

export function getArtifact(id, path) {
  return fetch(`${BASE}/runs/${id}/artifact?path=${encodeURIComponent(path)}`).then(handle)
}

export function publishRun(id) {
  return fetch(`${BASE}/runs/${id}/publish`, { method: 'POST' }).then(handle)
}

export function getProxyStatus() {
  return fetch(`${BASE}/proxy/status`).then(handle)
}

export function startProxy() {
  return fetch(`${BASE}/proxy/start`, { method: 'POST' }).then(handle)
}

export function stopProxy() {
  return fetch(`${BASE}/proxy/stop`, { method: 'POST' }).then(handle)
}

export function getSettings() {
  return fetch(`${BASE}/settings`).then(handle)
}

export function updateSettings(values) {
  return fetch(`${BASE}/settings`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(values),
  }).then(handle)
}
