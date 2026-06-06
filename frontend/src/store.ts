import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'
import { zipSync, unzipSync, strToU8, strFromU8 } from 'fflate'
import type { ActualTaskParams, AppSettings, TaskParams, InputImage, MaskDraft, TaskRecord, StoredImageThumbnail, ExportData } from './types'
import { DEFAULT_PARAMS } from './types'
import {
  getAllTasks,
  putTask,
  deleteTask as dbDeleteTask,
  clearTasks as dbClearTasks,
  getImage,
  getStoredFreshImageThumbnail,
  getImageThumbnail,
  getAllImageIds,
  getAllImages,
  putImage,
  putImageThumbnail,
  deleteImage,
  clearImages,
  storeImage,
} from './lib/db'
import { callImageApi, type CallApiResult } from './lib/api'
import { fetchImageUrlAsDataUrl } from './lib/imageApiShared'
import { loadImage, validateMaskMatchesImage } from './lib/canvasImage'
import { orderInputImagesForMask } from './lib/mask'
import { remapImageMentionsForOrder, replaceImageMentionsForApi } from './lib/promptImageMentions'

// ===== Constants =====

const DEFAULT_SETTINGS: AppSettings = {
  clearInputAfterSubmit: true,
  persistInputOnRestart: false,
  alwaysShowRetryButton: false,
  strictMaskComposite: false,
  enterSubmit: true,
  theme: 'system',
}

const IMAGE_CACHE_MAX = 8
const THUMBNAIL_CACHE_MAX = 80
const WEB_RECOVERY_INTERVAL = 2000
const PERSIST_STORAGE_KEY = 'gpt-image-playground'

const safeLocalStorage = {
  getItem: (name: string) => localStorage.getItem(name),
  removeItem: (name: string) => localStorage.removeItem(name),
  setItem: (name: string, value: string) => {
    try {
      localStorage.setItem(name, value)
    } catch (err) {
      if (name !== PERSIST_STORAGE_KEY || !isQuotaExceededError(err)) throw err
      const parsed = JSON.parse(value)
      if (parsed?.state) {
        delete parsed.state.inputImages
        delete parsed.state.maskDraft
        delete parsed.state.prompt
        localStorage.setItem(name, JSON.stringify(parsed))
        return
      }
      throw err
    }
  },
}

function isQuotaExceededError(err: unknown) {
  return err instanceof DOMException && (
    err.name === 'QuotaExceededError' ||
    err.name === 'NS_ERROR_DOM_QUOTA_REACHED' ||
    err.code === 22 ||
    err.code === 1014
  )
}

function areStringArraysEqual(a: string[], b: string[]) {
  if (a === b) return true
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false
  }
  return true
}

// ===== Image cache (LRU) =====

const imageCache = new Map<string, string>()
const thumbnailCache = new Map<string, StoredImageThumbnail>()
const thumbnailListeners = new Map<string, Set<() => void>>()
let backfillScheduled = false
let backfillQueue: string[] = []

function lruSet<K, V>(map: Map<K, V>, key: K, value: V, max: number) {
  if (map.has(key)) map.delete(key)
  map.set(key, value)
  while (map.size > max) {
    const first = map.keys().next().value
    if (first !== undefined) map.delete(first)
  }
}

export function getCachedImage(id: string): string | undefined {
  return imageCache.get(id)
}

export function cacheImage(id: string, dataUrl: string) {
  lruSet(imageCache, id, dataUrl, IMAGE_CACHE_MAX)
}

export async function ensureImageCached(id: string): Promise<string | undefined> {
  const cached = imageCache.get(id)
  if (cached) return cached
  const stored = await getImage(id)
  if (stored) {
    lruSet(imageCache, id, stored.dataUrl, IMAGE_CACHE_MAX)
    return stored.dataUrl
  }
  const serverUrl = getServerImageUrl(id)
  if (serverUrl) return serverUrl
  return undefined
}

export async function ensureImageThumbnailCached(id: string): Promise<{ dataUrl: string; width?: number; height?: number } | undefined> {
  const cached = thumbnailCache.get(id)
  if (cached) return { dataUrl: cached.thumbnailDataUrl, width: cached.width, height: cached.height }
  const serverUrl = getServerThumbnailUrl(id)
  if (serverUrl) return { dataUrl: serverUrl }
  const stored = await getStoredFreshImageThumbnail(id)
  if (stored) {
    lruSet(thumbnailCache, id, stored, THUMBNAIL_CACHE_MAX)
    notifyImageThumbnail(id, { dataUrl: stored.thumbnailDataUrl, width: stored.width, height: stored.height })
    return { dataUrl: stored.thumbnailDataUrl, width: stored.width, height: stored.height }
  }
  return undefined
}

export function subscribeImageThumbnail(id: string, listener: (thumbnail: { dataUrl: string; width?: number; height?: number }) => void): () => void {
  let set = thumbnailListeners.get(id)
  if (!set) {
    set = new Set()
    thumbnailListeners.set(id, set)
  }
  set.add(listener as any)
  return () => {
    set!.delete(listener as any)
    if (set!.size === 0) thumbnailListeners.delete(id)
  }
}

export function notifyImageThumbnail(id: string, thumbnail?: { dataUrl: string; width?: number; height?: number }) {
  if (!thumbnail) return
  thumbnailListeners.get(id)?.forEach((fn: any) => fn(thumbnail))
}

