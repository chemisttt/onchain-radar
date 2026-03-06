import { ReactNode } from 'react'

interface PanelProps {
  title: ReactNode
  children: ReactNode
  className?: string
  actions?: ReactNode
}

export default function Panel({ title, children, className = '', actions }: PanelProps) {
  return (
    <div className={`border border-border bg-bg-panel flex flex-col min-h-0 ${className}`}>
      <div className="flex items-center justify-between px-3 py-1.5 bg-bg-titlebar border-b border-border">
        <span className="text-xs text-text-secondary uppercase tracking-wider font-medium">
          {title}
        </span>
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </div>
      <div className="flex-1 overflow-auto p-3">
        {children}
      </div>
    </div>
  )
}
