import { forwardRef } from 'react'
import { cn } from '../../lib/cn'

const Card = forwardRef(function Card(
  { as: Tag = 'div', interactive = false, className, children, ...props },
  ref,
) {
  return (
    <Tag
      ref={ref}
      className={cn(
        'rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900',
        interactive &&
          'text-left transition-colors hover:border-brand-300 hover:bg-brand-50/40 dark:hover:border-brand-700 dark:hover:bg-brand-950/20',
        className,
      )}
      {...props}
    >
      {children}
    </Tag>
  )
})

export default Card
