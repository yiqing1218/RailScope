const frames: number[] = []
const interactions: number[] = []
const queries: number[] = []
let previous = 0
let frame = 0

function retain(values: number[], value: number, size: number) {
  if (values.length === size) values.shift()
  values.push(value)
}
function percentile(values: number[], fraction: number) {
  const sorted = [...values].sort((a, b) => a - b)
  return sorted[Math.max(0, Math.ceil(sorted.length * fraction) - 1)] ?? 0
}
function measure(now: number) {
  if (previous && !document.hidden) retain(frames, now - previous, 360)
  previous = now
  frame = requestAnimationFrame(measure)
}

export function startMetrics() {
  frame = requestAnimationFrame(measure)
  return () => cancelAnimationFrame(frame)
}

export function recordSelection(ms: number) { retain(interactions, ms, 80) }
export function recordQuery(ms: number) { retain(queries, ms, 80) }

export function metrics() {
  const mean = frames.reduce((sum, value) => sum + value, 0) / frames.length
  return {
    fps: Number.isFinite(mean) ? Math.round(1000 / mean) : 0,
    samples: frames.length,
    p50: percentile(frames, .5),
    p95: percentile(frames, .95),
    p99: percentile(frames, .99),
    selection: percentile(interactions, .95),
    query: percentile(queries, .95),
  }
}
