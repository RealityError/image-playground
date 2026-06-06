import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { MouseEvent as ReactMouseEvent, TouchEvent as ReactTouchEvent } from 'react'
import { useStore, reuseConfig, editOutputs, removeTask } from '../store'
import type { TaskRecord } from '../types'
import TaskCard from './TaskCard'

type SelectionBox = { startPageX: number; startPageY: number; currentPageX: number; currentPageY: number }
type DragCardRect = { taskId: string; left: number; right: number; top: number; bottom: number }
type TaskClickEvent = ReactMouseEvent | ReactTouchEvent
type TaskAction = (task: TaskRecord) => void
type TaskClickAction = (task: TaskRecord, event: TaskClickEvent) => void

const makeSelectionKey = (ids: string[]) => ids.join('\0')

const TaskGridItem = memo(function TaskGridItem({
  task,
  onTaskClick,
  onReuseTask,
  onEditOutputsTask,
  onDeleteTask,
}: {
  task: TaskRecord
  onTaskClick: TaskClickAction
  onReuseTask: TaskAction
  onEditOutputsTask: TaskAction
  onDeleteTask: TaskAction
}) {
  const isSelected = useStore((s) => s.selectedTaskIds.includes(task.id))

  const handleClick = useCallback((event: TaskClickEvent) => {
    onTaskClick(task, event)
  }, [onTaskClick, task])

  const handleReuse = useCallback(() => {
    onReuseTask(task)
  }, [onReuseTask, task])

  const handleEditOutputs = useCallback(() => {
    onEditOutputsTask(task)
  }, [onEditOutputsTask, task])

  const handleDelete = useCallback(() => {
    onDeleteTask(task)
  }, [onDeleteTask, task])

  return (
    <div className="task-card-wrapper" data-task-id={task.id}>
      <TaskCard
        task={task}
        onClick={handleClick}
        onReuse={handleReuse}
        onEditOutputs={handleEditOutputs}
        onDelete={handleDelete}
        isSelected={isSelected}
      />
    </div>
  )
})

