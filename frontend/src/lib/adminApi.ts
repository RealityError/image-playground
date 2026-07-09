export interface AdminOverview {
  total_jobs?: number
  success_jobs?: number
  failed_jobs?: number
  running_jobs?: number
  web_jobs?: number
  api_jobs?: number
  total_images?: number
  total_size_bytes?: number
  jobs_today?: number
  success_today?: number
  failed_today?: number
  owner_spaces?: number
  blocked_owner_spaces?: number
  auth_failures_24h?: number
  avg_elapsed_seconds?: number
  active_users?: number
  active_generations?: number
}

export interface AdminDashboard {
  overview?: AdminOverview
  live?: {
    active_users?: number
    active_generations?: number
    web_sessions?: number
    web_active_slots?: number
    api_active_slots?: number
  }
  recent_failures?: AdminJobItem[]
  size_stats?: Array<{
    size?: string | null
    total_jobs?: number | null
    success_jobs?: number | null
    failed_jobs?: number | null
    avg_elapsed_seconds?: number | null
  }>
}

export interface AdminGalleryItem {
  job_id: string
  image_index: number
  thumbnail_url?: string | null
  original_url?: string | null
  created_at?: string | null
  completed_at?: string | null
  scope?: string | null
  operation?: string | null
  client_ip?: string | null
  owner_type?: string | null
  owner_id?: string | null
  owner_hint?: string | null
  owner_label?: string | null
  owner_note?: string | null
  blocked_reason?: string | null
  prompt?: string | null
  prompt_preview?: string | null
  size_bytes?: number | null
  source?: string | null
  model?: string | null
  elapsed_seconds?: number | null
  image_count?: number | null
  input_image_count?: number | null
  mask_used?: boolean
  deleted_at?: string | null
  deleted_by?: string | null
  deleted_reason?: string | null
  files_removed_at?: string | null
  image_deleted_at?: string | null
  image_deleted_by?: string | null
  image_deleted_reason?: string | null
  image_files_removed_at?: string | null
}

export interface AdminJobItem {
  job_id: string
  created_at?: string | null
  completed_at?: string | null
  scope?: string | null
  route?: string | null
  client_ip?: string | null
  owner_type?: string | null
  owner_id?: string | null
  owner_hint?: string | null
  owner_label?: string | null
  owner_note?: string | null
  blocked_reason?: string | null
  prompt?: string | null
  prompt_preview?: string | null
  model?: string | null
  status?: string | null
  elapsed_seconds?: number | null
  image_count?: number | null
  error_message?: string | null
  operation?: string | null
  request_params_json?: string | null
  input_image_count?: number | null
  mask_used?: boolean
  total_size_bytes?: number | null
  deleted_at?: string | null
  deleted_by?: string | null
  deleted_reason?: string | null
  files_removed_at?: string | null
}

export interface AdminOwnerItem {
  owner_type: string
  owner_id: string
  label?: string | null
  note?: string | null
  blocked_reason?: string | null
  job_count?: number | null
  success_jobs?: number | null
  failed_jobs?: number | null
  image_count?: number | null
  total_size_bytes?: number | null
  last_created_at?: string | null
  first_created_at?: string | null
  owner_hint?: string | null
}

export interface AdminAuthEvent {
  id?: number
  created_at?: string | null
  scope?: string | null
  event_type?: string | null
  success?: number | boolean
  owner_type?: string | null
  owner_id?: string | null
  client_ip?: string | null
  user_agent?: string | null
  detail?: string | null
}

export interface AdminSystemStatus {
  live?: {
    active_users?: number
    active_generations?: number
    web_sessions?: number
    web_active_slots?: number
    api_active_slots?: number
  }
  active_counts?: {
    web?: Record<string, number>
    api?: Record<string, number>
  }
  workers?: {
    background_generation_workers?: number
    web_concurrency_per_session?: number
    api_concurrency_per_ip?: number
  }
  version?: {
    web_client_version?: string
    api_version?: string
    model?: string
    admin_page_path?: string
  }
  storage?: Record<string, number>
  disk?: {
    total_bytes?: number
    used_bytes?: number
    free_bytes?: number
    used_percent?: number
  }
}

export interface AdminListResponse<T> {
  items: T[]
  offset?: number
  next_offset?: number
  total?: number
  has_more?: boolean
}