export function getServerImageUrl(imageId: string): string | undefined {
  const tasks = useStore.getState().tasks
  for (const task of tasks) {
    if (task.serverImageUrls?.[imageId]) return task.serverImageUrls[imageId]
  }
  return undefined
}

export function getServerThumbnailUrl(imageId: string): string | undefined {
  const tasks = useStore.getState().tasks
  for (const task of tasks) {
    if (task.serverThumbnailUrls?.[imageId]) return task.serverThumbnailUrls[imageId]
  }
  return undefined
}

export async function ensureImageDataUrl(id: string): Promise<string> {
  const cached = imageCache.get(id)
  if (cached) return cached
  const stored = await getImage(id)
  if (stored) {
    lruSet(imageCache, id, stored.dataUrl, IMAGE_CACHE_MAX)
    return stored.dataUrl
  }
  // Try fetching from server URL
  const serverUrl = getServerImageUrl(id)
  if (serverUrl) {
    const dataUrl = await fetchImageUrlAsDataUrl(serverUrl, 'image/png')
    lruSet(imageCache, id, dataUrl, IMAGE_CACHE_MAX)
    return dataUrl
  }
  throw new Error(`Image not found: ${id}`)
}

function scheduleBackfill(imageIds: string[]) {
  backfillQueue = [...new Set([...backfillQueue, ...imageIds])]
  if (backfillScheduled) return
  backfillScheduled = true
  const run = () => {
    const id = backfillQueue.shift()
    if (!id) { backfillScheduled = false; return }
    ensureImageThumbnailCached(id).finally(() => {
      if (backfillQueue.length > 0) {
        if (typeof requestIdleCallback === 'function') requestIdleCallback(run)
        else setTimeout(run, 50)
      } else {
        backfillScheduled = false
      }
    })
  }
  if (typeof requestIdleCallback === 'function') requestIdleCallback(run)
  else setTimeout(run, 50)
}

// ===== Helpers =====

function genId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

function webJson(url: string, options?: RequestInit): Promise<any> {
  return fetch(url, {
    credentials: 'same-origin',
    cache: 'no-store',
    ...options,
    headers: {
      'X-Web-Version': (typeof window !== 'undefined' && window.__WEB_CLIENT_VERSION__) || '',
      'X-Web-Request': '1',
      ...(options?.headers || {}),
    },
  }).then(async (res) => {
    const text = await res.text()
    let json: any = {}
    if (text) { try { json = JSON.parse(text) } catch { json = { detail: text } } }
    if (!res.ok) throw new Error(json.detail || json.error_message || `HTTP ${res.status}`)
    return json
  })
}

async function compositeEditedImageIntoMask(
  sourceDataUrl: string,
  editedDataUrl: string,
  maskDataUrl: string,
): Promise<string> {
  const [source, edited, mask] = await Promise.all([
    loadImage(sourceDataUrl),
    loadImage(editedDataUrl),
    loadImage(maskDataUrl),
  ])
  const canvas = document.createElement('canvas')
  canvas.width = source.naturalWidth
  canvas.height = source.naturalHeight
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('Canvas not supported')
  // Draw source as base
  ctx.drawImage(source, 0, 0)
  // Use mask alpha to composite edited region
  const maskCanvas = document.createElement('canvas')
  maskCanvas.width = canvas.width
  maskCanvas.height = canvas.height
  const maskCtx = maskCanvas.getContext('2d')!
  maskCtx.drawImage(mask, 0, 0)
  const maskData = maskCtx.getImageData(0, 0, canvas.width, canvas.height)
  // Create edited layer with mask applied
  const editedCanvas = document.createElement('canvas')
  editedCanvas.width = canvas.width
  editedCanvas.height = canvas.height
  const editedCtx = editedCanvas.getContext('2d')!
  editedCtx.drawImage(edited, 0, 0)
  const editedData = editedCtx.getImageData(0, 0, canvas.width, canvas.height)
  const sourceData = ctx.getImageData(0, 0, canvas.width, canvas.height)
  for (let i = 0; i < maskData.data.length; i += 4) {
    const alpha = maskData.data[i + 3]
    const editStrength = 1 - alpha / 255
    sourceData.data[i] = Math.round(sourceData.data[i] * (1 - editStrength) + editedData.data[i] * editStrength)
    sourceData.data[i + 1] = Math.round(sourceData.data[i + 1] * (1 - editStrength) + editedData.data[i + 1] * editStrength)
    sourceData.data[i + 2] = Math.round(sourceData.data[i + 2] * (1 - editStrength) + editedData.data[i + 2] * editStrength)
  }
  ctx.putImageData(sourceData, 0, 0)
  return canvas.toDataURL('image/png')
}

// ===== Store interface =====

interface AppState {
  settings: AppSettings
  setSettings: (s: Partial<AppSettings>) => void

  prompt: string
  setPrompt: (p: string) => void
  inputImages: InputImage[]
  addInputImage: (img: InputImage) => void
  removeInputImage: (idx: number) => void
  clearInputImages: () => void
  setInputImages: (imgs: InputImage[], options?: { equivalentImageIds?: Record<string, string> }) => void
  moveInputImage: (fromIdx: number, toIdx: number) => void
  maskDraft: MaskDraft | null
  setMaskDraft: (draft: MaskDraft | null) => void
  clearMaskDraft: () => void
  maskEditorImageId: string | null
  setMaskEditorImageId: (id: string | null) => void

  params: TaskParams
  setParams: (p: Partial<TaskParams>) => void

