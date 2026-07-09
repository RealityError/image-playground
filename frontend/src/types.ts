// ===== 设置 =====

export interface AppSettings {
  clearInputAfterSubmit: boolean
  persistInputOnRestart: boolean
  alwaysShowRetryButton: boolean
  strictMaskComposite: boolean
  enterSubmit: boolean
  theme: 'light' | 'dark' | 'system'
}

export interface ProviderProfile {
  id: string
  name: string
  provider_type: string
  default_model: string
  models: string[]
  parameters?: Record<string, string[]>
}

export interface ModelProfile {
  id: string
  provider_id: string
  provider_name: string
  model: string
  name: string
  default?: boolean
  parameter_template: string
  parameters?: Record<string, string[]>
}

// ===== 任务参数 =====

export interface TaskParams {
  modelProfileId?: string
  providerId?: string
  model?: string
  size: string
  quality: 'auto' | 'low' | 'medium' | 'high' | 'standard' | 'hd'
  n: number
}

export type ActualTaskParams = Partial<TaskParams> & Record<string, unknown>

export const DEFAULT_PARAMS: TaskParams = {
  size: 'auto',
  quality: 'auto',
  n: 1,
}

// ===== 输入图片（UI 层面） =====

export interface InputImage {
  /** IndexedDB image store 的 id（SHA-256 hash） */
  id: string
  /** data URL，用于预览 */
  dataUrl: string
}

export interface MaskDraft {
  targetImageId: string
  maskDataUrl: string
  updatedAt: number
}

// ===== 任务记录 =====

export type TaskStatus = 'running' | 'done' | 'error'

export interface TaskRecord {
  id: string
  prompt: string
  params: TaskParams
  /** API 返回的实际生效参数，用于标记与请求值不一致的情况 */
  actualParams?: ActualTaskParams
  /** 输出图片对应的实际生效参数，key 为 outputImages 中的图片 id */
  actualParamsByImage?: Record<string, ActualTaskParams>
  /** 输出图片对应的 API 改写提示词，key 为 outputImages 中的图片 id */
  revisedPromptByImage?: Record<string, string>
  /** 输出图片对应的实际像素尺寸，key 为 outputImages 中的图片 id */
  outputImageDimensions?: Record<string, { width: number; height: number }>
  operation?: 'generate' | 'reference' | 'edit' | string
  modelProfileId?: string
  providerId?: string
  providerName?: string
  model?: string
  /** 可恢复任务标记，兼容旧记录 */
  falRecoverable?: boolean
  customRecoverable?: boolean
  /** 输入图片的 image store id 列表 */
  inputImageIds: string[]
  maskTargetImageId?: string | null
  maskImageId?: string | null
  /** 输出图片的 image store id 列表 */
  outputImages: string[]
  /** API 返回的原始图片 HTTP URL（非 base64 时记录） */
  rawImageUrls?: string[]
  /** 发生解析错误时的原始响应 JSON */
  rawResponsePayload?: string
  status: TaskStatus
  error: string | null
  createdAt: number
  finishedAt: number | null
  /** 总耗时毫秒 */
  elapsed: number | null
  /** 是否收藏 */
  isFavorite?: boolean
  /** 服务端任务 ID；存在时以后端为主数据源 */
  serverJobId?: string
  serverImageUrls?: Record<string, string>
  serverThumbnailUrls?: Record<string, string>
}

// ===== IndexedDB 存储的图片 =====

export interface StoredImage {
  id: string
  dataUrl: string
  createdAt?: number
  source?: 'upload' | 'generated' | 'mask'
  width?: number
  height?: number
}

export interface StoredImageThumbnail {
  id: string
  thumbnailDataUrl: string
  width?: number
  height?: number
  thumbnailVersion?: number
}

// ===== 导出数据（ZIP manifest.json 格式）=====

export interface ExportData {
  version: number
  exportedAt: string
  settings?: AppSettings
  tasks?: TaskRecord[]
  /** imageId → 图片信息 */
  imageFiles?: Record<string, {
    path: string
    createdAt?: number
    source?: 'upload' | 'generated' | 'mask'
    width?: number
    height?: number
  }>
  /** imageId → 缩略图信息 */
  thumbnailFiles?: Record<string, {
    path: string
    width?: number
    height?: number
    thumbnailVersion?: number
  }>
}