export interface AdminImageTarget {
  job_id: string
  image_index: number
}

export interface AdminOwnerTarget {
  owner_type: string
  owner_id: string
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

function adminFetch(path: string, options: RequestInit = {}) {
  const headers = new Headers(options.headers || {})
  headers.set('X-Admin-Request', '1')
  return fetch(path, {
    credentials: 'same-origin',
    cache: 'no-store',
    ...options,
    headers,
  })
}

export async function adminJson(path: string, options: RequestInit = {}) {
  return readJsonResponse(await adminFetch(path, options))
}

export async function getAdminSession() {
  return adminJson('/admin/session')
}

export async function loginAdmin(password: string) {
  return adminJson('/admin/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password }),
  })
}

export async function logoutAdmin() {
  return adminJson('/admin/logout', { method: 'POST' })
}

export function buildAdminQuery(params: Record<string, string | number | boolean | null | undefined>) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

export async function softDeleteAdminJobs(jobIds: string[], reason?: string) {
  return adminJson('/admin/jobs/soft-delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ job_ids: jobIds, reason }),
  })
}

export async function softDeleteAdminImages(images: AdminImageTarget[], reason?: string) {
  return adminJson('/admin/images/soft-delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ images, reason }),
  })
}

export async function setAdminOwnersBlocked(owners: AdminOwnerTarget[], blocked: boolean, reason?: string) {
  return adminJson('/admin/owners/block-batch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ owners, blocked, reason }),
  })
}

export interface AdminConfigItem {
  key: string
  value: string
  label: string
  type: 'int' | 'float' | 'str'
  min?: number
  max?: number
}

export interface AdminConfigResponse {
  items: AdminConfigItem[]
}

export interface AdminProviderProfile {
  id: string
  name: string
  provider_type: string
  base_url?: string | null
  enabled: boolean
  default_model: string
  models: string[]
  parameters: Record<string, string[]>
  api_key_configured?: boolean
  api_key_preview?: string
  created_at?: string
  updated_at?: string
}

export interface AdminProvidersResponse {
  items: AdminProviderProfile[]
  defaults?: {
    provider_type?: string
    parameters?: Record<string, string[]>
  }
}

export interface AdminModelProfile {
  id: string
  provider_id: string
  provider_name?: string
  model: string
  name: string
  enabled: boolean
  default: boolean
  parameter_template: string
  parameters: Record<string, string[]>
  provider_api_key_configured?: boolean
}

export interface AdminModelProfilesResponse {
  items: AdminModelProfile[]
  templates: Record<string, Record<string, string[]>>
}

export async function getAdminConfig(): Promise<AdminConfigResponse> {
  const raw = await adminJson('/admin/config')
  const config: Record<string, string> = raw.config ?? {}
  const schema: Record<string, { type: string; label: string; min?: number; max?: number }> = raw.schema ?? {}
  const items: AdminConfigItem[] = Object.entries(schema).map(([key, s]) => ({
    key,
    value: config[key] ?? '',
    label: s.label,
    type: s.type as AdminConfigItem['type'],
    min: s.min,
    max: s.max,
  }))
  return { items }
}

export async function setAdminConfig(config: Record<string, string | number>): Promise<{ ok: boolean }> {
  return adminJson('/admin/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  })
}

export async function getAdminProviders(): Promise<AdminProvidersResponse> {
  return adminJson('/admin/providers')
}

export async function saveAdminProvider(profile: Partial<AdminProviderProfile> & { api_key?: string; clear_api_key?: boolean }) {
  return adminJson('/admin/providers', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(profile),
  })
}

export async function deleteAdminProvider(providerId: string) {
  return adminJson(`/admin/providers/${encodeURIComponent(providerId)}`, {
    method: 'DELETE',
  })
}

export async function getAdminModelProfiles(): Promise<AdminModelProfilesResponse> {
  return adminJson('/admin/model-profiles')
}

export async function saveAdminModelProfile(profile: Partial<AdminModelProfile>) {
  return adminJson('/admin/model-profiles', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(profile),
  })
}

export async function deleteAdminModelProfile(modelProfileId: string) {
  return adminJson(`/admin/model-profiles/${encodeURIComponent(modelProfileId)}`, {
    method: 'DELETE',
  })
}