  tasks: TaskRecord[]
  setTasks: (t: TaskRecord[]) => void

  searchQuery: string
  setSearchQuery: (q: string) => void
  filterStatus: 'all' | 'running' | 'done' | 'error'
  setFilterStatus: (status: AppState['filterStatus']) => void
  filterFavorite: boolean
  setFilterFavorite: (f: boolean) => void

  selectedTaskIds: string[]
  setSelectedTaskIds: (ids: string[] | ((prev: string[]) => string[])) => void
  toggleTaskSelection: (id: string, force?: boolean) => void
  clearSelection: () => void

  detailTaskId: string | null
  setDetailTaskId: (id: string | null) => void
  lightboxImageId: string | null
  lightboxImageList: string[]
  setLightboxImageId: (id: string | null, list?: string[]) => void
  showSettings: boolean
  setShowSettings: (v: boolean) => void

  toast: { message: string; type: 'info' | 'success' | 'error' } | null
  showToast: (message: string, type?: 'info' | 'success' | 'error') => void

  confirmDialog: {
    title: string
    message: string
    confirmText?: string
    cancelText?: string
    showCancel?: boolean
    icon?: 'info' | 'copy'
    minConfirmDelayMs?: number
    messageAlign?: 'left' | 'center'
    tone?: 'danger' | 'warning'
    action: () => void
    cancelAction?: () => void
  } | null
  setConfirmDialog: (d: AppState['confirmDialog']) => void

  serverStats: {
    activeSpaces: number
    activeGenerations: number
    ownerActiveGenerations: number
    userConcurrencyLimit: number
  }
  setServerStats: (s: Partial<AppState['serverStats']>) => void
}

// ===== Store creation =====

export const useStore = create<AppState>()(
  persist(
    (set, get) => ({
      settings: { ...DEFAULT_SETTINGS },
      setSettings: (s) => set((state) => ({ settings: { ...state.settings, ...s } })),

      prompt: '',
      setPrompt: (prompt) => set({ prompt }),
      inputImages: [],
      addInputImage: (img) => set((state) => {
        if (state.inputImages.find((i) => i.id === img.id)) return state
        const inputImages = [...state.inputImages, img]
        return {
          inputImages,
          prompt: remapImageMentionsForOrder(state.prompt, state.inputImages, inputImages),
        }
      }),
      removeInputImage: (idx) => set((state) => {
        const removed = state.inputImages[idx]
        const inputImages = state.inputImages.filter((_, i) => i !== idx)
        const shouldClearMask = removed?.id === state.maskDraft?.targetImageId
        return {
          inputImages,
          prompt: remapImageMentionsForOrder(state.prompt, state.inputImages, inputImages),
          ...(shouldClearMask ? { maskDraft: null, maskEditorImageId: null } : {}),
        }
      }),
      clearInputImages: () => set((state) => ({
        inputImages: [],
        prompt: remapImageMentionsForOrder(state.prompt, state.inputImages, []),
        maskDraft: null,
        maskEditorImageId: null,
      })),
      setInputImages: (imgs, options) => set((state) => ({
        inputImages: imgs,
        prompt: remapImageMentionsForOrder(state.prompt, state.inputImages, imgs, options?.equivalentImageIds),
      })),
      moveInputImage: (fromIdx, toIdx) => set((state) => {
        const imgs = [...state.inputImages]
        const [item] = imgs.splice(fromIdx, 1)
        if (item) imgs.splice(toIdx, 0, item)
        return {
          inputImages: imgs,
          prompt: remapImageMentionsForOrder(state.prompt, state.inputImages, imgs),
        }
      }),
      maskDraft: null,
      setMaskDraft: (draft) => set((state) => ({
        maskDraft: draft,
        prompt: remapImageMentionsForOrder(state.prompt, state.inputImages, state.inputImages),
      })),
      clearMaskDraft: () => set({ maskDraft: null }),
      maskEditorImageId: null,
      setMaskEditorImageId: (id) => set({ maskEditorImageId: id }),

      params: { ...DEFAULT_PARAMS },
      setParams: (p) => set((state) => ({ params: { ...state.params, ...p } })),

      tasks: [],
      setTasks: (tasks) => set({ tasks }),

      searchQuery: '',
      setSearchQuery: (q) => set({ searchQuery: q }),
      filterStatus: 'all',
      setFilterStatus: (status) => set({ filterStatus: status }),
      filterFavorite: false,
      setFilterFavorite: (f) => set({ filterFavorite: f }),

      selectedTaskIds: [],
      setSelectedTaskIds: (ids) => set((state) => {
        const selectedTaskIds = typeof ids === 'function' ? ids(state.selectedTaskIds) : ids
        return areStringArraysEqual(state.selectedTaskIds, selectedTaskIds) ? state : { selectedTaskIds }
      }),
      toggleTaskSelection: (id, force) => set((state) => {
        const selected = force ?? !state.selectedTaskIds.includes(id)
        if (selected === state.selectedTaskIds.includes(id)) return state
        return {
          selectedTaskIds: selected
            ? [...state.selectedTaskIds, id]
            : state.selectedTaskIds.filter((x) => x !== id),
        }
      }),
      clearSelection: () => set((state) => (
        state.selectedTaskIds.length ? { selectedTaskIds: [] } : state
      )),

      detailTaskId: null,
      setDetailTaskId: (id) => set({ detailTaskId: id }),
      lightboxImageId: null,
      lightboxImageList: [],
      setLightboxImageId: (id, list) => set({ lightboxImageId: id, lightboxImageList: list || [] }),
      showSettings: false,
      setShowSettings: (v) => set({ showSettings: v }),

      toast: null,
      showToast: (message, type = 'info') => {
        set({ toast: { message, type } })
        setTimeout(() => {
          const current = get().toast
          if (current?.message === message) set({ toast: null })
        }, 3000)
      },

      confirmDialog: null,
      setConfirmDialog: (d) => set({ confirmDialog: d }),

      serverStats: { activeSpaces: 0, activeGenerations: 0, ownerActiveGenerations: 0, userConcurrencyLimit: 3 },
      setServerStats: (s) => set((state) => {
        const serverStats = { ...state.serverStats, ...s }
        return (
          serverStats.activeSpaces === state.serverStats.activeSpaces &&
          serverStats.activeGenerations === state.serverStats.activeGenerations &&
          serverStats.ownerActiveGenerations === state.serverStats.ownerActiveGenerations &&
          serverStats.userConcurrencyLimit === state.serverStats.userConcurrencyLimit
        )
          ? state
          : { serverStats }
      }),
    }),
    {
      name: PERSIST_STORAGE_KEY,
      storage: createJSONStorage(() => safeLocalStorage),
      partialize: (state) => {
        const persisted: any = {
          settings: state.settings,
          params: state.params,
        }
        if (state.settings.persistInputOnRestart) {
          persisted.prompt = state.prompt
          persisted.inputImages = state.inputImages.map((img) => ({ id: img.id, dataUrl: '' }))
          persisted.maskDraft = state.maskDraft
        }
        return persisted
      },
      merge: (persisted: any, current) => ({
        ...current,
        ...(persisted || {}),
        settings: { ...DEFAULT_SETTINGS, ...(persisted?.settings || {}) },
        params: { ...DEFAULT_PARAMS, ...(persisted?.params || {}) },
      }),
    },
  ),
)

