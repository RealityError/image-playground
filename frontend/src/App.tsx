import { useEffect, useState } from 'react'
import { initStore, loadProviderProfiles, recoverRunningServerTasks } from './store'
import { useStore } from './store'
import Header from './components/Header'
import SearchBar from './components/SearchBar'
import TaskGrid from './components/TaskGrid'
import InputBar from './components/InputBar'
import DetailModal from './components/DetailModal'
import Lightbox from './components/Lightbox'
import SettingsModal from './components/SettingsModal'
import ConfirmDialog from './components/ConfirmDialog'
import Toast from './components/Toast'
import MaskEditorModal from './components/MaskEditorModal'
import ImageContextMenu from './components/ImageContextMenu'
import { loadServerHistory } from './lib/serverHistory'

const WEB_CLIENT_VERSION = '20260512-playground-2'

async function readJsonResponse(response: Response) {
  const text = await response.text()
  let payload: any = {}
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      payload = { detail: text }
    }
  }
  if (!response.ok) {
    throw new Error(payload.detail || `HTTP ${response.status}`)
  }
  return payload
}

function webFetch(url: string, options: RequestInit = {}) {
  const headers = new Headers(options.headers || {})
  headers.set('X-Web-Version', WEB_CLIENT_VERSION)
  headers.set('X-Web-Request', '1')
  return fetch(url, {
    credentials: 'same-origin',
    cache: 'no-store',
    ...options,
    headers,
  })
}

