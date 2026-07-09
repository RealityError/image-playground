import type { ActualTaskParams, TaskParams } from '../types'

export const MAX_MASK_EDIT_FILE_BYTES = 50 * 1024 * 1024
export const MAX_IMAGE_INPUT_PAYLOAD_BYTES = 512 * 1024 * 1024

export interface CallApiOptions {
  prompt: string
  params: TaskParams
  inputImageDataUrls: string[]
  maskDataUrl?: string
  onWebJobEnqueued?: (job: { jobId: string; createdAt?: string }) => void
}

export interface CallApiResult {
  images: string[]
  actualParams?: ActualTaskParams
  actualParamsList?: Array<ActualTaskParams | undefined>
  revisedPrompts?: Array<string | undefined>
  imageDimensions?: Array<{ width: number; height: number } | undefined>
  rawImageUrls?: string[]
  jobId?: string
  imageUrls?: string[]
  thumbnailUrls?: string[]
  createdAt?: string
  completedAt?: string
  elapsedSeconds?: number
  provider?: {
    id?: string
    name?: string
    provider_type?: string
    default_model?: string
  }
}

function formatMiB(bytes: number): string {
  return `${(bytes / 1024 / 1024).toFixed(1)} MiB`
}

function assertMaxBytes(label: string, bytes: number, maxBytes: number) {
  if (bytes > maxBytes) {
    throw new Error(`${label}过大：${formatMiB(bytes)}，上限为 ${formatMiB(maxBytes)}`)
  }
}

export function assertImageInputPayloadSize(bytes: number) {
  assertMaxBytes('图像输入有效负载总大小', bytes, MAX_IMAGE_INPUT_PAYLOAD_BYTES)
}

export function assertMaskEditFileSize(label: string, bytes: number) {
  assertMaxBytes(label, bytes, MAX_MASK_EDIT_FILE_BYTES)
}

export function isDataUrl(value: unknown): value is string {
  return typeof value === 'string' && value.startsWith('data:')
}

export async function fetchImageUrlAsDataUrl(url: string, fallbackMime: string, signal?: AbortSignal): Promise<string> {
  if (isDataUrl(url)) return url

  const response = await fetch(url, { cache: 'no-store', signal })
  if (!response.ok) {
    throw new Error(`图片 URL 下载失败：HTTP ${response.status}`)
  }

  const blob = await response.blob()
  const bytes = new Uint8Array(await blob.arrayBuffer())
  let binary = ''
  for (let i = 0; i < bytes.length; i += 0x8000) {
    const chunk = bytes.subarray(i, i + 0x8000)
    binary += String.fromCharCode(...chunk)
  }
  return `data:${blob.type || fallbackMime};base64,${btoa(binary)}`
}