export default useStore

// ===== Task execution =====

async function executeTask(taskId: string) {
  const state = useStore.getState()
  const task = state.tasks.find((t) => t.id === taskId)
  if (!task) return

  try {
    // Gather input image data URLs
    const inputImageDataUrls: string[] = []
    for (const imgId of task.inputImageIds) {
      const dataUrl = await ensureImageDataUrl(imgId)
      inputImageDataUrls.push(dataUrl)
    }

    // Get mask data URL if present
    let maskDataUrl: string | undefined
    if (task.maskImageId) {
      maskDataUrl = await ensureImageDataUrl(task.maskImageId)
    }

    const result = await callImageApi({
      prompt: replaceImageMentionsForApi(task.prompt, task.inputImageIds.length),
      params: task.params,
      inputImageDataUrls,
      maskDataUrl,
      onWebJobEnqueued: (job) => {
        updateTaskInStore(taskId, {
          serverJobId: job.jobId,
          createdAt: job.createdAt ? new Date(job.createdAt).getTime() : task.createdAt,
        })
      },
    })

    await applyTaskResult(taskId, result)
  } catch (err: any) {
    const errorMsg = err?.message || '生成失败'
    await updateTaskInStore(taskId, {
      status: 'error',
      error: errorMsg,
      finishedAt: Date.now(),
      elapsed: Date.now() - task.createdAt,
    })
  }
}

async function applyTaskResult(taskId: string, result: CallApiResult) {
  const state = useStore.getState()
  const task = state.tasks.find((t) => t.id === taskId)
  if (!task) return

  const outputImageIds: string[] = []
  const serverImageUrls: Record<string, string> = {}
  const serverThumbnailUrls: Record<string, string> = {}

  const jobId = result.jobId || task.serverJobId
  for (let i = 0; i < result.images.length; i++) {
    const imageId = jobId ? `server-${jobId}-${i}` : await storeImage(result.images[i], 'generated')
    outputImageIds.push(imageId)
    if (result.imageUrls?.[i]) serverImageUrls[imageId] = result.imageUrls[i]
    if (result.thumbnailUrls?.[i]) serverThumbnailUrls[imageId] = result.thumbnailUrls[i]
  }

  const finishedAt = result.completedAt ? new Date(result.completedAt).getTime() : Date.now()
  const elapsed = result.elapsedSeconds
    ? Math.round(result.elapsedSeconds * 1000)
    : finishedAt - task.createdAt
  const actualParams = result.actualParams ?? result.actualParamsList?.find((params) => Boolean(params))
  const actualParamsByImage = Object.fromEntries(
    outputImageIds
      .map((id, index) => [id, result.actualParamsList?.[index] ?? actualParams] as const)
      .filter((entry): entry is readonly [string, ActualTaskParams] => Boolean(entry[1])),
  )
  const revisedPromptByImage = Object.fromEntries(
    outputImageIds
      .map((id, index) => [id, result.revisedPrompts?.[index]] as const)
      .filter((entry): entry is readonly [string, string] => typeof entry[1] === 'string' && entry[1].length > 0),
  )
  const outputImageDimensions = Object.fromEntries(
    outputImageIds
      .map((id, index) => [id, result.imageDimensions?.[index]] as const)
      .filter((entry): entry is readonly [string, { width: number; height: number }] => Boolean(entry[1])),
  )

  await updateTaskInStore(taskId, {
    status: 'done',
    error: null,
    outputImages: outputImageIds,
    serverJobId: jobId || undefined,
    serverImageUrls: Object.keys(serverImageUrls).length ? serverImageUrls : undefined,
    serverThumbnailUrls: Object.keys(serverThumbnailUrls).length ? serverThumbnailUrls : undefined,
    actualParams,
    actualParamsByImage: Object.keys(actualParamsByImage).length ? actualParamsByImage : undefined,
    revisedPromptByImage: Object.keys(revisedPromptByImage).length ? revisedPromptByImage : undefined,
    outputImageDimensions: Object.keys(outputImageDimensions).length ? outputImageDimensions : undefined,
    rawImageUrls: result.rawImageUrls,
    finishedAt,
    elapsed,
  })

  // Schedule thumbnail backfill for new output images
  scheduleBackfill(outputImageIds)
}

