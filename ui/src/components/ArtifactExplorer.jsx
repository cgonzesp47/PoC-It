import { ChevronDown, ChevronRight, File, Folder } from 'lucide-react'
import { useEffect, useState } from 'react'
import { getArtifact, getFileTree } from '../api'
import Card from './ui/Card'

function findFirst(node, predicate) {
  if (predicate(node)) return node
  for (const child of node.children || []) {
    const found = findFirst(child, predicate)
    if (found) return found
  }
  return null
}

function TreeNode({ node, selectedPath, onSelect, depth = 0 }) {
  const [open, setOpen] = useState(depth < 1)
  const indent = { paddingLeft: `${depth * 14 + 8}px` }

  if (node.type === 'dir') {
    const Chevron = open ? ChevronDown : ChevronRight
    return (
      <div>
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="flex w-full items-center gap-1.5 rounded px-2 py-1 text-left text-sm text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
          style={indent}
        >
          <Chevron size={13} className="shrink-0 text-slate-400" />
          <Folder size={14} className="shrink-0 text-brand-500" />
          <span className="truncate">{node.name}</span>
        </button>
        {open && (
          <div>
            {node.children.map((child) => (
              <TreeNode
                key={child.path}
                node={child}
                selectedPath={selectedPath}
                onSelect={onSelect}
                depth={depth + 1}
              />
            ))}
          </div>
        )}
      </div>
    )
  }

  const active = node.path === selectedPath
  return (
    <button
      type="button"
      onClick={() => onSelect(node.path)}
      className={`flex w-full items-center gap-1.5 truncate rounded px-2 py-1 text-left text-sm ${
        active
          ? 'bg-brand-600 text-white'
          : 'text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800'
      }`}
      style={{ paddingLeft: `${depth * 14 + 8 + 18}px` }}
    >
      <File size={14} className={`shrink-0 ${active ? 'text-white' : 'text-slate-400'}`} />
      <span className="truncate">{node.name}</span>
    </button>
  )
}

export default function ArtifactExplorer({ runId }) {
  const [tree, setTree] = useState(null)
  const [error, setError] = useState(null)
  const [selectedPath, setSelectedPath] = useState(null)
  const [content, setContent] = useState('')
  const [contentError, setContentError] = useState(null)

  useEffect(() => {
    let cancelled = false
    getFileTree(runId)
      .then((data) => {
        if (cancelled) return
        setTree(data)
        const readme = findFirst(data, (n) => n.type === 'file' && n.name.toLowerCase() === 'readme.md')
        if (readme) setSelectedPath(readme.path)
      })
      .catch((err) => !cancelled && setError(err.message))
    return () => {
      cancelled = true
    }
  }, [runId])

  useEffect(() => {
    if (!selectedPath) return
    let cancelled = false
    setContentError(null)
    getArtifact(runId, selectedPath)
      .then((data) => !cancelled && setContent(data.content))
      .catch((err) => !cancelled && setContentError(err.message))
    return () => {
      cancelled = true
    }
  }, [runId, selectedPath])

  if (error) {
    return <p className="text-sm text-slate-500 dark:text-slate-400">{error}</p>
  }

  if (!tree) {
    return <p className="text-sm text-slate-500 dark:text-slate-400">Cargando ficheros…</p>
  }

  return (
    <div className="grid grid-cols-3 gap-4">
      <Card className="col-span-1 max-h-96 overflow-y-auto py-2">
        <TreeNode node={tree} selectedPath={selectedPath} onSelect={setSelectedPath} />
      </Card>
      <Card className="col-span-2 max-h-96 overflow-auto bg-slate-50 p-4 dark:bg-slate-950">
        {!selectedPath && (
          <p className="text-sm text-slate-400">Selecciona un fichero del árbol.</p>
        )}
        {selectedPath && contentError && (
          <p className="text-sm text-red-600 dark:text-red-400">{contentError}</p>
        )}
        {selectedPath && !contentError && (
          <pre className="whitespace-pre-wrap break-words font-mono text-xs text-slate-700 dark:text-slate-300">
            {content}
          </pre>
        )}
      </Card>
    </div>
  )
}
