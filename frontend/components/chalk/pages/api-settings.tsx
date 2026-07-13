"use client"

import { useCallback, useEffect, useState } from "react"
import { Check, Cpu, Eye, EyeOff, KeyRound, RefreshCw, Search } from "lucide-react"
import { apiFetch } from "@/lib/api/client"
import { Btn, FieldLabel, Input, Panel, StatusDot, Tag } from "../ui"

type Provider = "dashscope" | "semantic_scholar" | "ncbi" | "crossref_mailto"
type KeyStatus = { configured: Record<Provider, boolean> }

const PROVIDERS: Array<{ id: Provider; label: string; hint: string; placeholder: string }> = [
  { id: "dashscope", label: "DashScope API Key", hint: "文献向量化、问答和模型分析", placeholder: "sk-..." },
  { id: "semantic_scholar", label: "Semantic Scholar API Key", hint: "提高 Semantic Scholar 检索配额", placeholder: "可选" },
  { id: "ncbi", label: "NCBI API Key", hint: "提高 PMC / Entrez 检索配额", placeholder: "可选" },
  { id: "crossref_mailto", label: "Crossref 联系邮箱", hint: "进入 Crossref polite pool", placeholder: "researcher@example.com" },
]

const EMPTY_STATUS: Record<Provider, boolean> = {
  dashscope: false,
  semantic_scholar: false,
  ncbi: false,
  crossref_mailto: false,
}

const EMPTY_VALUES: Record<Provider, string> = {
  dashscope: "",
  semantic_scholar: "",
  ncbi: "",
  crossref_mailto: "",
}

export function ApiSettingsPage() {
  const [showSecrets, setShowSecrets] = useState(false)
  const [values, setValues] = useState<Record<Provider, string>>(EMPTY_VALUES)
  const [configured, setConfigured] = useState<Record<Provider, boolean>>(EMPTY_STATUS)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState<Provider | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const loadStatus = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const status = await apiFetch<KeyStatus>("/settings/api-keys")
      setConfigured({ ...EMPTY_STATUS, ...status.configured })
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "配置状态加载失败")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void loadStatus() }, [loadStatus])

  async function saveProvider(provider: Provider) {
    const value = values[provider].trim()
    if (!value) return
    setSaving(provider)
    setMessage(null)
    setError(null)
    try {
      const status = await apiFetch<KeyStatus>("/settings/api-keys", {
        method: "PUT",
        body: JSON.stringify({ provider, apiKey: value }),
      })
      setConfigured({ ...EMPTY_STATUS, ...status.configured })
      setValues((current) => ({ ...current, [provider]: "" }))
      setMessage(`${PROVIDERS.find((item) => item.id === provider)?.label} 已保存`)
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "配置保存失败")
    } finally {
      setSaving(null)
    }
  }

  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-[360px_1fr]">
      <Panel title="服务凭据" icon={Cpu}>
        <div className="space-y-2">
          {PROVIDERS.map((provider) => (
            <div key={provider.id} className="flex items-center gap-3 border-b border-border py-2 last:border-0">
              <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-primary-soft text-primary">
                {provider.id === "dashscope" ? <Cpu className="size-4" /> : <Search className="size-4" />}
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate text-[13px] font-medium text-foreground">{provider.label}</p>
                <p className="text-[11px] text-muted-foreground">{provider.hint}</p>
              </div>
              <Tag tone={configured[provider.id] ? "green" : "gray"}>{configured[provider.id] ? "已配置" : "未配置"}</Tag>
            </div>
          ))}
        </div>
        <div className="mt-3 flex items-center justify-between gap-2 border-t border-border pt-3">
          <StatusDot tone={error ? "red" : configured.dashscope ? "green" : "gray"} label={error || (configured.dashscope ? "模型服务凭据已保存" : "模型任务需要 DashScope Key")} />
          <Btn size="xs" icon={RefreshCw} onClick={loadStatus} disabled={loading}>刷新</Btn>
        </div>
      </Panel>

      <Panel title="API 配置" icon={KeyRound}>
        <div className="space-y-3">
          {PROVIDERS.map((provider) => (
            <div key={provider.id}>
              <FieldLabel>{provider.label}</FieldLabel>
              <div className="flex gap-2">
                <div className="relative min-w-0 flex-1">
                  <Input
                    type={provider.id === "crossref_mailto" || showSecrets ? "text" : "password"}
                    value={values[provider.id]}
                    onChange={(event) => setValues((current) => ({ ...current, [provider.id]: event.target.value }))}
                    placeholder={configured[provider.id] ? "输入新值可覆盖现有配置" : provider.placeholder}
                    className={provider.id === "crossref_mailto" ? "" : "pr-9 font-mono"}
                  />
                  {provider.id !== "crossref_mailto" && (
                    <button type="button" onClick={() => setShowSecrets((value) => !value)} className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground" aria-label={showSecrets ? "隐藏密钥" : "显示密钥"}>
                      {showSecrets ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                    </button>
                  )}
                </div>
                <Btn icon={Check} disabled={!values[provider.id].trim() || saving !== null} onClick={() => void saveProvider(provider.id)}>
                  {saving === provider.id ? "保存中" : "保存"}
                </Btn>
              </div>
            </div>
          ))}
          {message && <p className="text-xs text-success">{message}</p>}
          {error && <p className="text-xs text-danger">{error}</p>}
        </div>
      </Panel>
    </div>
  )
}
