export function SkeletonLine({ width = '100%' }: { width?: string }) {
  return (
    <div
      className="h-3 bg-border/30 animate-pulse"
      style={{ width }}
    />
  )
}

export function SkeletonBlock({ lines = 3 }: { lines?: number }) {
  return (
    <div className="flex flex-col gap-2">
      {Array.from({ length: lines }).map((_, i) => (
        <SkeletonLine key={i} width={`${80 - i * 15}%`} />
      ))}
    </div>
  )
}