export async function updateTaskInStore(taskId: string, patch: Partial<TaskRecord>) {
  const state = useStore.getState()
  const tasks = state.tasks.map((t) => (t.id === taskId ? { ...t, ...patch } : t))
  useStore.setState({ tasks })
  const updated = tasks.find((t) => t.id === taskId)
  if (updated) await putTask(updated)
}

// ===== Web recovery =====

export async function recoverRunningServerTasks() {
  const tasks = useStore.getState().tasks
  for (const task of tasks) {
    if (task.status === 'running' && task.serverJobId) {
      recoverWebTask(task.id)
    }
  }
}

async function recoverWebTask(taskId: string) {
  const state = useStore.getState()
  const task = state.tasks.find((t) => t.id === taskId)
  if (!task || !task.serverJobId) return

  try {
    const payload = await webJson(`/web/jobs/${encodeURIComponent(task.serverJobId)}`)
    if (payload.status === 'success') {
      const images = Array.isArray(payload.images) ? payload.images : []
      const urls = images.map((img: any) => img.url).filter((u: unknown): u is string => typeof u === 'string' && u.length > 0)
      const dimensions = images.map((img: any) => {
        const width = Number(img.width)
        const height = Number(img.height)
        return Number.isFinite(width) && Number.isFinite(height) && width > 0 && height > 0
          ? { width, height }
          : undefined
      })
      const result: CallApiResult = {
        images: urls,
        actualParams: payload.actual_params || undefined,
        actualParamsList: urls.map(() => payload.actual_params || undefined),
        revisedPrompts: images.map((img: any) => img.revised_prompt),
        imageDimensions: dimensions,
        rawImageUrls: urls,
        jobId: task.serverJobId,
        imageUrls: urls,
        thumbnailUrls: images.map((img: any) => img.thumbnail_url).filter((u: unknown): u is string => typeof u === 'string' && u.length > 0),
        createdAt: payload.created_at,
        completedAt: payload.completed_at,
        elapsedSeconds: payload.elapsed_seconds,
      }
      await applyTaskResult(taskId, result)
    } else if (payload.status === 'failed') {
      await updateTaskInStore(taskId, {
        status: 'error',
        error: payload.error_message || '生成失败',
        finishedAt: Date.now(),
        elapsed: Date.now() - task.createdAt,
      })
    } else {
      // Still running, retry after interval
      setTimeout(() => recoverWebTask(taskId), WEB_RECOVERY_INTERVAL)
    }
  } catch (err: any) {
    // Network error during recovery — retry
    setTimeout(() => recoverWebTask(taskId), WEB_RECOVERY_INTERVAL * 2)
  }
}

// ===== Public actions =====

export async function initStore(options?: { loadTasks?: boolean }) {
  const { loadTasks: shouldLoad = true } = options || {}

  if (shouldLoad) {
    try {
      const tasks = await getAllTasks()
      tasks.sort((a, b) => b.createdAt - a.createdAt)
      useStore.setState({ tasks })
    } catch {
      // IndexedDB unavailable
    }
  }

  // Restore persisted input images (validate they still exist in DB)
  const state = useStore.getState()
  if (state.inputImages.length > 0) {
    const validImages: InputImage[] = []
    for (const img of state.inputImages) {
      const stored = await getImage(img.id)
      if (stored) validImages.push({ id: img.id, dataUrl: stored.dataUrl })
    }
    const maskDraft = state.maskDraft && validImages.some((img) => img.id === state.maskDraft?.targetImageId)
      ? state.maskDraft
      : null
    useStore.setState({ inputImages: validImages, maskDraft })
  }

  // Clean orphan images
  cleanOrphanImages()

  // Schedule thumbnail backfill for all output images
  const allTasks = useStore.getState().tasks
  const outputIds = allTasks.flatMap((t) => t.outputImages)
  if (outputIds.length > 0) scheduleBackfill(outputIds)

  // Recover running server tasks
  recoverRunningServerTasks()
}

async function cleanOrphanImages() {
  try {
    const tasks = useStore.getState().tasks
    const referencedIds = new Set<string>()
    for (const task of tasks) {
      task.inputImageIds.forEach((id) => referencedIds.add(id))
      task.outputImages.forEach((id) => referencedIds.add(id))
      if (task.maskImageId) referencedIds.add(task.maskImageId)
    }
    // Also keep current input images
    const state = useStore.getState()
    state.inputImages.forEach((img) => referencedIds.add(img.id))

    const allIds = await getAllImageIds()
    for (const id of allIds) {
      if (!referencedIds.has(id)) await deleteImage(id)
    }
  } catch {
    // Non-critical
  }
}

