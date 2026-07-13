"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import { Beaker, CalendarDays, Database, FlaskConical, Plus, RefreshCw, Save, Search, Sparkles, Square, Trash2, User2 } from "lucide-react"
import { apiFetch, type Page } from "@/lib/api/client"
import { cancelJob, createJob, waitForJob } from "@/lib/api/jobs"
import type { DocumentRecord, Job, LabRecord } from "@/lib/api/types"
import { ErrorState, LoadingState, NoDataState } from "../api-state"
import { Btn, FieldLabel, Input, Panel, Select, Tag, Textarea, Toolbar } from "../ui"

type LabSuggestionResult = { recordId: number; suggestion: string }

export function NotesPage() {
  const [records, setRecords] = useState<LabRecord[]>([])
  const [activeId, setActiveId] = useState<number | null>(null)
  const [title, setTitle] = useState("")
  const [content, setContent] = useState("")
  const [documents, setDocuments] = useState<DocumentRecord[]>([])
  const [relatedDocumentId, setRelatedDocumentId] = useState(0)
  const [suggestion, setSuggestion] = useState("")
  const [query, setQuery] = useState("")
  const [job, setJob] = useState<Job<LabSuggestionResult> | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await apiFetch<Page<LabRecord>>(`/lab-records?pageSize=100&keyword=${encodeURIComponent(query)}`)
      setRecords(response.data)
      const first = response.data[0]
      setActiveId((current) => current ?? first?.id ?? null)
    } catch (err) {
      setError(err instanceof Error ? err.message : "实验记录加载失败")
    } finally {
      setLoading(false)
    }
  }, [query])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    apiFetch<Page<DocumentRecord>>("/documents?pageSize=100")
      .then((response) => setDocuments(response.data))
      .catch(() => setDocuments([]))
  }, [])

  const active = useMemo(() => records.find((record) => record.id === activeId) ?? null, [records, activeId])

  useEffect(() => {
    setTitle(active?.title ?? "")
    setContent(active?.content ?? "")
    setSuggestion(active?.aiSuggestion ?? "")
    try {
      const related = JSON.parse(active?.relatedDocIds || "[]")
      setRelatedDocumentId(Number(related[0]) || 0)
    } catch {
      setRelatedDocumentId(0)
    }
    setMessage(null)
  }, [active])

  async function createRecord() {
    try {
      const created = await apiFetch<LabRecord>("/lab-records", {
        method: "POST",
        body: JSON.stringify({ title: "新的实验记录", content: "" }),
      })
      await load()
      setActiveId(created.id)
    } catch (createError) {
      setError(createError instanceof Error ? createError.message : "实验记录创建失败")
    }
  }

  async function saveRecord(): Promise<LabRecord | null> {
    if (!active || !title.trim()) return null
    try {
      const updated = await apiFetch<LabRecord>(`/lab-records/${active.id}`, {
        method: "PATCH",
        body: JSON.stringify({ title: title.trim(), content, relatedDocIds: relatedDocumentId ? JSON.stringify([relatedDocumentId]) : null }),
      })
      setRecords((items) => items.map((item) => (item.id === updated.id ? updated : item)))
      setMessage("实验记录已保存")
      return updated
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "实验记录保存失败")
      return null
    }
  }

  async function deleteRecord() {
    if (!active || !window.confirm(`删除实验记录“${active.title}”？`)) return
    try {
      await apiFetch<void>(`/lab-records/${active.id}`, { method: "DELETE" })
      setActiveId(null)
      await load()
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "实验记录删除失败")
    }
  }

  async function requestSuggestion() {
    if (!active || !content.trim()) return
    setError(null)
    setMessage(null)
    const saved = await saveRecord()
    if (!saved) return
    try {
      const created = await createJob<LabSuggestionResult>("lab_suggest", { recordId: active.id })
      setJob(created)
      const completed = await waitForJob(created, { onUpdate: setJob })
      if (completed.status !== "SUCCEEDED" || !completed.result) {
        throw new Error(completed.error?.message || "AI 建议生成失败")
      }
      setSuggestion(completed.result.suggestion)
      setRecords((items) => items.map((item) => item.id === active.id ? { ...item, aiSuggestion: completed.result!.suggestion } : item))
      setJob(null)
    } catch (suggestError) {
      setError(suggestError instanceof Error ? suggestError.message : "AI 建议生成失败")
    }
  }

  async function cancelSuggestion() {
    if (!job) return
    try {
      setJob(await cancelJob<LabSuggestionResult>(job.id))
    } catch (cancelError) {
      setError(cancelError instanceof Error ? cancelError.message : "任务取消失败")
    }
  }

  async function indexEvidence() {
    if (!active) return
    try {
      const result = await apiFetch<{ domainEvidence: number }>(`/evidence/lab-records/${active.id}/index`, { method: "POST" })
      setMessage(`已索引 ${result.domainEvidence || 0} 条实验证据`)
    } catch (indexError) {
      setError(indexError instanceof Error ? indexError.message : "证据索引失败")
    }
  }

  const running = Boolean(job && ["QUEUED", "RUNNING"].includes(job.status))

  return (
    <div className="flex h-full flex-col gap-3">
      <Toolbar>
        <Btn variant="primary" icon={Plus} onClick={createRecord}>新建实验记录</Btn>
        <Btn icon={RefreshCw} onClick={load}>刷新</Btn>
        <Input icon={Search} placeholder="搜索实验记录" value={query} onChange={(event) => setQuery(event.target.value)} className="w-52" />
        <Btn variant="danger" icon={Trash2} disabled={!active} onClick={() => void deleteRecord()}>删除</Btn>
        <span className="ml-auto text-xs text-muted-foreground">共 {records.length} 条记录</span>
      </Toolbar>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-[300px_1fr]">
        <Panel title="记录列表" icon={Beaker} noPadding className="min-h-0" bodyClassName="overflow-auto">
          {loading ? (
            <LoadingState />
          ) : error ? (
            <ErrorState message={error} onRetry={load} />
          ) : records.length === 0 ? (
            <NoDataState title="还没有实验记录" />
          ) : (
            records.map((record) => (
              <button
                key={record.id}
                onClick={() => setActiveId(record.id)}
                className={`flex w-full flex-col gap-1 border-b border-border px-3 py-2.5 text-left transition-colors ${
                  activeId === record.id ? "bg-primary-soft" : "hover:bg-secondary"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-[11px] text-muted-foreground">LN-{record.id}</span>
                  <Tag tone="orange">草稿</Tag>
                </div>
                <p className="line-clamp-2 text-[13px] font-medium leading-snug text-foreground">{record.title}</p>
                <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
                  <span className="inline-flex items-center gap-1"><User2 className="size-3" />当前用户</span>
                  <span className="inline-flex items-center gap-1"><CalendarDays className="size-3" />{new Date(record.updatedAt).toLocaleDateString()}</span>
                </div>
              </button>
            ))
          )}
        </Panel>

        <Panel
          title={active?.title ?? "实验记录"}
          icon={FlaskConical}
          extra={<Btn size="xs" icon={Save} onClick={saveRecord} disabled={!active}>保存</Btn>}
          bodyClassName="overflow-auto space-y-3"
        >
          {!active ? (
            <NoDataState title="请选择或新建实验记录" />
          ) : (
            <>
              <Input value={title} onChange={(event) => setTitle(event.target.value)} />
              <div>
                <FieldLabel>关联文献</FieldLabel>
                <Select aria-label="关联文献" className="w-full" value={relatedDocumentId} onChange={(event) => setRelatedDocumentId(Number(event.target.value))}>
                  <option value={0}>不关联文献</option>
                  {documents.map((document) => <option key={document.id} value={document.id}>{document.title}</option>)}
                </Select>
              </div>
              <Textarea className="min-h-[360px]" value={content} onChange={(event) => setContent(event.target.value)} />
              <section className="border-t border-border pt-3">
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <h3 className="text-xs font-semibold text-foreground">AI 实验建议</h3>
                  {running ? <Btn size="xs" variant="danger" icon={Square} onClick={() => void cancelSuggestion()}>取消</Btn> : <Btn size="xs" icon={Sparkles} disabled={!content.trim()} onClick={() => void requestSuggestion()}>生成建议</Btn>}
                  <Btn size="xs" icon={Database} onClick={() => void indexEvidence()}>加入证据库</Btn>
                  {job && <span className="text-xs tabular-nums text-muted-foreground">{job.progress}% {job.message}</span>}
                </div>
                {suggestion ? <div className="whitespace-pre-wrap border border-border bg-secondary p-3 text-[13px] leading-6 text-foreground">{suggestion}</div> : <NoDataState title="尚未生成 AI 建议" className="py-6" />}
              </section>
              {message && <p className="text-xs text-success">{message}</p>}
              {error && <p className="text-xs text-danger">{error}</p>}
              <p className="text-xs text-muted-foreground">
                最后更新：{new Date(active.updatedAt).toLocaleString()}
              </p>
            </>
          )}
        </Panel>
      </div>
    </div>
  )
}
