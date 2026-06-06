import { useEffect, useState, useRef } from 'react'
import type { TaskRecord } from '../types'
import { useStore, ensureImageThumbnailCached, subscribeImageThumbnail, retryTask } from '../store'
import { formatImageRatio } from '../lib/size'
import { getParamDisplay, ActualValueBadge } from '../lib/paramDisplay'

interface Props {
  task: TaskRecord
  onReuse: () => void
  onEditOutputs: () => void
  onDelete: () => void
  onClick: (e: React.MouseEvent | React.TouchEvent) => void
  isSelected?: boolean
}

export default function TaskCard({
  task,
  onReuse,
  onEditOutputs,
  onDelete,
  onClick,
  isSelected,
}: Props) {
  const [thumbSrc, setThumbSrc] = useState<string>('')
  const [coverRatio, setCoverRatio] = useState<string>('')
  const [coverSize, setCoverSize] = useState<string>('')
  const [now, setNow] = useState(Date.now())
  const [swipeOffset, setSwipeOffset] = useState(0)
  const [isSwiping, setIsSwiping] = useState(false)
  const [swipeStartedSelected, setSwipeStartedSelected] = useState(false)
  const [swipeActionActive, setSwipeActionActive] = useState(false)
  const toggleTaskSelection = useStore((s) => s.toggleTaskSelection)
  const alwaysShowRetryButton = useStore((s) => s.settings.alwaysShowRetryButton)
  const touchStartRef = useRef<{ x: number; y: number } | null>(null)
  const swipeResetTimerRef = useRef<number | null>(null)
  const suppressClickUntilRef = useRef(0)
  const horizontalSwipeRef = useRef(false)

  const isTagScrollTarget = (target: EventTarget | null) => {
    return target instanceof Element && Boolean(target.closest('[data-tag-scroll-area]'))
  }

  const handleTouchStart = (e: React.TouchEvent) => {
    if (isTagScrollTarget(e.target)) {
      touchStartRef.current = null
      horizontalSwipeRef.current = false
      setIsSwiping(false)
      setSwipeOffset(0)
      setSwipeActionActive(false)
      return
    }

    if (swipeResetTimerRef.current != null) {
      window.clearTimeout(swipeResetTimerRef.current)
      swipeResetTimerRef.current = null
    }
    touchStartRef.current = { x: e.touches[0].clientX, y: e.touches[0].clientY }
    horizontalSwipeRef.current = false
    setSwipeStartedSelected(Boolean(isSelected))
    setSwipeActionActive(false)
    setIsSwiping(true)
  }

  const handleTouchMove = (e: React.TouchEvent) => {
    if (isTagScrollTarget(e.target)) return
    if (!touchStartRef.current) return
    const deltaX = e.touches[0].clientX - touchStartRef.current.x
    const deltaY = e.touches[0].clientY - touchStartRef.current.y
    
    // 如果主要是水平滑动
    if (Math.abs(deltaX) > Math.abs(deltaY) && Math.abs(deltaX) > 10) {
      horizontalSwipeRef.current = true
      e.preventDefault()
      // 限制滑动距离，例如最大 60px
      const boundedOffset = Math.max(-60, Math.min(60, deltaX))
      setSwipeOffset(boundedOffset)
      setSwipeActionActive(Math.abs(deltaX) >= 40)
    }
  }

  const handleTouchEnd = (e: React.TouchEvent) => {
    if (isTagScrollTarget(e.target)) {
      touchStartRef.current = null
      horizontalSwipeRef.current = false
      setIsSwiping(false)
      setSwipeOffset(0)
      setSwipeActionActive(false)
      return
    }

    setIsSwiping(false)
    setSwipeOffset(0)
    
    if (!touchStartRef.current) return
    const deltaX = e.changedTouches[0].clientX - touchStartRef.current.x
    touchStartRef.current = null
    const isSwipeAction = horizontalSwipeRef.current && Math.abs(deltaX) > 40
    horizontalSwipeRef.current = false
    setSwipeActionActive(isSwipeAction)
    swipeResetTimerRef.current = window.setTimeout(() => {
      setSwipeActionActive(false)
      swipeResetTimerRef.current = null
    }, 220)

    // 如果是水平滑动，且垂直偏移较小，认为是滑动选择
    if (isSwipeAction) {
      suppressClickUntilRef.current = Date.now() + 350
      e.preventDefault()
      e.stopPropagation()
      toggleTaskSelection(task.id)
    }
  }

  const handleTouchCancel = () => {
    touchStartRef.current = null
    horizontalSwipeRef.current = false
    setIsSwiping(false)
    setSwipeOffset(0)
    setSwipeActionActive(false)
  }

  useEffect(() => () => {
    if (swipeResetTimerRef.current != null) {
      window.clearTimeout(swipeResetTimerRef.current)
    }
  }, [])

  // 定时更新运行中任务的计时
  useEffect(() => {
    if (task.status !== 'running' && !(task.status === 'error' && (task.falRecoverable || task.customRecoverable))) return
    const id = setInterval(() => setNow(Date.now()), 1000)
    setNow(Date.now())
    return () => clearInterval(id)
  }, [task.customRecoverable, task.falRecoverable, task.status])

  // 加载缩略图
  useEffect(() => {
    setCoverRatio('')
    setCoverSize('')
    setThumbSrc('')

    let cancelled = false
    const imageId = task.outputImages?.[0]
    let unsubscribe: (() => void) | undefined

    const applyThumbnail = (thumbnail: { dataUrl: string; width?: number; height?: number }) => {
      if (cancelled) return
      setThumbSrc(thumbnail.dataUrl)
      if (thumbnail.width && thumbnail.height) {
        setCoverRatio(formatImageRatio(thumbnail.width, thumbnail.height))
        setCoverSize(`${thumbnail.width}×${thumbnail.height}`)
      }
    }

    if (imageId) {
      unsubscribe = subscribeImageThumbnail(imageId, applyThumbnail)
      ensureImageThumbnailCached(imageId).then((thumbnail) => {
        if (cancelled || !thumbnail) return
        applyThumbnail(thumbnail)
      }).catch(() => {
        if (!cancelled) setThumbSrc('')
      })
    }

    return () => {
      cancelled = true
      unsubscribe?.()
    }
  }, [task.outputImages])

  const duration = (() => {
    let seconds: number
    if (task.status === 'running') {
      seconds = Math.floor((now - task.createdAt) / 1000)
    } else if (task.elapsed != null) {
      seconds = Math.floor(task.elapsed / 1000)
    } else {
      return '00:00'
    }
    const mm = String(Math.floor(seconds / 60)).padStart(2, '0')
    const ss = String(seconds % 60).padStart(2, '0')
    return `${mm}:${ss}`
  })()
  const isSwipeReady = Math.abs(swipeOffset) >= 40
  const showSwipeAction = isSwipeReady || swipeActionActive
  const isFalReconnecting = task.status === 'error' && Boolean(task.falRecoverable)
  const isCustomReconnecting = task.status === 'error' && Boolean(task.customRecoverable)
  const isReconnecting = isFalReconnecting || isCustomReconnecting
  const showRunningTimer = task.status === 'running' || isFalReconnecting || isCustomReconnecting
  const swipeBgClass = showSwipeAction
    ? swipeStartedSelected
      ? 'bg-gray-500 dark:bg-gray-600'
      : 'bg-blue-500'
    : 'bg-gray-200 dark:bg-gray-700'

  const coverImageId = task.outputImages?.[0]
  const coverActualParams = coverImageId ? task.actualParamsByImage?.[coverImageId] ?? task.actualParams : task.actualParams
  const qualityDisplay = getParamDisplay(task, 'quality', coverActualParams)
  const sizeDisplay = getParamDisplay(task, 'size', coverActualParams)
  const savedCoverDimensions = coverImageId ? task.outputImageDimensions?.[coverImageId] : undefined
  const actualCoverSize = savedCoverDimensions
    ? `${savedCoverDimensions.width}×${savedCoverDimensions.height}`
    : coverSize
  const resolvedSizeDisplay = task.params.size === 'auto' && actualCoverSize
    ? actualCoverSize
    : sizeDisplay.isMismatch || sizeDisplay.isAutoResolved
    ? sizeDisplay.displayValue
    : ''
  const sizeBadgeTooltip = task.params.size === 'auto' && actualCoverSize
    ? '输出图片实际像素尺寸'
    : 'API 实际响应值'
  const outputCountLabel = task.outputImages.length > 0 ? `${task.outputImages.length} 张` : `${task.params.n} 张`
  const showOutputCount = task.outputImages.length > 1 || task.params.n > 1
  const operationLabel = task.maskImageId || task.operation === 'edit'
    ? '局部重绘'
    : task.inputImageIds.length > 0 || task.operation === 'reference'
    ? '参考图生图'
    : '文生图'
  const operationRoute = task.maskImageId || task.operation === 'edit'
    ? '/web/edit'
    : task.inputImageIds.length > 0 || task.operation === 'reference'
    ? '/web/image'
    : '/web/generate'

  return (
    <div className="relative rounded-xl">
      {/* 侧滑底图 */}
      <div
        className={`absolute inset-0 rounded-xl flex items-center transition-opacity duration-200 pointer-events-none ${
          isSwiping || swipeOffset || swipeActionActive ? 'opacity-100' : 'opacity-0'
        } ${swipeBgClass} ${
          swipeOffset > 0 ? 'justify-start pl-6' : 'justify-end pr-6'
        }`}
      >
        <svg className={`w-8 h-8 transition-transform duration-150 ${showSwipeAction ? 'scale-110 text-white' : 'scale-90 text-white/60'}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
          {swipeStartedSelected && showSwipeAction ? (
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          ) : (
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
          )}
        </svg>
      </div>

      <div
        className={`relative bg-white/90 dark:bg-white/[0.045] rounded-xl border overflow-hidden cursor-pointer duration-200 hover:shadow-md dark:hover:bg-white/[0.07] ${
          !isSwiping ? 'transition-[box-shadow,border-color,background-color,transform]' : 'transition-[box-shadow,border-color,background-color]'
        } ${
          task.status === 'running'
            ? 'border-blue-400 generating'
            : isSelected
            ? 'border-blue-500 shadow-md ring-2 ring-blue-500/50'
            : 'border-gray-200 dark:border-white/[0.08] hover:border-gray-300 dark:hover:border-white/[0.18]'
        }`}
        style={{
          transform: swipeOffset ? `translateX(${swipeOffset}px)` : undefined,
        }}
        onClick={(e) => {
          if (Date.now() < suppressClickUntilRef.current) {
            e.preventDefault()
            e.stopPropagation()
            return
          }
          onClick(e)
        }}
        onTouchStart={handleTouchStart}
        onTouchMove={handleTouchMove}
        onTouchEnd={handleTouchEnd}
        onTouchCancel={handleTouchCancel}
      >
        {/* 选中时的角标 */}
      {isSelected && (
        <div className="absolute top-2 right-2 z-10 w-5 h-5 bg-blue-500 rounded-full flex items-center justify-center shadow-sm">
          <svg className="w-3 h-3 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
          </svg>
        </div>
      )}
      <div className="flex h-44">
        {/* 左侧图片区域 */}
        <div className="w-44 min-w-[11rem] h-full bg-gray-100 dark:bg-black/25 relative flex items-center justify-center overflow-hidden flex-shrink-0">
          {task.status === 'running' && (
            <div className="flex flex-col items-center gap-2">
              <svg
                className="w-8 h-8 text-blue-400 animate-spin"
                fill="none"
                viewBox="0 0 24 24"
              >
                <circle
                  className="opacity-25"
                  cx="12"
                  cy="12"
                  r="10"
                  stroke="currentColor"
                  strokeWidth="4"
                />
                <path
                  className="opacity-75"
                  fill="currentColor"
                  d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                />
              </svg>
              <span className="text-xs text-gray-400 dark:text-gray-500">生成中...</span>
            </div>
          )}
          {task.status === 'error' && isReconnecting && (
            <div className="flex flex-col items-center gap-1 px-2">
              <svg
                className="w-7 h-7 text-yellow-400"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
                />
              </svg>
              <span className="text-xs text-yellow-500 text-center leading-tight">
                重连中
              </span>
            </div>
          )}
          {task.status === 'error' && !isReconnecting && (
            <div className="flex flex-col items-center gap-1 px-2">
              <svg
                className="w-7 h-7 text-red-400"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
                />
              </svg>
              <span className="text-xs text-red-400 text-center leading-tight">
                失败
              </span>
            </div>
          )}
          {task.status === 'done' && thumbSrc && (
            <>
              <img
                src={thumbSrc}
                data-image-id={task.outputImages[0]}
                className="saveable-image w-full h-full object-cover"
                loading="lazy"
                alt=""
              />
              {task.outputImages.length > 1 && (
                <span className="absolute bottom-1 right-1 bg-black/60 text-white text-xs px-1.5 py-0.5 rounded">
                  {task.outputImages.length}
                </span>
              )}
            </>
          )}
          {task.status === 'done' && !thumbSrc && (
            <svg
              className="w-8 h-8 text-gray-300"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={1.5}
                d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"
              />
            </svg>
          )}
          {/* 运行中显示耗时，完成后显示封面图比例与分辨率标签 */}
          <div className="absolute top-1.5 left-1.5 flex items-center gap-1">
            {showRunningTimer || task.status !== 'done' || !coverRatio || !coverSize ? (
              <span className="flex items-center gap-1 bg-black/50 text-white text-[10px] sm:text-xs px-1.5 py-0.5 rounded backdrop-blur-sm font-mono">
                <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                {duration}
              </span>
            ) : (
              <>
                <span className="bg-black/50 text-white text-[10px] sm:text-xs px-1.5 py-0.5 rounded backdrop-blur-sm font-mono">
                  {coverRatio}
                </span>
                <span className="bg-black/50 text-white/90 text-[10px] sm:text-xs px-1.5 py-0.5 rounded backdrop-blur-sm font-medium">
                  {coverSize}
                </span>
              </>
            )}
          </div>
        </div>

        {/* 右侧信息区域 */}
        <div className="flex-1 p-3.5 flex flex-col min-w-0">
          <div className="mb-2 flex items-center gap-2 text-[11px] text-gray-400 dark:text-gray-500">
            <span className={`h-1.5 w-1.5 rounded-full ${
              task.status === 'done'
                ? 'bg-emerald-400'
                : task.status === 'running'
                ? 'bg-blue-400'
                : isReconnecting
                ? 'bg-yellow-400'
                : 'bg-red-400'
            }`} />
            <span>
              {task.status === 'done'
                ? '已完成'
                : task.status === 'running'
                ? '生成中'
                : isReconnecting
                ? '重连中'
                : '失败'}
            </span>
            <span className="font-mono">{duration}</span>
          </div>
          <div className="min-h-0 flex-1 overflow-hidden">
            {task.status === 'error' && task.error ? (
              <p className="line-clamp-3 text-xs leading-5 text-red-400 break-words">
                {task.error}
              </p>
            ) : (
              <p className="line-clamp-4 text-xs leading-5 text-gray-500 dark:text-gray-400 break-words">
                {task.prompt || '无提示词'}
              </p>
            )}
          </div>
          <div className="mt-auto flex flex-col gap-1.5">
            {/* 参数与信息：横向滚动 */}
            <div
              data-tag-scroll-area
              className="flex flex-wrap gap-1.5 pt-0.5 min-w-0"
              onTouchStart={(e) => e.stopPropagation()}
              onTouchMove={(e) => e.stopPropagation()}
              onTouchEnd={(e) => e.stopPropagation()}
              onTouchCancel={(e) => e.stopPropagation()}
            >
              <span
                className={`flex items-center gap-1 px-1.5 py-0.5 rounded text-xs flex-shrink-0 ${
                task.maskImageId || task.operation === 'edit'
                  ? 'bg-blue-50 text-blue-600 dark:bg-blue-500/10 dark:text-blue-400'
                  : task.inputImageIds.length > 0 || task.operation === 'reference'
                  ? 'bg-emerald-50 text-emerald-600 dark:bg-emerald-500/10 dark:text-emerald-400'
                  : 'bg-gray-100 text-gray-500 dark:bg-white/[0.04] dark:text-gray-400'
                }`}
                title={operationRoute}
              >
                {(task.maskImageId || task.operation === 'edit') && (
                  <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                  </svg>
                )}
                {operationLabel}
              </span>
              <span
                className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-gray-100 dark:bg-white/[0.04] text-xs flex-shrink-0"
                title={`请求质量：${qualityDisplay.requestedValue}${qualityDisplay.isMismatch || qualityDisplay.isAutoResolved ? `，API 回传：${qualityDisplay.displayValue}` : ''}`}
              >
                <span className="text-gray-400 dark:text-gray-500">质量</span>
                <span className="text-gray-600 dark:text-gray-300">{qualityDisplay.requestedValue}</span>
                {(qualityDisplay.isMismatch || qualityDisplay.isAutoResolved) && (
                  <>
                    <span className="text-gray-300 dark:text-gray-600">→</span>
                    <ActualValueBadge value={qualityDisplay.displayValue} className="px-1 rounded-sm" />
                  </>
                )}
              </span>
              <span
                className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-gray-100 dark:bg-white/[0.04] text-xs flex-shrink-0"
                title={`请求尺寸：${sizeDisplay.requestedValue}${resolvedSizeDisplay ? `，实际：${resolvedSizeDisplay}` : ''}`}
              >
                <span className="text-gray-400 dark:text-gray-500">尺寸</span>
                <span className="text-gray-600 dark:text-gray-300">{sizeDisplay.requestedValue}</span>
                {resolvedSizeDisplay && (
                  <>
                    <span className="text-gray-300 dark:text-gray-600">→</span>
                    <ActualValueBadge value={resolvedSizeDisplay} tooltip={sizeBadgeTooltip} className="px-1 rounded-sm" />
                  </>
                )}
              </span>
              {showOutputCount && (
                <span className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-gray-100 dark:bg-white/[0.04] text-xs flex-shrink-0">
                  <span className="text-gray-400 dark:text-gray-500">输出</span>
                  <span className="text-gray-600 dark:text-gray-300">{outputCountLabel}</span>
                </span>
              )}
            </div>
            {/* 操作按钮 */}
            <div
              className="flex w-full items-center justify-between flex-shrink-0 mt-0.5 sm:w-auto sm:justify-end sm:gap-1"
              onClick={(e) => e.stopPropagation()}
            >
              {((task.status === 'error' && !isReconnecting) || alwaysShowRetryButton) && (
                <button
                  onClick={() => retryTask(task)}
                  className="p-1.5 rounded-md text-gray-400 opacity-70 transition hover:bg-blue-50 hover:text-blue-500 hover:opacity-100 dark:hover:bg-blue-950/30"
                  title="重试任务"
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                  </svg>
                </button>
              )}
              <button
                onClick={onReuse}
                className="p-1.5 rounded-md text-gray-400 opacity-70 transition hover:bg-blue-50 hover:text-blue-500 hover:opacity-100 dark:hover:bg-blue-950/30"
                title="复用配置"
              >
                <svg
                  className="w-4 h-4"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M3 10h10a8 8 0 018 8v2M3 10l6 6m-6-6l6-6"
                  />
                </svg>
              </button>
              <button
                onClick={onEditOutputs}
                className="p-1.5 rounded-md text-gray-400 opacity-70 transition hover:bg-green-50 hover:text-green-500 hover:opacity-100 disabled:opacity-30 dark:hover:bg-green-950/30"
                title="编辑输出"
                disabled={!task.outputImages?.length}
              >
                <svg
                  className="w-4 h-4"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"
                  />
                </svg>
              </button>
              <button
                onClick={onDelete}
                className="p-1.5 rounded-md text-gray-400 opacity-70 transition hover:bg-red-50 hover:text-red-500 hover:opacity-100 dark:hover:bg-red-950/30"
                title="删除记录"
              >
                <svg
                  className="w-4 h-4"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
                  />
                </svg>
              </button>
            </div>
          </div>
        </div>
      </div>
      </div>
    </div>
  )
}