export async function submitTask(options?: { allowFullMask?: boolean }) {
  const state = useStore.getState()
  const { prompt, inputImages, maskDraft, params, settings, serverStats } = state

  if (!prompt.trim() && inputImages.length === 0) {
    useStore.getState().showToast('请输入提示词或添加图片', 'error')
    return
  }

  const requestedN = params.n
  const available = serverStats.userConcurrencyLimit - serverStats.ownerActiveGenerations
  const actualN = Math.min(requestedN, Math.max(0, available))
  if (actualN <= 0) {
    useStore.getState().showToast(`已达并发上限（${serverStats.userConcurrencyLimit}）`, 'error')
    return
  }

  // Handle mask
  let maskImageId: string | null = null
  let maskTargetImageId: string | null = null
  let orderedImages = inputImages

  if (maskDraft && inputImages.some((img) => img.id === maskDraft.targetImageId)) {
    const targetImg = inputImages.find((img) => img.id === maskDraft.targetImageId)!
    const coverage = await validateMaskMatchesImage(maskDraft.maskDataUrl, targetImg.dataUrl)
    if (coverage === 'full' && !options?.allowFullMask) {
      useStore.getState().showToast('遮罩覆盖了整张图片，请留出需要保留的区域', 'error')
      return
    }
    maskImageId = await storeImage(maskDraft.maskDataUrl, 'mask')
    maskTargetImageId = maskDraft.targetImageId
    orderedImages = orderInputImagesForMask(inputImages, maskDraft.targetImageId)
  }

  // Persist input images to IndexedDB
  const inputImageIds: string[] = []
  for (const img of orderedImages) {
    await putImage({ id: img.id, dataUrl: img.dataUrl, createdAt: Date.now(), source: 'upload' })
    inputImageIds.push(img.id)
  }

  // Create N parallel tasks
  const tasks: TaskRecord[] = []
  for (let i = 0; i < actualN; i++) {
    tasks.push({
      id: genId(),
      prompt: prompt.trim(),
      params: { ...params, n: 1 },
      operation: maskImageId ? 'edit' : inputImageIds.length > 0 ? 'reference' : 'generate',
      inputImageIds,
      maskTargetImageId,
      maskImageId,
      outputImages: [],
      status: 'running',
      error: null,
      createdAt: Date.now(),
      finishedAt: null,
      elapsed: null,
    })
  }

  useStore.setState((s) => ({ tasks: [...tasks, ...s.tasks] }))
  for (const task of tasks) {
    await putTask(task)
  }

  // Clear input if configured
  if (settings.clearInputAfterSubmit) {
    useStore.setState({ prompt: '', inputImages: [], maskDraft: null })
  } else {
    useStore.setState({ maskDraft: null })
  }

  // Execute all in parallel
  for (const task of tasks) {
    executeTask(task.id)
  }

  if (actualN < requestedN) {
    useStore.getState().showToast(`并发受限，已提交 ${actualN}/${requestedN} 个任务`, 'info')
  }
}

export async function retryTask(task: TaskRecord) {
  const resetPatch: Partial<TaskRecord> = {
    status: 'running',
    error: null,
    outputImages: [],
    finishedAt: null,
    elapsed: null,
    createdAt: Date.now(),
    serverJobId: undefined,
    serverImageUrls: undefined,
    serverThumbnailUrls: undefined,
    actualParams: undefined,
    actualParamsByImage: undefined,
    revisedPromptByImage: undefined,
    outputImageDimensions: undefined,
    rawImageUrls: undefined,
    rawResponsePayload: undefined,
  }
  await updateTaskInStore(task.id, resetPatch)
  executeTask(task.id)
}

export function reuseConfig(task: TaskRecord) {
  const state = useStore.getState()
  useStore.setState({ prompt: task.prompt, params: { ...task.params } })

  // Restore input images
  const inputImages: InputImage[] = []
  for (const imgId of task.inputImageIds) {
    const cached = imageCache.get(imgId)
    if (cached) {
      inputImages.push({ id: imgId, dataUrl: cached })
    }
  }
  // If not all cached, load from DB asynchronously
  if (inputImages.length < task.inputImageIds.length) {
    Promise.all(
      task.inputImageIds.map(async (imgId) => {
        const dataUrl = await ensureImageDataUrl(imgId).catch(() => null)
        return dataUrl ? { id: imgId, dataUrl } : null
      }),
    ).then((results) => {
      const valid = results.filter((r): r is InputImage => r !== null)
      useStore.setState({ inputImages: valid })
    })
  } else {
    useStore.setState({ inputImages })
  }

  // Restore mask if present
  if (task.maskImageId && task.maskTargetImageId) {
    ensureImageDataUrl(task.maskImageId).then((maskDataUrl) => {
      useStore.setState({
        maskDraft: {
          targetImageId: task.maskTargetImageId!,
          maskDataUrl,
          updatedAt: Date.now(),
        },
      })
    }).catch(() => {})
  }
}

export async function editOutputs(task: TaskRecord) {
  const newInputImages: InputImage[] = []
  for (const imgId of task.outputImages) {
    try {
      const dataUrl = await ensureImageDataUrl(imgId)
      newInputImages.push({ id: imgId, dataUrl })
    } catch {
      // Skip images that can't be loaded
    }
  }
  if (newInputImages.length === 0) {
    useStore.getState().showToast('无法加载输出图片', 'error')
    return
  }
  useStore.setState((s) => ({ inputImages: [...s.inputImages, ...newInputImages] }))
}

