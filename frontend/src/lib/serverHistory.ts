import type { ActualTaskParams, TaskRecord } from '../types'
import { DEFAULT_PARAMS } from '../types'
import { deleteTask as dbDeleteTask, putTask } from './db'

// Removed fields no longer in TaskParams — keep compatibility with server responses

interface ServerHistoryItem {
  image_id?: string
  job_id?: string
  created_at?: string
  completed_at?: string
  operation?: string
  url?: string
  thumbnail_url?: string
  filename?: string
  size_bytes?: number
  width?: number
  height?: number
  prompt?: string
  provider_id?: string
  provider_name_snapshot?: string
  provider_type?: string
  request_params?: Partial<TaskRecord['params']>
  actual_params?: ActualTaskParams
  elapsed_seconds?: number
  input_image_count?: number
  input_image_urls?: string[]
  mask_url?: string
}

interface ServerHistoryJob {
  job_id?: string
  created_at?: string
  operation?: string
  prompt?: string
  provider_id?: string
  provider_name_snapshot?: string
  provider_type?: string
  model?: string
  request_params?: Partial<TaskRecord['params']>
  actual_params?: ActualTaskParams
  status?: string
}

async function readJsonResponse(response: Response): Promise<any> {
  const text = await response.text()
  let payload: any = {}
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      payload = { detail: text }
    }
  }
  if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`)
  return payload
}

function parseTime(value: unknown) {
  if (typeof value !== 'string' || !value) return Date.now()
  const time = Date.parse(value)
  return Number.isFinite(time) ? time : Date.now()
}

function parseOptionalTime(value: unknown) {
  if (typeof value !== 'string' || !value) return null
  const time = Date.parse(value)
  return Number.isFinite(time) ? time : null
}

function hasParams(value: unknown): value is ActualTaskParams {
  return Boolean(value && typeof value === 'object' && Object.keys(value).length > 0)
}

function historyItemToTask(item: ServerHistoryItem): TaskRecord | null {
  const jobId = item.job_id || ''
  const imageIndexMatch = String(item.url || '').match(/\/(\d+)$/)
  const imageIndex = imageIndexMatch ? Number(imageIndexMatch[1]) : 1
  const imageId = `server-${jobId}-${imageIndex || 1}`
  const url = item.url
  if (!url || !jobId) return null
  const createdAt = parseTime(item.created_at)
  const requestParams = hasParams(item.request_params) ? item.request_params : undefined
  const actualParams = hasParams(item.actual_params) ? item.actual_params : undefined
  const operation = item.operation === 'edit' && !item.mask_url ? 'reference' : item.operation

  const inputImageIds: string[] = []
  const serverImageUrls: Record<string, string> = { [imageId]: url }
  const serverThumbnailUrls: Record<string, string> = item.thumbnail_url ? { [imageId]: item.thumbnail_url } : {}
  const width = Number(item.width)
  const height = Number(item.height)
  const imageDimensions = Number.isFinite(width) && Number.isFinite(height) && width > 0 && height > 0
    ? { [imageId]: { width, height } }
    : undefined

  if (item.input_image_urls && item.input_image_urls.length > 0) {
    for (let i = 0; i < item.input_image_urls.length; i++) {
      const inputId = `server-input-${jobId}-${i + 1}`
      inputImageIds.push(inputId)
      serverImageUrls[inputId] = item.input_image_urls[i]
    }
  }

  let maskImageId: string | undefined
  let maskTargetImageId: string | undefined
  if (item.mask_url && inputImageIds.length > 0) {
    maskImageId = `server-mask-${jobId}`
    maskTargetImageId = inputImageIds[0]
    serverImageUrls[maskImageId] = item.mask_url
  }

  return {
    id: jobId,
    prompt: item.prompt || '',
    params: { ...DEFAULT_PARAMS, ...(requestParams || {}) },
    modelProfileId: requestParams?.modelProfileId || (requestParams as any)?.model_profile_id,
    providerId: item.provider_id || requestParams?.providerId,
    providerName: item.provider_name_snapshot,
    model: requestParams?.model,
    actualParams,
    actualParamsByImage: actualParams ? { [imageId]: actualParams } : undefined,
    outputImageDimensions: imageDimensions,
    operation,
    inputImageIds,
    maskImageId,
    maskTargetImageId,
    outputImages: [imageId],
    serverJobId: jobId,
    serverImageUrls,
    serverThumbnailUrls: Object.keys(serverThumbnailUrls).length ? serverThumbnailUrls : undefined,
    rawImageUrls: [url],
    status: 'done',
    error: null,
    createdAt,
    finishedAt: parseOptionalTime(item.completed_at) ?? createdAt,
    elapsed: typeof item.elapsed_seconds === 'number' ? Math.round(item.elapsed_seconds * 1000) : null,
  }
}

function historyJobToTask(job: ServerHistoryJob): TaskRecord | null {
  const jobId = job.job_id || ''
  if (!jobId || job.status !== 'running') return null
  const requestParams = hasParams(job.request_params) ? job.request_params : undefined
  const actualParams = hasParams(job.actual_params) ? job.actual_params : undefined
  return {
    id: jobId,
    prompt: job.prompt || '',
    params: { ...DEFAULT_PARAMS, ...(requestParams || {}) },
    modelProfileId: requestParams?.modelProfileId || (requestParams as any)?.model_profile_id,
    providerId: job.provider_id || requestParams?.providerId,
    providerName: job.provider_name_snapshot,
    model: job.model || requestParams?.model,
    actualParams,
    operation: job.operation,
    inputImageIds: [],
    outputImages: [],
    serverJobId: jobId,
    status: 'running',
    error: null,
    createdAt: parseTime(job.created_at),
    finishedAt: null,
    elapsed: null,
  }
}

function mergeServerTask(existing: TaskRecord, serverTask: TaskRecord): TaskRecord {
  return {
    ...existing,
    ...serverTask,
    id: existing.id,
    serverJobId: serverTask.serverJobId,
    inputImageIds: existing.inputImageIds.length ? existing.inputImageIds : serverTask.inputImageIds,
    maskTargetImageId: existing.maskTargetImageId ?? serverTask.maskTargetImageId,
    maskImageId: existing.maskImageId ?? serverTask.maskImageId,
    serverImageUrls: { ...(serverTask.serverImageUrls || {}), ...(existing.serverImageUrls || {}) },
    serverThumbnailUrls: { ...(serverTask.serverThumbnailUrls || {}), ...(existing.serverThumbnailUrls || {}) },
    params: existing.params ?? serverTask.params,
    actualParams: existing.actualParams ?? serverTask.actualParams,
    actualParamsByImage: { ...(serverTask.actualParamsByImage || {}), ...(existing.actualParamsByImage || {}) },
    revisedPromptByImage: { ...(serverTask.revisedPromptByImage || {}), ...(existing.revisedPromptByImage || {}) },
    outputImageDimensions: { ...(serverTask.outputImageDimensions || {}), ...(existing.outputImageDimensions || {}) },
    rawImageUrls: existing.rawImageUrls?.length ? existing.rawImageUrls : serverTask.rawImageUrls,
    operation: existing.operation ?? serverTask.operation,
    isFavorite: existing.isFavorite ?? serverTask.isFavorite,
    status: serverTask.status,
    error: serverTask.error,
    createdAt: existing.createdAt || serverTask.createdAt,
    finishedAt: serverTask.finishedAt || existing.finishedAt,
    elapsed: serverTask.elapsed ?? existing.elapsed,
  }
}

export async function loadServerHistory(webVersion: string, existingTasks: TaskRecord[] = []): Promise<TaskRecord[]> {
  const response = await fetch('/web/history?offset=0&limit=60', {
    credentials: 'same-origin',
    cache: 'no-store',
    headers: {
      'X-Web-Version': webVersion,
      'X-Web-Request': '1',
    },
  })
  const payload = await readJsonResponse(response)
  const items = Array.isArray(payload.items) ? payload.items as ServerHistoryItem[] : []
  const jobs = Array.isArray(payload.jobs) ? payload.jobs as ServerHistoryJob[] : []
  const taskMap = new Map<string, TaskRecord>()

  for (const job of jobs) {
    const task = historyJobToTask(job)
    if (task) taskMap.set(task.id, task)
  }

  for (const item of items) {
    const task = historyItemToTask(item)
    if (!task) continue
    const existing = taskMap.get(task.id)
    if (existing) {
      const imageId = task.outputImages[0]
      existing.outputImages.push(imageId)
      existing.serverImageUrls = { ...(existing.serverImageUrls || {}), ...(task.serverImageUrls || {}) }
      existing.serverThumbnailUrls = { ...(existing.serverThumbnailUrls || {}), ...(task.serverThumbnailUrls || {}) }
      existing.actualParams = existing.actualParams ?? task.actualParams
      existing.actualParamsByImage = { ...(existing.actualParamsByImage || {}), ...(task.actualParamsByImage || {}) }
      existing.outputImageDimensions = { ...(existing.outputImageDimensions || {}), ...(task.outputImageDimensions || {}) }
      existing.rawImageUrls = [...(existing.rawImageUrls || []), ...(task.rawImageUrls || [])]
      if (!existing.inputImageIds.length && task.inputImageIds.length) {
        existing.inputImageIds = task.inputImageIds
        existing.maskImageId = existing.maskImageId ?? task.maskImageId
        existing.maskTargetImageId = existing.maskTargetImageId ?? task.maskTargetImageId
      }
      continue
    }
    taskMap.set(task.id, task)
  }

  const tasks = Array.from(taskMap.values())
  const serverJobIds = new Set(tasks.map((task) => task.serverJobId || task.id))

  for (const serverTask of tasks) {
    const jobId = serverTask.serverJobId || serverTask.id
    const existing = existingTasks.find((task) => task.serverJobId === jobId || task.id === jobId)
    await putTask(existing ? mergeServerTask(existing, serverTask) : serverTask)
  }

  for (const task of existingTasks) {
    const jobId = task.serverJobId || task.id
    if (task.status === 'running') {
      if (!serverJobIds.has(jobId)) taskMap.set(task.id, task)
    } else if (!serverJobIds.has(jobId)) {
      await dbDeleteTask(task.id)
    }
  }

  return Array.from(taskMap.values())
}
