"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import { BookMarked, Plus, RefreshCw, Search, Trash2 } from "lucide-react"
import { apiFetch, toQuery, type Page } from "@/lib/api/client"
import type { GlossaryTermRecord } from "@/lib/api/types"
import { ErrorState, LoadingState, NoDataState } from "../api-state"
import { Btn, Input, Panel, Tag, Toolbar } from "../ui"

export function GlossaryPage() {
  const [terms, setTerms] = useState<GlossaryTermRecord[]>([])
  const [query, setQuery] = useState("")
  const [draftEn, setDraftEn] = useState("")
  const [draftZh, setDraftZh] = useState("")
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await apiFetch<Page<GlossaryTermRecord>>(
        `/glossary${toQuery({ keyword: query, pageSize: 100 })}`,
      )
      setTerms(response.data)
    } catch (err) {
      setError(err instanceof Error ? err.message : "术语库加载失败")
    } finally {
      setLoading(false)
    }
  }, [query])

  useEffect(() => {
    void load()
  }, [load])

  const canSave = useMemo(() => draftEn.trim() && draftZh.trim(), [draftEn, draftZh])

  async function saveTerm() {
    if (!canSave) return
    setSaving(true)
    setError(null)
    try {
      await apiFetch<GlossaryTermRecord>("/glossary", {
        method: "POST",
        body: JSON.stringify({ enTerm: draftEn, zhTerm: draftZh }),
      })
      setDraftEn("")
      setDraftZh("")
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : "术语保存失败")
    } finally {
      setSaving(false)
    }
  }

  async function deleteTerm(id: number) {
    await apiFetch<void>(`/glossary/${id}`, { method: "DELETE" })
    await load()
  }

  return (
    <div className="flex h-full flex-col gap-3">
      <Toolbar>
        <Input icon={Search} placeholder="搜索中英文术语" className="w-56" value={query} onChange={(event) => setQuery(event.target.value)} />
        <Btn icon={RefreshCw} onClick={load}>刷新</Btn>
        <span className="ml-auto text-xs text-muted-foreground">{terms.length} 条术语</span>
      </Toolbar>

      <Panel title="新增 / 更新术语" icon={Plus}>
        <div className="grid gap-2 md:grid-cols-[1fr_1fr_auto]">
          <Input placeholder="English term" value={draftEn} onChange={(event) => setDraftEn(event.target.value)} />
          <Input placeholder="中文译名" value={draftZh} onChange={(event) => setDraftZh(event.target.value)} />
          <Btn variant="primary" icon={Plus} disabled={!canSave || saving} onClick={saveTerm}>
            保存
          </Btn>
        </div>
      </Panel>

      <Panel title="化工术语对照库" icon={BookMarked} className="min-h-0" bodyClassName="overflow-auto">
        {loading ? (
          <LoadingState />
        ) : error ? (
          <ErrorState message={error} onRetry={load} />
        ) : terms.length === 0 ? (
          <NoDataState title="还没有术语" hint="保存常用翻译后，文献翻译 job 可以优先使用这些术语。" />
        ) : (
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {terms.map((term) => (
              <div key={term.id} className="group flex flex-col gap-1.5 rounded-lg border border-border bg-card p-3 transition-colors hover:border-primary">
                <div className="flex items-start justify-between gap-2">
                  <span className="text-[15px] font-semibold text-foreground">{term.zhTerm}</span>
                  <button className="text-muted-foreground hover:text-danger" aria-label="删除术语" onClick={() => void deleteTerm(term.id)}>
                    <Trash2 className="size-4" />
                  </button>
                </div>
                <span className="text-[13px] italic text-muted-foreground">{term.enTerm}</span>
                {term.note && <p className="text-xs text-muted-foreground">{term.note}</p>}
                <div className="mt-1">
                  <Tag tone="blue">用户术语</Tag>
                </div>
              </div>
            ))}
          </div>
        )}
      </Panel>
    </div>
  )
}

