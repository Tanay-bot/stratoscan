import { useId } from 'react'
import { cn } from '@/lib/utils'

export function Sparkline({
  data,
  width = 96,
  height = 32,
  positive = true,
  className,
}: {
  data: number[]
  width?: number
  height?: number
  positive?: boolean
  className?: string
}) {
  const id = useId()
  if (!data.length) return null
  const min = Math.min(...data)
  const max = Math.max(...data)
  const range = max - min || 1
  const pts = data.map((v, i) => {
    const x = (i / (data.length - 1)) * width
    const y = height - 3 - ((v - min) / range) * (height - 6)
    return `${x.toFixed(1)},${y.toFixed(1)}`
  })
  const color = positive ? '#16c784' : '#ea3943'
  const gid = `spark-${id}`

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      className={cn('overflow-visible', className)}
      role="img"
      aria-label={positive ? 'Upward price trend' : 'Downward price trend'}
    >
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.3} />
          <stop offset="100%" stopColor={color} stopOpacity={0} />
        </linearGradient>
      </defs>
      <polygon points={`0,${height} ${pts.join(' ')} ${width},${height}`} fill={`url(#${gid})`} />
      <polyline
        points={pts.join(' ')}
        fill="none"
        stroke={color}
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}
