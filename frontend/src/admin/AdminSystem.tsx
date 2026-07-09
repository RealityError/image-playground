import { useEffect, useState } from 'react'
import {
  adminJson,
  deleteAdminProvider,
  getAdminConfig,
  getAdminProviders,
  saveAdminProvider,
  setAdminConfig,
  type AdminConfigItem,
  type AdminProviderProfile,
  type AdminSystemStatus,
} from '../lib/adminApi'

function formatBytes(size?: number | null) {
  const value = Number(size)
  if (!Number.isFinite(value)) return '-'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(2)} MB`
  return `${(value / 1024 / 1024 / 1024).toFixed(2)} GB`
}

export default function AdminSystem() {
  const [data, setData] = useState<AdminSystemStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    void (async () => {
      try {
        const result = await adminJson('/admin/system')
        setData(result)
      } catch (e) {
        setError(e instanceof Error ? e.message : '加载失败')
      } finally {
        setLoading(false)
      }
    })()
  }, [])

  if (loading) return <p className="text-gray-500 dark:text-gray-400">加载中...</p>
  if (error) return <p className="text-red-500">{error}</p>
  if (!data) return <p className="text-gray-500 dark:text-gray-400">暂无数据</p>

  const live = data.live
  const disk = data.disk
  const workers = data.workers
  const version = data.version

  return (
    <div className="space-y-5">
      {/* Runtime Config */}
      <RuntimeConfigCard />
      <ProviderConfigCard />

      {/* Live Metrics */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <MetricCard label="在线用户" value={live?.active_users ?? 0} />
        <MetricCard label="生成中" value={live?.active_generations ?? 0} />
        <MetricCard label="Web会话" value={live?.web_sessions ?? 0} />
        <MetricCard label="存储使用" value={disk ? formatBytes(disk.used_bytes) : '-'} />
      </div>

      {/* Disk Usage */}
      {disk && (
        <div className="rounded-2xl border border-gray-200 dark:border-white/10 bg-white dark:bg-gray-950 shadow-sm p-4 space-y-3">
          <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">磁盘使用</h3>
          <div className="w-full h-3 bg-gray-100 dark:bg-gray-900 rounded-full overflow-hidden">
            <div
              className="h-full bg-gray-900 dark:bg-gray-100 rounded-full transition-all"
              style={{ width: `${Math.min(disk.used_percent ?? 0, 100)}%` }}
            />
          </div>
          <div className="flex justify-between text-xs text-gray-500 dark:text-gray-400">
            <span>已用 {formatBytes(disk.used_bytes)} ({(disk.used_percent ?? 0).toFixed(1)}%)</span>
            <span>总计 {formatBytes(disk.total_bytes)}</span>
          </div>
        </div>
      )}

      {/* Version Info */}
      {version && (
        <div className="rounded-2xl border border-gray-200 dark:border-white/10 bg-white dark:bg-gray-950 shadow-sm p-4 space-y-2">
          <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">版本信息</h3>
          <div className="grid grid-cols-2 gap-2 text-sm">
            {version.web_client_version && (
              <InfoRow label="Web客户端" value={version.web_client_version} />
            )}
            {version.api_version && (
              <InfoRow label="API版本" value={version.api_version} />
            )}
            {version.model && (
              <InfoRow label="模型" value={version.model} />
            )}
          </div>
        </div>
      )}

      {/* Worker Config */}
      {workers && (
        <div className="rounded-2xl border border-gray-200 dark:border-white/10 bg-white dark:bg-gray-950 shadow-sm p-4 space-y-2">
          <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Worker 配置</h3>
          <div className="grid grid-cols-2 gap-2 text-sm">
            {workers.background_generation_workers != null && (
              <InfoRow label="后台生成Worker" value={String(workers.background_generation_workers)} />
            )}
            {workers.web_concurrency_per_session != null && (
              <InfoRow label="Web并发/会话" value={String(workers.web_concurrency_per_session)} />
            )}
            {workers.api_concurrency_per_ip != null && (
              <InfoRow label="API并发/IP" value={String(workers.api_concurrency_per_ip)} />
            )}
          </div>
        </div>
      )}

      {/* Storage breakdown */}
      {data.storage && Object.keys(data.storage).length > 0 && (
        <div className="rounded-2xl border border-gray-200 dark:border-white/10 bg-white dark:bg-gray-950 shadow-sm p-4 space-y-2">
          <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">存储明细</h3>
          <div className="grid grid-cols-2 gap-2 text-sm">
            {Object.entries(data.storage).map(([key, value]) => (
              <InfoRow key={key} label={key} value={formatBytes(value)} />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function splitCsv(value: string) {
  return value.split(',').map((item) => item.trim()).filter(Boolean)
}

function joinCsv(value?: string[]) {
  return (value || []).join(', ')
}

function defaultProviderDraft(): AdminProviderProfile & { api_key?: string; clear_api_key?: boolean } {
  return {
    id: 'openai-main',
    name: 'OpenAI 主线路',
    provider_type: 'openai-compatible',
    base_url: '',
    enabled: true,
    default_model: 'gpt-image-2',
    models: ['gpt-image-2'],
    parameters: {
      size: ['auto', '1024x1024', '1536x1024', '1024x1536'],
      quality: ['auto', 'low', 'medium', 'high'],
      response_format: ['url', 'b64_json'],
    },
    api_key_configured: false,
    api_key_preview: '',
  }
}

function ProviderConfigCard() {
  const [items, setItems] = useState<AdminProviderProfile[]>([])
  const [draft, setDraft] = useState<(AdminProviderProfile & { api_key?: string; clear_api_key?: boolean })>(defaultProviderDraft())
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const res = await getAdminProviders()
      setItems(res.items || [])
      if (res.items?.[0]) setDraft({ ...res.items[0], api_key: '', clear_api_key: false })
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载 provider 失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const save = async () => {
    setSaving(true)
    setError('')
    setSuccess('')
    try {
      await saveAdminProvider({
        ...draft,
        models: draft.models?.length ? draft.models : [draft.default_model],
      })
      setSuccess('Provider 已保存')
      setDraft((current) => ({ ...current, api_key: '', clear_api_key: false }))
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : '保存 provider 失败')
    } finally {
      setSaving(false)
    }
  }

  const remove = async (providerId: string) => {
    setError('')
    setSuccess('')
    try {
      await deleteAdminProvider(providerId)
      setSuccess('Provider 已删除')
      setDraft(defaultProviderDraft())
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : '删除 provider 失败')
    }
  }

  const update = (patch: Partial<typeof draft>) => setDraft((current) => ({ ...current, ...patch }))

  return (
    <div className="rounded-2xl border border-gray-200 dark:border-white/10 bg-white dark:bg-gray-950 shadow-sm p-4 space-y-3">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Provider 配置</h3>
        <button
          type="button"
          onClick={() => setDraft(defaultProviderDraft())}
          className="rounded-lg bg-gray-100 dark:bg-white/[0.06] px-3 py-1.5 text-xs font-semibold text-gray-700 dark:text-gray-200"
        >
          新建
        </button>
      </div>
      {loading && <p className="text-xs text-gray-500 dark:text-gray-400">加载中...</p>}
      {error && <p className="text-xs text-red-500">{error}</p>}
      {success && <p className="text-xs text-green-600 dark:text-green-400">{success}</p>}
      {items.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {items.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setDraft({ ...item, api_key: '', clear_api_key: false })}
              className={`rounded-lg border px-2.5 py-1 text-xs ${
                draft.id === item.id
                  ? 'border-gray-900 bg-gray-900 text-white dark:border-white dark:bg-white dark:text-gray-950'
                  : 'border-gray-200 bg-gray-50 text-gray-600 dark:border-white/10 dark:bg-white/[0.04] dark:text-gray-300'
              }`}
            >
              {item.name}
            </button>
          ))}
        </div>
      )}
      <div className="grid gap-2 sm:grid-cols-2">
        <AdminTextInput label="ID" value={draft.id} onChange={(value) => update({ id: value })} />
        <AdminTextInput label="名称" value={draft.name} onChange={(value) => update({ name: value })} />
        <AdminTextInput label="Base URL" value={draft.base_url || ''} onChange={(value) => update({ base_url: value })} />
        <AdminTextInput label="默认模型" value={draft.default_model} onChange={(value) => update({ default_model: value })} />
        <AdminTextInput label="模型列表" value={joinCsv(draft.models)} onChange={(value) => update({ models: splitCsv(value) })} />
        <AdminTextInput label="API Key" type="password" value={draft.api_key || ''} placeholder={draft.api_key_configured ? `已配置 ${draft.api_key_preview || ''}` : '未配置'} onChange={(value) => update({ api_key: value, clear_api_key: false })} />
      </div>
      <div className="grid gap-2 sm:grid-cols-3">
        {(['size', 'quality', 'response_format'] as const).map((key) => (
          <AdminTextInput
            key={key}
            label={`参数 ${key}`}
            value={joinCsv(draft.parameters?.[key])}
            onChange={(value) => update({ parameters: { ...(draft.parameters || {}), [key]: splitCsv(value) } })}
          />
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <label className="inline-flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300">
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(e) => update({ enabled: e.target.checked })}
          />
          启用
        </label>
        <label className="inline-flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300">
          <input
            type="checkbox"
            checked={Boolean(draft.clear_api_key)}
            onChange={(e) => update({ clear_api_key: e.target.checked, api_key: '' })}
          />
          清除 API Key
        </label>
        <button
          type="button"
          onClick={() => void save()}
          disabled={saving}
          className="rounded-lg bg-gray-900 dark:bg-gray-100 px-3 py-1.5 text-xs font-semibold text-white dark:text-gray-950 disabled:opacity-50"
        >
          {saving ? '保存中...' : '保存 Provider'}
        </button>
        {items.some((item) => item.id === draft.id) && (
          <button
            type="button"
            onClick={() => void remove(draft.id)}
            className="rounded-lg bg-red-50 px-3 py-1.5 text-xs font-semibold text-red-600 dark:bg-red-500/10 dark:text-red-400"
          >
            删除
          </button>
        )}
      </div>
    </div>
  )
}

function AdminTextInput({
  label,
  value,
  onChange,
  type = 'text',
  placeholder = '',
}: {
  label: string
  value: string
  onChange: (value: string) => void
  type?: string
  placeholder?: string
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs text-gray-500 dark:text-gray-400">{label}</span>
      <input
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-lg border border-gray-300 dark:border-white/10 bg-white dark:bg-gray-900 px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 outline-none focus:ring-2 focus:ring-gray-900/10"
      />
    </label>
  )
}

function RuntimeConfigCard() {
  const [items, setItems] = useState<AdminConfigItem[]>([])
  const [values, setValues] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

  useEffect(() => {
    void (async () => {
      try {
        const res = await getAdminConfig()
        setItems(res.items)
        const v: Record<string, string> = {}
        for (const item of res.items) v[item.key] = item.value
        setValues(v)
      } catch (e) {
        setError(e instanceof Error ? e.message : '加载配置失败')
      }
    })()
  }, [])

  const save = async (key: string) => {
    setSaving(key)
    setError('')
    setSuccess('')
    try {
      await setAdminConfig({ [key]: values[key] })
      setSuccess(`${key} 已保存`)
      setTimeout(() => setSuccess(''), 2000)
    } catch (e) {
      setError(e instanceof Error ? e.message : '保存失败')
    } finally {
      setSaving(null)
    }
  }

  if (!items.length && !error) return null

  return (
    <div className="rounded-2xl border border-gray-200 dark:border-white/10 bg-white dark:bg-gray-950 shadow-sm p-4 space-y-3">
      <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">运行时配置</h3>
      {error && <p className="text-xs text-red-500">{error}</p>}
      {success && <p className="text-xs text-green-600 dark:text-green-400">{success}</p>}
      <div className="space-y-2">
        {items.map((item) => (
          <div key={item.key} className="flex items-center gap-2">
            <label className="text-sm text-gray-500 dark:text-gray-400 w-36 shrink-0">{item.label}</label>
            <input
              type={item.type === 'str' ? 'text' : 'number'}
              value={values[item.key] ?? ''}
              onChange={(e) => setValues((v) => ({ ...v, [item.key]: e.target.value }))}
              min={item.min}
              max={item.max}
              className="flex-1 min-w-0 rounded-lg border border-gray-300 dark:border-white/10 bg-white dark:bg-gray-900 px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 outline-none focus:ring-2 focus:ring-gray-900/10"
            />
            <button
              onClick={() => void save(item.key)}
              disabled={saving === item.key}
              className="shrink-0 rounded-lg bg-gray-900 dark:bg-gray-100 text-white dark:text-gray-950 px-3 py-1.5 text-xs font-semibold disabled:opacity-50"
            >
              {saving === item.key ? '...' : '保存'}
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}

function MetricCard({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-2xl border border-gray-200 dark:border-white/10 bg-white dark:bg-gray-950 shadow-sm p-4">
      <p className="text-xs text-gray-500 dark:text-gray-400">{label}</p>
      <p className="mt-1 text-2xl font-bold text-gray-900 dark:text-gray-100">{value}</p>
    </div>
  )
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span className="text-gray-500 dark:text-gray-400">{label}: </span>
      <span className="text-gray-900 dark:text-gray-100">{value}</span>
    </div>
  )
}