export default function TaskGrid() {
  const tasks = useStore((s) => s.tasks)
  const searchQuery = useStore((s) => s.searchQuery)
  const filterStatus = useStore((s) => s.filterStatus)
  const setDetailTaskId = useStore((s) => s.setDetailTaskId)
  const setConfirmDialog = useStore((s) => s.setConfirmDialog)
  const setSelectedTaskIds = useStore((s) => s.setSelectedTaskIds)
  const clearSelection = useStore((s) => s.clearSelection)
  const rootRef = useRef<HTMLDivElement>(null)
  const gridRef = useRef<HTMLDivElement>(null)
  const [selectionBox, setSelectionBox] = useState<SelectionBox | null>(null)
  const isDragging = useRef(false)
  const dragStart = useRef<{ pageX: number; pageY: number } | null>(null)
  const lastClientPoint = useRef<{ x: number; y: number } | null>(null)
  const hasDragged = useRef(false)
  const dragCardRects = useRef<DragCardRect[]>([])
  const initialSelectionSet = useRef<Set<string>>(new Set())
  const lastAppliedSelectionKey = useRef('')
  const pendingSelectionPoint = useRef<{ pageX: number; pageY: number } | null>(null)
  const selectionFrameRef = useRef<number | null>(null)
  const dragScrollIntervalRef = useRef<number | null>(null)
  const dragScrollDirectionRef = useRef<-1 | 1 | null>(null)
  const lastToastTimeRef = useRef(0)
  const suppressClickUntil = useRef(0)
  const startedOnCard = useRef(false)
  const startedWithCtrl = useRef(false)
  const initialSelection = useRef<string[]>([])
  const isMac = /Mac|iPod|iPhone|iPad/.test(navigator.platform)

  const filteredTasks = useMemo(() => {
    const sorted = [...tasks].sort((a, b) => b.createdAt - a.createdAt)
    const q = searchQuery.trim().toLowerCase()
    
    return sorted.filter((t) => {
      const matchStatus = filterStatus === 'all' || t.status === filterStatus
      if (!matchStatus) return false
      
      if (!q) return true
      const prompt = (t.prompt || '').toLowerCase()
      const paramStr = JSON.stringify(t.params).toLowerCase()
      return prompt.includes(q) || paramStr.includes(q)
    })
  }, [tasks, searchQuery, filterStatus])

  const handleDelete = useCallback((task: TaskRecord) => {
    setConfirmDialog({
      title: '删除记录',
      message: '确定要删除这条记录吗？关联的图片资源也会被清理（如果没有其他任务引用）。',
      action: () => removeTask(task),
    })
  }, [setConfirmDialog])

  const handleTaskClick = useCallback((task: TaskRecord, event: TaskClickEvent) => {
    if (Date.now() < suppressClickUntil.current) {
      event.preventDefault()
      return
    }
    suppressClickUntil.current = 0
    const isCtrl = isMac ? event.metaKey : event.ctrlKey
    const selectedCount = useStore.getState().selectedTaskIds.length
    if (isCtrl) {
      useStore.getState().toggleTaskSelection(task.id)
    } else if (selectedCount > 0) {
      clearSelection()
      setDetailTaskId(task.id)
    } else {
      setDetailTaskId(task.id)
    }
  }, [clearSelection, isMac, setDetailTaskId])

  const handleReuseTask = useCallback((task: TaskRecord) => {
    reuseConfig(task)
  }, [])

  const handleEditOutputsTask = useCallback((task: TaskRecord) => {
    editOutputs(task)
  }, [])

  const getPagePoint = (clientX: number, clientY: number) => ({
    pageX: clientX + window.scrollX,
    pageY: clientY + window.scrollY,
  })

  const cacheDragCardRects = () => {
    if (!gridRef.current) {
      dragCardRects.current = []
      return
    }
    dragCardRects.current = Array.from(gridRef.current.querySelectorAll<HTMLElement>('.task-card-wrapper'))
      .map((card) => {
        const taskId = card.dataset.taskId
        if (!taskId) return null
        const rect = card.getBoundingClientRect()
        return {
          taskId,
          left: rect.left + window.scrollX,
          right: rect.right + window.scrollX,
          top: rect.top + window.scrollY,
          bottom: rect.bottom + window.scrollY,
        }
      })
      .filter((rect): rect is DragCardRect => Boolean(rect))
  }

  const beginSelection = (target: HTMLElement, clientX: number, clientY: number, isCtrl: boolean) => {
    const point = getPagePoint(clientX, clientY)

    startedOnCard.current = Boolean(target.closest('.task-card-wrapper'))
    startedWithCtrl.current = isCtrl
    initialSelection.current = [...useStore.getState().selectedTaskIds]
    initialSelectionSet.current = new Set(initialSelection.current)
    lastAppliedSelectionKey.current = makeSelectionKey(initialSelection.current)

    isDragging.current = true
    hasDragged.current = false
    dragStart.current = point
    lastClientPoint.current = { x: clientX, y: clientY }
    cacheDragCardRects()
    document.body.classList.add('select-none')
    document.body.classList.add('drag-selecting')
    setSelectionBox({
      startPageX: point.pageX,
      startPageY: point.pageY,
      currentPageX: point.pageX,
      currentPageY: point.pageY,
    })
  }

  const updateSelectionFromPoint = (pageX: number, pageY: number) => {
    const start = dragStart.current
    if (!start) return

    const minX = Math.min(start.pageX, pageX)
    const maxX = Math.max(start.pageX, pageX)
    const minY = Math.min(start.pageY, pageY)
    const maxY = Math.max(start.pageY, pageY)

    const newSelected = new Set(initialSelection.current)
    const initialSelected = initialSelectionSet.current

    dragCardRects.current.forEach((card) => {
      const isIntersecting =
        minX < card.right && maxX > card.left && minY < card.bottom && maxY > card.top

      if (isIntersecting) {
        if (initialSelected.has(card.taskId)) {
          newSelected.delete(card.taskId)
        } else {
          newSelected.add(card.taskId)
        }
      } else if (!initialSelected.has(card.taskId)) {
        newSelected.delete(card.taskId)
      }
    })

    const nextSelected = Array.from(newSelected)
    const nextSelectionKey = makeSelectionKey(nextSelected)
    if (nextSelectionKey === lastAppliedSelectionKey.current) return
    lastAppliedSelectionKey.current = nextSelectionKey
    setSelectedTaskIds(nextSelected)
  }

  const cancelSelectionFrame = () => {
    if (selectionFrameRef.current != null) {
      window.cancelAnimationFrame(selectionFrameRef.current)
      selectionFrameRef.current = null
    }
    pendingSelectionPoint.current = null
  }

  const scheduleSelectionUpdate = (pageX: number, pageY: number) => {
    pendingSelectionPoint.current = { pageX, pageY }
    if (selectionFrameRef.current != null) return
    selectionFrameRef.current = window.requestAnimationFrame(() => {
      selectionFrameRef.current = null
      const point = pendingSelectionPoint.current
      pendingSelectionPoint.current = null
      const start = dragStart.current
      if (!point || !start || !isDragging.current || !hasDragged.current) return
      setSelectionBox({
        startPageX: start.pageX,
        startPageY: start.pageY,
        currentPageX: point.pageX,
        currentPageY: point.pageY,
      })
      updateSelectionFromPoint(point.pageX, point.pageY)
    })
  }

  useEffect(() => {
    const stopDragScroll = () => {
      if (dragScrollIntervalRef.current) {
        clearInterval(dragScrollIntervalRef.current)
        dragScrollIntervalRef.current = null
      }
      dragScrollDirectionRef.current = null
    }

    const startDragScroll = (direction: -1 | 1) => {
      if (dragScrollIntervalRef.current && dragScrollDirectionRef.current === direction) return
      stopDragScroll()
      dragScrollDirectionRef.current = direction
      dragScrollIntervalRef.current = window.setInterval(() => {
        window.scrollBy({ top: direction * 15, behavior: 'instant' })
      }, 16)
    }

    const endSelection = (clearEmptySurfaceClick = false, suppressClick = false) => {
      if (isDragging.current) {
        document.body.classList.remove('select-none')
        document.body.classList.remove('drag-selecting')
      }
      if (isDragging.current && clearEmptySurfaceClick && !hasDragged.current && !startedOnCard.current && !startedWithCtrl.current) {
        clearSelection()
      }
      if (isDragging.current && suppressClick && hasDragged.current) {
        suppressClickUntil.current = Date.now() + 250
      }
      stopDragScroll()
      cancelSelectionFrame()
      isDragging.current = false
      dragStart.current = null
      lastClientPoint.current = null
      dragCardRects.current = []
      initialSelectionSet.current = new Set()
      lastAppliedSelectionKey.current = ''
      setSelectionBox(null)
    }

    const getEventElement = (e: MouseEvent) => {
      if (e.target instanceof Element) return e.target
      return document.elementFromPoint(e.clientX, e.clientY)
    }

    const handleDocumentMouseDown = (e: MouseEvent) => {
      if (e.button !== 0) return
      const target = getEventElement(e)
      if (!target) return
      if (!target.closest('[data-drag-select-surface]')) return
      if (target.closest('[data-input-bar]')) return
      if (target.closest('[data-no-drag-select], [data-lightbox-root]')) return
      if (target.closest('button, a, input, textarea, select')) return

      const isCtrl = isMac ? e.metaKey : e.ctrlKey
      beginSelection(target as HTMLElement, e.clientX, e.clientY, isCtrl)
      e.preventDefault()
    }

    const handleDocumentMouseMove = (e: MouseEvent) => {
      if (!isDragging.current || !dragStart.current) return

      const start = dragStart.current
      const point = getPagePoint(e.clientX, e.clientY)
      lastClientPoint.current = { x: e.clientX, y: e.clientY }
      const distance = Math.hypot(point.pageX - start.pageX, point.pageY - start.pageY)
      if (distance < 6 && !hasDragged.current) return

      hasDragged.current = true
      scheduleSelectionUpdate(point.pageX, point.pageY)
      e.preventDefault()

      const scrollThreshold = 40
      if (e.clientY < scrollThreshold) {
        startDragScroll(-1)
      } else if (e.clientY > window.innerHeight - scrollThreshold) {
        startDragScroll(1)
      } else {
        stopDragScroll()
      }
    }

    const handleDocumentScroll = () => {
      if (!isDragging.current || !dragStart.current || !lastClientPoint.current || !hasDragged.current) return

      const point = getPagePoint(lastClientPoint.current.x, lastClientPoint.current.y)
      scheduleSelectionUpdate(point.pageX, point.pageY)
    }

    const handleDocumentWheel = (e: WheelEvent) => {
      if (!isDragging.current) return
      if ((e.buttons & 1) === 0) {
        endSelection()
        return
      }
      if (!hasDragged.current) return
      if (!e.ctrlKey && !e.metaKey) return

      e.preventDefault()
      const now = Date.now()
      if (now - lastToastTimeRef.current > 3000) {
        lastToastTimeRef.current = now
        const keyName = isMac ? '⌘' : 'Ctrl'
        useStore.getState().showToast(`松开 ${keyName} 键使用滚轮，或拖至边缘自动滚动`, 'info')
      }
    }

    const handleDocumentMouseUp = () => {
      endSelection(true, true)
    }

    document.addEventListener('mousedown', handleDocumentMouseDown, true)
    document.addEventListener('mousemove', handleDocumentMouseMove, true)
    document.addEventListener('mouseup', handleDocumentMouseUp, true)
    document.addEventListener('wheel', handleDocumentWheel, { capture: true, passive: false })
    window.addEventListener('scroll', handleDocumentScroll, true)
    return () => {
      stopDragScroll()
      cancelSelectionFrame()
      document.removeEventListener('mousedown', handleDocumentMouseDown, true)
      document.removeEventListener('mousemove', handleDocumentMouseMove, true)
      document.removeEventListener('mouseup', handleDocumentMouseUp, true)
      document.removeEventListener('wheel', handleDocumentWheel, true)
      window.removeEventListener('scroll', handleDocumentScroll, true)
    }
  }, [clearSelection, isMac])

  if (!filteredTasks.length) {
    return (
      <div className="text-center py-20 text-gray-400 dark:text-gray-500">
        {searchQuery ? (
          <p className="text-sm">没有找到匹配的记录</p>
        ) : (
          <>
            <svg
              className="w-16 h-16 mx-auto mb-4 text-gray-200 dark:text-gray-700"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={1}
                d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"
              />
            </svg>
            <p className="text-sm">输入提示词开始生成图片</p>
          </>
        )}
      </div>
    )
  }

  return (
    <div 
      ref={rootRef}
      data-task-grid-root
      className="relative min-h-[50vh]"
    >
      <div ref={gridRef} className="grid grid-cols-1 gap-4 pb-10 sm:grid-cols-2 xl:grid-cols-3">
        {filteredTasks.map((task) => (
          <TaskGridItem
            key={task.id}
            task={task}
            onTaskClick={handleTaskClick}
            onReuseTask={handleReuseTask}
            onEditOutputsTask={handleEditOutputsTask}
            onDeleteTask={handleDelete}
          />
        ))}
      </div>
      {selectionBox && (
        <div
          className="fixed bg-blue-500/20 border border-blue-500/50 pointer-events-none z-[30]"
          style={{
            left: Math.min(selectionBox.startPageX, selectionBox.currentPageX) - window.scrollX,
            top: Math.min(selectionBox.startPageY, selectionBox.currentPageY) - window.scrollY,
            width: Math.abs(selectionBox.currentPageX - selectionBox.startPageX),
            height: Math.abs(selectionBox.currentPageY - selectionBox.startPageY),
          }}
        />
      )}
    </div>
  )
}
