import { describe, expect, it } from 'vitest'
import { getHorizontalSwipeAction } from './lightboxSwipe'

describe('getHorizontalSwipeAction', () => {
  it('识别足够距离的左右滑动', () => {
    expect(getHorizontalSwipeAction({ deltaX: -72, deltaY: 12 })).toBe('next')
    expect(getHorizontalSwipeAction({ deltaX: 72, deltaY: 12 })).toBe('prev')
  })

  it('忽略短距离或垂直主导的触控', () => {
    expect(getHorizontalSwipeAction({ deltaX: -20, deltaY: 2 })).toBeNull()
    expect(getHorizontalSwipeAction({ deltaX: 80, deltaY: 120 })).toBeNull()
  })
})
