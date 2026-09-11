import { cn } from '../../lib/cn'

const TONES = {
  neutral: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300',
  brand: 'bg-brand-100 text-brand-700 dark:bg-brand-900/40 dark:text-brand-300',
  success: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300',
  warning: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
  danger: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300',
}

export default function Badge({ tone = 'neutral', className, children }) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium',
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  )
}