export async function removeTask(task: TaskRecord) {
  // Delete from server if it has a server job ID
  if (task.serverJobId) {
    try {
      await webJson(`/web/jobs/${encodeURIComponent(task.serverJobId)}/delete`, { method: 'POST' })
    } catch {
      // Non-critical: server may already have deleted it
    }
  }

  // Remove from store
  useStore.setState((s) => ({
    tasks: s.tasks.filter((t) => t.id !== task.id),
    selectedTaskIds: s.selectedTaskIds.filter((id) => id !== task.id),
  }))
  await dbDeleteTask(task.id)

  // Clean orphan images (deferred)
  setTimeout(cleanOrphanImages, 100)
}

export async function removeMultipleTasks(taskIds: string[]) {
  const state = useStore.getState()
  const tasksToRemove = state.tasks.filter((t) => taskIds.includes(t.id))
  const serverJobIds = tasksToRemove
    .map((t) => t.serverJobId)
    .filter((id): id is string => !!id)

  // Batch delete from server
  if (serverJobIds.length > 0) {
    try {
      await webJson('/web/jobs/delete-batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_ids: serverJobIds }),
      })
    } catch {
      // Non-critical
    }
  }

  // Remove from store
  const idSet = new Set(taskIds)
  useStore.setState((s) => ({
    tasks: s.tasks.filter((t) => !idSet.has(t.id)),
    selectedTaskIds: s.selectedTaskIds.filter((id) => !idSet.has(id)),
  }))
  await Promise.all(taskIds.map((id) => dbDeleteTask(id)))

  setTimeout(cleanOrphanImages, 100)
}

export async function clearData(options: { tasks?: boolean; config?: boolean }) {
  if (options.tasks) {
    useStore.setState({ tasks: [], selectedTaskIds: [] })
    await dbClearTasks()
    await clearImages()
  }
  if (options.config) {
    useStore.setState({
      settings: { ...DEFAULT_SETTINGS },
      params: { ...DEFAULT_PARAMS },
      prompt: '',
      inputImages: [],
      maskDraft: null,
    })
  }
}

export async function addImageFromFile(file: File): Promise<void> {
  const dataUrl = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = () => reject(new Error('文件读取失败'))
    reader.readAsDataURL(file)
  })
  const id = await storeImage(dataUrl, 'upload')
  useStore.getState().addInputImage({ id, dataUrl })
}

export async function addImageFromUrl(src: string): Promise<void> {
  const dataUrl = await fetchImageUrlAsDataUrl(src, 'image/png')
  const id = await storeImage(dataUrl, 'upload')
  useStore.getState().addInputImage({ id, dataUrl })
}

// ===== Export / Import =====