export default function App() {
  const setSettings = useStore((s) => s.setSettings)
  const serverStats = useStore((s) => s.serverStats)
  const setServerStats = useStore((s) => s.setServerStats)
  const [unlocked, setUnlocked] = useState(false)
  const [ownerHint, setOwnerHint] = useState('')
  const [passphrase, setPassphrase] = useState('')
  const [unlockStatus, setUnlockStatus] = useState('检查中')

  const applyServerStats = (data: any) => {
    setServerStats({
      activeSpaces: Number(data.active_spaces ?? data.active_users ?? 0),
      activeGenerations: Number(data.active_generations ?? 0),
      ownerActiveGenerations: Number(data.owner_active_generations ?? 0),
      userConcurrencyLimit: Number(data.user_concurrency_limit ?? 3),
    })
  }

  useEffect(() => {
    window.__WEB_CLIENT_VERSION__ = WEB_CLIENT_VERSION
    void initStore({ loadTasks: false })
  }, [setSettings])

  useEffect(() => {
    void (async () => {
      try {
        const response = await webFetch('/web/session')
        const data = await readJsonResponse(response)
        applyServerStats(data)
        setUnlocked(Boolean(data.unlocked))
        setOwnerHint(data.owner_hint || '')
        setUnlockStatus(data.unlocked ? '' : '输入口令进入')
        if (data.unlocked) {
          await loadProviderProfiles()
          const tasks = await loadServerHistory(WEB_CLIENT_VERSION, useStore.getState().tasks)
          useStore.getState().setTasks([...tasks].sort((a, b) => b.createdAt - a.createdAt))
          recoverRunningServerTasks()
        }
      } catch (error) {
        setUnlockStatus(error instanceof Error ? error.message : '服务不可用')
      }
    })()
  }, [])

  useEffect(() => {
    if (!unlocked) return
    let cancelled = false
    const refreshStats = async () => {
      try {
        const response = await webFetch('/web/stats')
        const data = await readJsonResponse(response)
        if (!cancelled) applyServerStats(data)
      } catch {
        // stats are informational
      }
    }
    void refreshStats()
    const timer = window.setInterval(refreshStats, 5000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [unlocked])

  useEffect(() => {
    const preventPageImageDrag = (e: DragEvent) => {
      if ((e.target as HTMLElement | null)?.closest('img')) {
        e.preventDefault()
      }
    }
    document.addEventListener('dragstart', preventPageImageDrag)
    return () => document.removeEventListener('dragstart', preventPageImageDrag)
  }, [])

  const unlock = async () => {
    const value = passphrase.trim()
    if (!value) {
      setUnlockStatus('请输入口令')
      return
    }
    setUnlockStatus('进入中')
    try {
      const response = await webFetch('/web/unlock', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ passphrase: value }),
      })
      const data = await readJsonResponse(response)
      applyServerStats(data)
      setUnlocked(true)
      setOwnerHint(data.owner_hint || '')
      setPassphrase('')
      setUnlockStatus('')
      await loadProviderProfiles()
      const tasks = await loadServerHistory(WEB_CLIENT_VERSION, useStore.getState().tasks)
      useStore.getState().setTasks([...tasks].sort((a, b) => b.createdAt - a.createdAt))
      recoverRunningServerTasks()
    } catch (error) {
      setUnlockStatus(error instanceof Error ? error.message : '解锁失败')
    }
  }

  const lock = async () => {
    try {
      await webFetch('/web/lock', { method: 'POST' })
    } catch {
      /* ignore */
    }
    setUnlocked(false)
    useStore.getState().setTasks([])
    setOwnerHint('')
    setUnlockStatus('输入口令进入')
  }

  return (
    <>
      {!unlocked && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-gray-950/70 backdrop-blur px-4">
          <div className="w-full max-w-sm rounded-2xl bg-white dark:bg-gray-950 border border-gray-200 dark:border-white/10 shadow-2xl p-5 space-y-4">
            <div>
              <h2 className="text-lg font-bold text-gray-900 dark:text-gray-100">image-playground</h2>
              <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">输入空间口令后使用</p>
            </div>
            <input
              value={passphrase}
              onChange={(event) => setPassphrase(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') void unlock()
              }}
              type="password"
              autoFocus
              className="w-full rounded-xl border border-gray-300 dark:border-white/10 bg-white dark:bg-gray-900 px-3 py-2.5 text-gray-900 dark:text-gray-100 outline-none focus:ring-2 focus:ring-gray-900/10"
              placeholder="口令"
            />
            <div className="flex items-center justify-between gap-3">
              <button
                onClick={() => void unlock()}
                className="rounded-xl bg-gray-900 dark:bg-gray-100 text-white dark:text-gray-950 px-4 py-2.5 font-semibold"
              >
                进入
              </button>
              <span className="text-xs text-gray-500 dark:text-gray-400">{unlockStatus}</span>
            </div>
          </div>
        </div>
      )}
      <Header />
      {unlocked && (
        <div data-no-drag-select className="safe-area-x max-w-[1560px] mx-auto mt-4 flex flex-wrap items-center justify-between gap-3 text-xs text-gray-500 dark:text-gray-400">
          <div className="flex flex-wrap items-center gap-2 rounded-2xl border border-gray-200/70 bg-white/60 px-3 py-2 dark:border-white/[0.08] dark:bg-white/[0.03]">
            <span className="font-medium text-gray-600 dark:text-gray-300">空间 {ownerHint}</span>
            <span className="rounded-lg bg-gray-100 px-2 py-1 dark:bg-white/[0.05]">在线 {serverStats.activeSpaces}</span>
            <span className="rounded-lg bg-gray-100 px-2 py-1 dark:bg-white/[0.05]">队列 {serverStats.activeGenerations}</span>
            <span className="rounded-lg bg-gray-100 px-2 py-1 dark:bg-white/[0.05]">我的 {serverStats.ownerActiveGenerations}/{serverStats.userConcurrencyLimit}</span>
          </div>
          <button onClick={() => void lock()} className="px-3 py-1.5 rounded-lg border border-gray-200/70 bg-white/50 dark:border-white/10 dark:bg-white/[0.03] hover:bg-gray-100 dark:hover:bg-white/[0.06]">
            退出空间
          </button>
        </div>
      )}
      <main data-home-main data-drag-select-surface className="pb-44 sm:pb-52">
        <div className="safe-area-x max-w-[1560px] mx-auto">
          <SearchBar />
          <TaskGrid />
        </div>
      </main>
      <InputBar />
      <DetailModal />
      <Lightbox />
      <SettingsModal />
      <ConfirmDialog />
      <Toast />
      <MaskEditorModal />
      <ImageContextMenu />
    </>
  )
}
