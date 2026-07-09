import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useStore, exportData, importData, clearData } from '../store'
import { useCloseOnEscape } from '../hooks/useCloseOnEscape'
import { usePreventBackgroundScroll } from '../hooks/usePreventBackgroundScroll'
import { dismissAllTooltips } from '../lib/tooltipDismiss'
import Select from './Select'
import { CloseIcon, GithubIcon, ExportIcon, ImportIcon, TrashIcon } from './icons'

type SettingsTab = 'general' | 'data' | 'about'

export default function SettingsModal() {
  const showSettings = useStore((s) => s.showSettings)
  const setShowSettings = useStore((s) => s.setShowSettings)
  const settings = useStore((s) => s.settings)
  const setSettings = useStore((s) => s.setSettings)
  const setConfirmDialog = useStore((s) => s.setConfirmDialog)
  const showToast = useStore((s) => s.showToast)

  const [activeTab, setActiveTab] = useState<SettingsTab>('general')
  const [importing, setImporting] = useState(false)
  const [exporting, setExporting] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const settingsScrollBoundaryRef = useRef<HTMLDivElement>(null)

  useCloseOnEscape(showSettings, () => setShowSettings(false))
  usePreventBackgroundScroll(showSettings)

  useEffect(() => {
    if (!settings.theme) return
    const root = document.documentElement
    if (settings.theme === 'dark') {
      root.classList.add('dark')
    } else if (settings.theme === 'light') {
      root.classList.remove('dark')
    } else {
      const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
      root.classList.toggle('dark', prefersDark)
    }
  }, [settings.theme])

  const handleClose = () => setShowSettings(false)

  const handleExport = async () => {
    if (exporting) return
    setExporting(true)
    try {
      await exportData({ exportConfig: true, exportTasks: true })
      showToast('导出成功', 'success')
    } catch (err) {
      showToast(err instanceof Error ? err.message : '导出失败', 'error')
    } finally {
      setExporting(false)
    }
  }

  const handleImportClick = () => {
    dismissAllTooltips()
    fileInputRef.current?.click()
  }

  const handleImportFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file || importing) return

    setConfirmDialog({
      title: '导入数据',
      message: '导入将替换当前的设置和本地任务缓存，不影响服务端数据。是否继续？',
      confirmText: '导入',
      tone: 'warning',
      action: async () => {
        setImporting(true)
        try {
          const ok = await importData(file, { importConfig: true, importTasks: true })
          if (ok) showToast('导入成功', 'success')
        } catch (err) {
          showToast(err instanceof Error ? err.message : '导入失败', 'error')
        } finally {
          setImporting(false)
        }
      },
    })
  }

  const handleClearTasks = () => {
    setConfirmDialog({
      title: '清空本地任务缓存',
      message: '将清空本地所有任务历史和缓存图片，不影响服务端数据。是否继续？',
      confirmText: '清空',
      tone: 'danger',
      action: () => {
        void clearData({ config: false, tasks: true })
      },
    })
  }

  if (!showSettings) return null

  return createPortal(
    <div data-no-drag-select className="fixed inset-0 z-[70] flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-black/30 backdrop-blur-sm animate-overlay-in"
        onClick={handleClose}
      />
      <div
        ref={settingsScrollBoundaryRef}
        className="relative z-10 w-full max-w-3xl rounded-3xl border border-white/50 bg-white/95 shadow-2xl ring-1 ring-black/5 animate-modal-in dark:border-white/[0.08] dark:bg-gray-900/95 dark:ring-white/10 flex h-[85vh] sm:h-[600px] flex-col overflow-hidden"
      >
        {/* Header */}
        <div className="flex items-center justify-between shrink-0 p-5 border-b border-gray-100 dark:border-white/[0.08]">
          <h3 className="text-lg font-bold text-gray-800 dark:text-gray-100 flex items-center gap-2">
            <svg className="w-5 h-5 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
            设置
          </h3>
          <div className="flex items-center gap-3">
            <button
              onClick={handleClose}
              className="rounded-full p-1 text-gray-400 transition hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-white/[0.06] dark:hover:text-gray-200"
              aria-label="关闭"
            >
              <CloseIcon className="h-5 w-5" />
            </button>
          </div>
        </div>

        <div className="flex flex-1 min-h-0 flex-col sm:flex-row">
          {/* Sidebar */}
          <div className="w-full sm:w-48 shrink-0 flex flex-col border-b sm:border-b-0 sm:border-r border-gray-100 dark:border-white/[0.08] bg-gray-50/50 dark:bg-white/[0.02]">
            <nav className="flex-1 overflow-x-auto sm:overflow-y-auto custom-scrollbar p-3 space-x-1 sm:space-x-0 sm:space-y-1 flex sm:flex-col">
              <TabButton
                active={activeTab === 'general'}
                onClick={() => setActiveTab('general')}
                label="习惯配置"
                icon={
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6v6l4 2m6-2a10 10 0 11-20 0 10 10 0 0120 0z" />
                  </svg>
                }
              />
              <TabButton
                active={activeTab === 'data'}
                onClick={() => setActiveTab('data')}
                label="数据管理"
                icon={
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 7v10c0 2 1 3 3 3h10c2 0 3-1 3-3V7M4 7l8-4 8 4M4 7l8 4 8-4" />
                  </svg>
                }
              />
              <TabButton
                active={activeTab === 'about'}
                onClick={() => setActiveTab('about')}
                label="关于"
                icon={
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                }
              />
            </nav>
          </div>

          {/* Content */}
          <div className="flex-1 flex flex-col min-w-0 min-h-0 bg-transparent relative overflow-hidden">
            <div className="flex-1 overflow-y-auto overscroll-contain custom-scrollbar p-5 sm:p-6">
              {activeTab === 'general' && (
                <div className="space-y-4">
                  <Row
                    title="主题"
                    description="浅色、深色或跟随系统"
                    control={
                      <div className="w-36">
                        <Select
                          value={settings.theme}
                          onChange={(val) => setSettings({ theme: val as 'system' | 'light' | 'dark' })}
                          options={[
                            { label: '跟随系统', value: 'system' },
                            { label: '浅色', value: 'light' },
                            { label: '深色', value: 'dark' },
                          ]}
                          className="w-full px-3 py-1.5 rounded-xl border border-gray-200/60 dark:border-white/[0.08] bg-white/50 dark:bg-white/[0.03] hover:bg-white dark:hover:bg-white/[0.06] text-xs transition-all duration-200 shadow-sm text-gray-700 dark:text-gray-200 outline-none"
                        />
                      </div>
                    }
                  />

                  <Row
                    title="任务提交方式"
                    description="选择 Enter 提交时，使用 Shift + Enter 换行；否则直接 Enter 换行。"
                    control={
                      <div className="w-32">
                        <Select
                          value={settings.enterSubmit ? 'enter' : 'ctrl-enter'}
                          onChange={(val) => setSettings({ enterSubmit: val === 'enter' })}
                          options={[
                            { label: 'Enter', value: 'enter' },
                            { label: navigator.userAgent.includes('Mac') ? 'Cmd + Enter' : 'Ctrl + Enter', value: 'ctrl-enter' },
                          ]}
                          className="w-full px-3 py-1.5 rounded-xl border border-gray-200/60 dark:border-white/[0.08] bg-white/50 dark:bg-white/[0.03] hover:bg-white dark:hover:bg-white/[0.06] text-xs transition-all duration-200 shadow-sm text-gray-700 dark:text-gray-200 outline-none"
                        />
                      </div>
                    }
                  />

                  <Row
                    title="提交任务后清空输入框"
                    description="开启后，提交成功创建任务时会清空提示词和参考图。"
                    control={
                      <Toggle
                        checked={settings.clearInputAfterSubmit}
                        onChange={(v) => setSettings({ clearInputAfterSubmit: v })}
                      />
                    }
                  />

                  <Row
                    title="重启后加载上次的输入框"
                    description="关闭后，不再持久化提示词和参考图，下次启动会使用空输入框。"
                    control={
                      <Toggle
                        checked={settings.persistInputOnRestart}
                        onChange={(v) => setSettings({ persistInputOnRestart: v })}
                      />
                    }
                  />

                  <Row
                    title="始终显示重试按钮"
                    description="在已完成任务上也显示重试按钮。"
                    control={
                      <Toggle
                        checked={settings.alwaysShowRetryButton}
                        onChange={(v) => setSettings({ alwaysShowRetryButton: v })}
                      />
                    }
                  />

                  <Row
                    title="严格遮罩合成"
                    description="编辑图片时，结果只保留遮罩区域的变化，非遮罩区域与原图完全一致。"
                    control={
                      <Toggle
                        checked={settings.strictMaskComposite}
                        onChange={(v) => setSettings({ strictMaskComposite: v })}
                      />
                    }
                  />
                </div>
              )}

              {activeTab === 'data' && (
                <div className="space-y-4">
                  <div>
                    <h4 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-1">导出 / 导入</h4>
                    <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">
                      导出将打包本地设置、任务历史和缓存图片为 ZIP 文件；导入将替换当前本地数据。不影响服务端数据。
                    </p>
                    <div className="flex flex-wrap gap-2">
                      <button
                        type="button"
                        onClick={handleExport}
                        disabled={exporting}
                        className="inline-flex items-center gap-2 rounded-xl bg-gray-100/80 px-4 py-2 text-sm font-medium text-gray-700 transition-all hover:bg-gray-200 hover:text-gray-900 dark:bg-white/[0.06] dark:text-gray-300 dark:hover:bg-white/[0.1] dark:hover:text-white disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        <ExportIcon className="h-4 w-4 opacity-70" />
                        {exporting ? '导出中...' : '导出数据'}
                      </button>
                      <button
                        type="button"
                        onClick={handleImportClick}
                        disabled={importing}
                        className="inline-flex items-center gap-2 rounded-xl bg-gray-100/80 px-4 py-2 text-sm font-medium text-gray-700 transition-all hover:bg-gray-200 hover:text-gray-900 dark:bg-white/[0.06] dark:text-gray-300 dark:hover:bg-white/[0.1] dark:hover:text-white disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        <ImportIcon className="h-4 w-4 opacity-70" />
                        {importing ? '导入中...' : '导入数据'}
                      </button>
                      <input
                        ref={fileInputRef}
                        type="file"
                        accept=".zip"
                        className="hidden"
                        onChange={handleImportFile}
                      />
                    </div>
                  </div>

                  <div className="pt-4 border-t border-gray-100 dark:border-white/[0.08]">
                    <h4 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-1">危险操作</h4>
                    <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">
                      清空本地任务历史和缓存图片，不影响服务端数据。
                    </p>
                    <button
                      type="button"
                      onClick={handleClearTasks}
                      className="inline-flex items-center gap-2 rounded-xl border border-red-200 bg-red-50/50 px-4 py-2 text-sm font-medium text-red-600 transition-all hover:bg-red-50 hover:border-red-300 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-400 dark:hover:bg-red-500/20"
                    >
                      <TrashIcon className="h-4 w-4" />
                      清空本地任务缓存
                    </button>
                  </div>
                </div>
              )}

              {activeTab === 'about' && (
                <div className="flex h-full min-h-[300px] flex-col items-center justify-center pb-8 px-6">
                  <div className="flex flex-col items-center">
                    <div className="mb-5 flex h-[88px] w-[88px] items-center justify-center rounded-full border border-gray-200/80 bg-gray-50/50 text-gray-800 transition-colors dark:border-white/[0.08] dark:bg-white/[0.02] dark:text-gray-100">
                      <GithubIcon className="h-11 w-11" />
                    </div>
                    <h4 className="text-[17px] font-bold text-gray-800 dark:text-gray-100">image-playground</h4>
                    <p className="mt-1.5 text-[13px] text-gray-500 dark:text-gray-400">
                      多用户图片生成与编辑工作台
                    </p>
                  </div>

                  <p className="mt-8 mb-6 max-w-[360px] text-center text-[13px] leading-relaxed text-gray-500 dark:text-gray-400">
                    感谢 CookSleep 开源的原项目，原项目采用 MIT 许可证。
                  </p>

                  <div className="flex flex-wrap items-center justify-center gap-3">
                    <a
                      href="https://github.com/CookSleep/gpt_image_playground"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex items-center justify-center gap-2 whitespace-nowrap rounded-xl bg-gray-100/80 px-5 py-2.5 text-sm font-medium text-gray-700 transition-all hover:bg-gray-200 hover:text-gray-900 dark:bg-white/[0.06] dark:text-gray-300 dark:hover:bg-white/[0.1] dark:hover:text-white"
                    >
                      <svg className="h-4 w-4 opacity-70" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />
                      </svg>
                      原项目 GitHub
                    </a>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  )
}

function TabButton({
  active,
  onClick,
  label,
  icon,
}: {
  active: boolean
  onClick: () => void
  label: string
  icon: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      className={`whitespace-nowrap flex-shrink-0 flex items-center gap-2.5 px-3 py-2.5 text-sm rounded-xl transition-colors ${
        active
          ? 'bg-white dark:bg-white/[0.08] shadow-sm text-blue-600 dark:text-blue-400 font-medium'
          : 'text-gray-600 dark:text-gray-400 hover:bg-gray-100/80 dark:hover:bg-white/[0.04]'
      }`}
    >
      {icon}
      {label}
    </button>
  )
}

function Row({
  title,
  description,
  control,
}: {
  title: string
  description: string
  control: React.ReactNode
}) {
  return (
    <div className="block">
      <div className="mb-1 flex items-center justify-between gap-3">
        <span className="block text-sm text-gray-600 dark:text-gray-300">{title}</span>
        {control}
      </div>
      <div data-selectable-text className="text-xs text-gray-500 dark:text-gray-500">
        {description}
      </div>
    </div>
  )
}

function Toggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-4 w-7 items-center rounded-full transition-colors flex-shrink-0 ${
        checked ? 'bg-blue-500' : 'bg-gray-300 dark:bg-gray-600'
      }`}
      role="switch"
      aria-checked={checked}
    >
      <span
        className={`inline-block h-3 w-3 transform rounded-full bg-white shadow transition-transform ${
          checked ? 'translate-x-[14px]' : 'translate-x-[2px]'
        }`}
      />
    </button>
  )
}
