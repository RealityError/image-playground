import { dataUrlToBlob, getImageDimensions, maskDataUrlToPngBlob } from './canvasImage'
import { assertImageInputPayloadSize, assertMaskEditFileSize } from './imageApiShared'
import { normalizeImageSize } from './size'
import type { CallApiOptions, CallApiResult } from './imageApiShared'

export type { CallApiOptions, CallApiResult } from './imageApiShared'

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
  if (!response.ok) {
    throw new Error(payload.detail || payload.error_message || `HTTP ${response.status}`)
  }
  return payload
}

async function waitForWebJob(jobId: string, signal?: AbortSignal): Promise<any> {
  let delay = 900
  while (true) {
    await new Promise<void>((resolve, reject) => {
      const timer = globalThis.setTimeout(resolve, delay)
      signal?.addEventListener('abort', () => {
        globalThis.clearTimeout(timer)
        reject(new DOMException('Aborted', 'AbortError'))
      }, { once: true })
    })

    const response = await fetch(`/web/jobs/${encodeURIComponent(jobId)}`, {
      credentials: 'same-origin',
      cache: 'no-store',
      headers: {
        'X-Web-Version': getWebClientVersion(),
        'X-Web-Request': '1',
      },
      signal,
    })
    const payload = await readJsonResponse(response)
    if (payload.status === 'success') return payload
    if (payload.status === 'failed') throw new Error(payload.error_message || '生成失败')
    delay = Math.min(2500, delay + 200)
  }
}

function getWebClientVersion() {
  return typeof window === 'undefined' ? '' : window.__WEB_CLIENT_VERSION__ || ''
}

function appendCommonParams(target: Record<string, unknown> | FormData, opts: CallApiOptions) {
  const params = opts.params
  const entries = [
    ['prompt', opts.prompt],
    ['model_profile_id', params.modelProfileId],
    ['provider_id', params.providerId],
    ['model', params.model],
    ['size', params.size],
    ['quality', params.quality],
  ] as const

  for (const [key, value] of entries) {
    if (!value || value === 'auto') continue
    if (target instanceof FormData) target.append(key, String(value))
    else target[key] = value
  }
}

async function resultToDataUrls(payload: any): Promise<CallApiResult> {
  const images = Array.isArray(payload.images) ? payload.images : []
  const urls = images.map((image: any) => image.url).filter((url: unknown): url is string => typeof url === 'string' && url.length > 0)
  const dimensions = images.map((image: any) => {
    const width = Number(image.width)
    const height = Number(image.height)
    return Number.isFinite(width) && Number.isFinite(height) && width > 0 && height > 0
      ? { width, height }
      : undefined
  })
  return {
    images: urls,
    actualParams: payload.actual_params || undefined,
    actualParamsList: urls.map(() => payload.actual_params || undefined),
    revisedPrompts: images.map((image: any) => image.revised_prompt),
    imageDimensions: dimensions,
    rawImageUrls: urls,
    jobId: payload.job_id,
    imageUrls: urls,
    thumbnailUrls: images.map((image: any) => image.thumbnail_url).filter((url: unknown): url is string => typeof url === 'string' && url.length > 0),
    createdAt: payload.created_at,
    completedAt: payload.completed_at,
    elapsedSeconds: payload.elapsed_seconds,
    provider: payload.provider,
    modelProfile: payload.model_profile,
  }
}

export async function callImageApi(opts: CallApiOptions): Promise<CallApiResult> {
  const hasInputImages = opts.inputImageDataUrls.length > 0
  const isEdit = Boolean(opts.maskDataUrl)
  let response: Response

  if (hasInputImages) {
    const formData = new FormData()
    appendCommonParams(formData, opts)
    if (opts.maskDataUrl && (!opts.params.size || opts.params.size === 'auto') && opts.inputImageDataUrls[0]) {
      const { width, height } = await getImageDimensions(opts.inputImageDataUrls[0])
      formData.append('size', normalizeImageSize(`${width}x${height}`))
    }
    const imageBlobs: Blob[] = []
    for (let i = 0; i < opts.inputImageDataUrls.length; i++) {
      const blob = await dataUrlToBlob(opts.inputImageDataUrls[i])
      imageBlobs.push(blob)
      formData.append('image', blob, `image-${i + 1}.png`)
    }
    if (opts.maskDataUrl) {
      const maskBlob = await maskDataUrlToPngBlob(opts.maskDataUrl)
      assertMaskEditFileSize('遮罩主图文件', imageBlobs[0]?.size ?? 0)
      assertMaskEditFileSize('遮罩文件', maskBlob.size)
      assertImageInputPayloadSize(imageBlobs.reduce((sum, blob) => sum + blob.size, 0) + maskBlob.size)
      formData.append('mask', maskBlob, 'mask.png')
    } else {
      assertImageInputPayloadSize(imageBlobs.reduce((sum, blob) => sum + blob.size, 0))
    }
    response = await fetch(isEdit ? '/web/edit' : '/web/image', {
      method: 'POST',
      credentials: 'same-origin',
      cache: 'no-store',
      headers: {
        'X-Web-Version': getWebClientVersion(),
        'X-Web-Request': '1',
      },
      body: formData,
    })
  } else {
    const body: Record<string, unknown> = {}
    appendCommonParams(body, opts)
    response = await fetch('/web/generate', {
      method: 'POST',
      credentials: 'same-origin',
      cache: 'no-store',
      headers: {
        'Content-Type': 'application/json',
        'X-Web-Version': getWebClientVersion(),
        'X-Web-Request': '1',
      },
      body: JSON.stringify(body),
    })
  }

  let payload = await readJsonResponse(response)
  if (payload.status === 'running' && payload.job_id) {
    opts.onWebJobEnqueued?.({ jobId: payload.job_id, createdAt: payload.created_at })
    payload = await waitForWebJob(payload.job_id)
  }
  return resultToDataUrls(payload)
}
