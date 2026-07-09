import { useMemo } from 'react'
import { removeMultipleTasks, useStore } from '../store'
import { getFailedTaskIds } from '../lib/taskFilters'

export default function SearchBar() {
  const searchQuery = useStore((s) => s.searchQuery)
  const setSearchQuery = useStore((s) => s.setSearchQuery)
  const filterStatus = useStore((s) => s.filterStatus)
  const setFilterStatus = useStore((s) => s.setFilterStatus)
  const tasks = useStore((s) => s.tasks)
  const setConfirmDialog = useStore((s) => s.setConfirmDialog)
  const clearSelection = useStore((s) => s.clearSelection)
  const failedTaskIds = useMemo(() => getFailedTaskIds(tasks), [tasks])

  const handleClearFailedTasks = () => {
    if (!failedTaskIds.length) return

    setConfirmDialog({
      title: '清理失败记录',
      message: `确定要删除 ${failedTaskIds.length} 条失败记录吗？已完成和生成中的记录不会受影响。`,
      confirmText: '清理',
      tone: 'warning',
      action: () => {
        void removeMultipleTasks(failedTaskIds)
        clearSelection()
      },
    })
  }

  return (
    <div data-no-drag-select className="mt-5 mb-5 flex flex-col gap-2 sm:flex-row sm:items-center">
      <div className="flex gap-2 flex-shrink-0 z-20">
        <select
          value={filterStatus}
          onChange={(e) => setFilterStatus(e.target.value as any)}
          className="h-10 px-3 rounded-xl border border-gray-200/80 dark:border-white/[0.08] bg-white/80 dark:bg-white/[0.04] hover:bg-white dark:hover:bg-white/[0.07] text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/30 focus:border-blue-400 transition text-gray-700 dark:text-gray-300"
        >
          <option value="all">全部状态</option>
          <option value="done">已完成</option>
          <option value="running">生成中</option>
          <option value="error">失败</option>
        </select>
        {failedTaskIds.length > 0 && (
          <button
            type="button"
            onClick={handleClearFailedTasks}
            className="h-10 rounded-xl border border-yellow-200/80 bg-yellow-50/80 px-3 text-sm font-medium text-yellow-700 transition hover:bg-yellow-100 dark:border-yellow-500/20 dark:bg-yellow-500/10 dark:text-yellow-300 dark:hover:bg-yellow-500/20"
            title={`清理 ${failedTaskIds.length} 条失败记录`}
          >
            清理失败
          </button>
        )}
      </div>
      <div className="relative flex-1 z-10 sm:max-w-3xl">
        <svg
          className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 dark:text-gray-500"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
        </svg>
        <input
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          type="text"
          placeholder="搜索提示词..."
          className="h-10 w-full pl-10 pr-4 rounded-xl border border-gray-200/80 dark:border-white/[0.08] bg-white/80 dark:bg-white/[0.04] text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/30 focus:border-blue-400 transition"
        />
      </div>
    </div>
  )
}
