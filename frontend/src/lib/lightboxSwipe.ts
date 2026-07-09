export type HorizontalSwipeAction = 'prev' | 'next'

export function getHorizontalSwipeAction({
  deltaX,
  deltaY,
  threshold = 48,
}: {
  deltaX: number
  deltaY: number
  threshold?: number
}): HorizontalSwipeAction | null {
  if (Math.abs(deltaX) < threshold) return null
  if (Math.abs(deltaX) <= Math.abs(deltaY)) return null
  return deltaX < 0 ? 'next' : 'prev'
}