function formatExportFileTime(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}_${pad(date.getHours())}-${pad(date.getMinutes())}-${pad(date.getSeconds())}`
}

function dataUrlToBytes(dataUrl: string): { ext: string; bytes: Uint8Array } {
  const match = dataUrl.match(/^data:image\/(\w+);base64,/)
  const ext = match?.[1] ?? 'png'
  const b64 = dataUrl.replace(/^data:[^;]+;base64,/, '')
  const binary = atob(b64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
  return { ext, bytes }
}

function bytesToDataUrl(bytes: Uint8Array, filePath: string): string {
  const ext = filePath.split('.').pop()?.toLowerCase() ?? 'png'
  const mimeMap: Record<string, string> = { png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp' }
  const mime = mimeMap[ext] ?? 'image/png'
  let binary = ''
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i])
  return `data:${mime};base64,${btoa(binary)}`
}

const KNOWN_SETTINGS_KEYS: (keyof AppSettings)[] = [
  'clearInputAfterSubmit',
  'persistInputOnRestart',
  'alwaysShowRetryButton',
  'strictMaskComposite',
  'enterSubmit',
  'theme',
]

function pickKnownSettings(imported: Partial<AppSettings> | undefined): Partial<AppSettings> {
  if (!imported || typeof imported !== 'object') return {}
  const out: Partial<AppSettings> = {}
  for (const key of KNOWN_SETTINGS_KEYS) {
    if (key in imported) {
      ;(out as any)[key] = (imported as any)[key]
    }
  }
  return out
}

export interface ExportOptions {
  exportConfig?: boolean
  exportTasks?: boolean
}

export async function exportData(options: ExportOptions = { exportConfig: true, exportTasks: true }) {
  const { showToast } = useStore.getState()
  try {
    const shouldExportConfig = options.exportConfig !== false
    const shouldExportTasks = options.exportTasks !== false

    const tasks = shouldExportTasks ? await getAllTasks() : []
    const images = shouldExportTasks ? await getAllImages() : []
    const { settings } = useStore.getState()
    const exportedAt = Date.now()

    const imageCreatedAtFallback = new Map<string, number>()
    if (shouldExportTasks) {
      for (const task of tasks) {
        for (const id of [
          ...(task.inputImageIds || []),
          ...(task.maskImageId ? [task.maskImageId] : []),
          ...(task.outputImages || []),
        ]) {
          const prev = imageCreatedAtFallback.get(id)
          if (prev == null || task.createdAt < prev) {
            imageCreatedAtFallback.set(id, task.createdAt)
          }
        }
      }
    }

    const imageFiles: NonNullable<ExportData['imageFiles']> = {}
    const thumbnailFiles: NonNullable<ExportData['thumbnailFiles']> = {}
    const zipFiles: Record<string, Uint8Array | [Uint8Array, { mtime: Date }]> = {}

    if (shouldExportTasks) {
      for (const img of images) {
        const { ext, bytes } = dataUrlToBytes(img.dataUrl)
        const path = `images/${img.id}.${ext}`
        const createdAt = img.createdAt ?? imageCreatedAtFallback.get(img.id) ?? exportedAt
        imageFiles[img.id] = {
          path,
          createdAt,
          source: img.source,
          width: img.width,
          height: img.height,
        }
        zipFiles[path] = [bytes, { mtime: new Date(createdAt) }]

        const thumbnail = await getImageThumbnail(img.id)
        if (thumbnail?.thumbnailDataUrl) {
          const { ext: thumbnailExt, bytes: thumbnailBytes } = dataUrlToBytes(thumbnail.thumbnailDataUrl)
          const thumbnailPath = `thumbnails/${img.id}.${thumbnailExt}`
          imageFiles[img.id].width = imageFiles[img.id].width ?? thumbnail.width
          imageFiles[img.id].height = imageFiles[img.id].height ?? thumbnail.height
          thumbnailFiles[img.id] = {
            path: thumbnailPath,
            width: thumbnail.width,
            height: thumbnail.height,
            thumbnailVersion: thumbnail.thumbnailVersion,
          }
          zipFiles[thumbnailPath] = [thumbnailBytes, { mtime: new Date(createdAt) }]
          lruSet(thumbnailCache, img.id, {
            id: img.id,
            thumbnailDataUrl: thumbnail.thumbnailDataUrl,
            width: thumbnail.width,
            height: thumbnail.height,
            thumbnailVersion: thumbnail.thumbnailVersion,
          }, THUMBNAIL_CACHE_MAX)
        }
      }
    }

    const manifest: ExportData = {
      version: 3,
      exportedAt: new Date(exportedAt).toISOString(),
    }

    if (shouldExportConfig) manifest.settings = settings
    if (shouldExportTasks) {
      manifest.tasks = tasks
      manifest.imageFiles = imageFiles
      manifest.thumbnailFiles = thumbnailFiles
    }

    zipFiles['manifest.json'] = [strToU8(JSON.stringify(manifest, null, 2)), { mtime: new Date(exportedAt) }]

    const zipped = zipSync(zipFiles, { level: 6 })
    const blob = new Blob([zipped.buffer as ArrayBuffer], { type: 'application/zip' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `gpt-image-playground-${formatExportFileTime(new Date(exportedAt))}.zip`
    a.click()
    URL.revokeObjectURL(url)
    showToast('数据已导出', 'success')
  } catch (e) {
    showToast(`导出失败：${e instanceof Error ? e.message : String(e)}`, 'error')
  }
}

export interface ImportOptions {
  importConfig?: boolean
  importTasks?: boolean
}

export async function importData(file: File, options: ImportOptions = { importConfig: true, importTasks: true }): Promise<boolean> {
  const { showToast } = useStore.getState()
  try {
    const buffer = await file.arrayBuffer()
    const unzipped = unzipSync(new Uint8Array(buffer))

    const manifestBytes = unzipped['manifest.json']
    if (!manifestBytes) throw new Error('ZIP 中缺少 manifest.json')

    const data: ExportData = JSON.parse(strFromU8(manifestBytes))

    const shouldImportConfig = options.importConfig !== false
    const shouldImportTasks = options.importTasks !== false

    const importedImageIds: string[] = []
    if (shouldImportTasks && data.tasks && data.imageFiles) {
      for (const [id, info] of Object.entries(data.imageFiles)) {
        const bytes = unzipped[info.path]
        if (!bytes) continue
        const dataUrl = bytesToDataUrl(bytes, info.path)
        await putImage({
          id,
          dataUrl,
          createdAt: info.createdAt,
          source: info.source,
          width: info.width,
          height: info.height,
        })
        cacheImage(id, dataUrl)
        importedImageIds.push(id)
      }

      for (const [id, info] of Object.entries(data.thumbnailFiles ?? {})) {
        const bytes = unzipped[info.path]
        if (!bytes) continue
        const thumbnailDataUrl = bytesToDataUrl(bytes, info.path)
        await putImageThumbnail({
          id,
          thumbnailDataUrl,
          width: info.width,
          height: info.height,
          thumbnailVersion: info.thumbnailVersion,
        })
        lruSet(thumbnailCache, id, {
          id,
          thumbnailDataUrl,
          width: info.width,
          height: info.height,
          thumbnailVersion: info.thumbnailVersion,
        }, THUMBNAIL_CACHE_MAX)
      }

      for (const task of data.tasks) {
        await putTask(task)
      }

      const tasks = await getAllTasks()
      tasks.sort((a, b) => b.createdAt - a.createdAt)
      useStore.getState().setTasks(tasks)
      if (importedImageIds.length > 0) scheduleBackfill(importedImageIds)
    }

    if (shouldImportConfig && data.settings) {
      const state = useStore.getState()
      const picked = pickKnownSettings(data.settings)
      state.setSettings({ ...state.settings, ...picked })
    }

    let msg = '数据已成功导入'
    if (shouldImportTasks && data.tasks) {
      msg = `已导入 ${data.tasks.length} 条记录`
    } else if (shouldImportConfig && data.settings) {
      msg = '配置已成功导入'
    }

    showToast(msg, 'success')
    return true
  } catch (e) {
    showToast(`导入失败：${e instanceof Error ? e.message : String(e)}`, 'error')
    return false
  }
}
