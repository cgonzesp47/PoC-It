import { cn } from '../../lib/cn'

const VARIANTS = {
  primary:
    'bg-brand-600 text-white shadow-sm hover:bg-brand-700 focus-visible:outline-brand-600 disabled:hover:bg-brand-600',
  secondary:
    'border border-slate-300 text-slate-700 hover:bg-slate-100 focus-visible:outline-brand-600 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800',
  ghost:
    'text-slate-500 hover:bg-slate-100 hover:text-slate-700 focus-visible:outline-brand-600 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-200',
  danger:
    'bg-red-600 text-white shadow-sm hover:bg-red-700 focus-visible:outline-red-600 disabled:hover:bg-red-600',
}

const SIZES = {
  sm: 'px-2.5 py-1 text-xs',
  md: 'px-4 py-2 text-sm',
}

export default function Button({
  variant = 'primary',
  size = 'md',
  icon: Icon,
  className,
  children,
  ...props
}) {
  return (
    <button
      className={cn(
        'inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-colors',
        'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2',
        'disabled:cursor-not-allowed disabled:opacity-50',
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...props}
    >
      {Icon && <Icon size={size === 'sm' ? 14 : 16} strokeWidth={2.25} />}
      {children}
    </button>
  )
}
