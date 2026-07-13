"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Download, Eye, FileText, FileUp, GitCompareArrows, MessageSquareText, RefreshCw, Search, Trash2, X } from "lucide-react"
import { apiFetch, apiUrl, toQuery, type Page } from "@/lib/api/client"
import { cancelJob, createJob, getLatestActiveJob, listJobs, waitForJob } from "@/lib/api/jobs"
import type {
  ComparisonAnalysisResult,
  DocumentAnalysisRecord,
  DocumentAnalysisType,
  DocumentRecord,
  Job,
  ReactionItem,
  TranslationAnalysisResult,
} from "@/lib/api/types"
import { cn } from "@/lib/utils"
import { ErrorState, LoadingState, NoDataState } from "../api-state"
import { Btn, Input, Panel, Tag, Toolbar } from "../ui"
import { AnalysisMap, ComparisonDialog, LiteratureAnalysisView } from "./literature-analysis"

type ImportResult = { documentId: number; title: string; chunkCount: number }

const ANALYSIS_TYPES = new Set<DocumentAnalysisType>([
  "summary", "images", "structures", "safety", "sop", "reactions", "translation",
])

export function LiteraturePage({ onAskDocument }: { onAskDocument?: (documentId: number) => void }) {
  const [documents, setDocuments] = useState<DocumentRecord[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [compareIds, setCompareIds] = useState<Set<number>>(new Set())
  const [query, setQuery] = useState("")
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [uploadJob, setUploadJob] = useState<Job<ImportResult> | null>(null)

  const [analyses, setAnalyses] = useState<AnalysisMap>({})
  const [analysisLoading, setAnalysisLoading] = useState(false)
  const [analysisError, setAnalysisError] = useState<string | null>(null)
  const [analysisJob, setAnalysisJob] = useState<Job<DocumentAnalysisRecord> | null>(null)
  const [actionMessage, setActionMessage] = useState<string | null>(null)

  const [comparisonOpen, setComparisonOpen] = useState(false)
  const [comparisonRecord, setComparisonRecord] = useState<DocumentAnalysisRecord<ComparisonAnalysisResult> | null>(null)
  const [comparisonJob, setComparisonJob] = useState<Job<DocumentAnalysisRecord<ComparisonAnalysisResult>> | null>(null)
  const [comparisonError, setComparisonError] = useState<string | null>(null)

  const fileInputRef = useRef<HTMLInputElement>(null)
  const reimportInputRef = useRef<HTMLInputElement>(null)
  const importPollRef = useRef<AbortController | null>(null)
  const analysisPollRef = useRef<AbortController | null>(null)
  const comparisonPollRef = useRef<AbortController | null>(null)

  useEffect(() => () => {
    importPollRef.current?.abort()
    analysisPollRef.current?.abort()
    comparisonPollRef.current?.abort()
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await apiFetch<Page<DocumentRecord>>(
        `/documents${toQuery({ keyword: query, pageSize: 100 })}`,
      )
      setDocuments(response.data)
      setSelectedId((current) => (
        current && response.data.some((document) => document.id === current)
          ? current
          : response.data[0]?.id ?? null
      ))
      setCompareIds((current) => new Set([...current].filter((id) => response.data.some((document) => document.id === id))))
    } catch (err) {
      setError(err instanceof Error ? err.message : "文献列表加载失败")
    } finally {
      setLoading(false)
    }
  }, [query])

  const followImportJob = useCallback(async (queued: Job<ImportResult>, controller: AbortController) => {
    const completed = await waitForJob(queued, { signal: controller.signal, onUpdate: setUploadJob })
    if (completed.status === "SUCCEEDED" && completed.result) {
      setSelectedId(completed.result.documentId)
      await load()
      setUploadJob(null)
    } else if (completed.status !== "CANCELLED") {
      setError(completed.error?.message || "PDF 导入失败")
    }
  }, [load])

  useEffect(() => {
    void load()
    void Promise.all([
      getLatestActiveJob<ImportResult>("pdf_import"),
      getLatestActiveJob<ImportResult>("pdf_reimport"),
    ]).then((activeJobs) => {
      const activeJob = activeJobs.filter((candidate): candidate is Job<ImportResult> => Boolean(candidate)).sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))[0]
      if (!activeJob) return
      const controller = new AbortController()
      importPollRef.current = controller
      return followImportJob(activeJob, controller)
    }).catch((err) => setError(err instanceof Error ? err.message : "无法恢复导入任务"))
    return () => importPollRef.current?.abort()
  }, [followImportJob, load])

  const selected = useMemo(
    () => documents.find((document) => document.id === selectedId) ?? documents[0],
    [documents, selectedId],
  )

  const comparisonDocuments = useMemo(
    () => documents.filter((document) => compareIds.has(document.id)).sort((a, b) => a.id - b.id),
    [compareIds, documents],
  )

  const followAnalysisJob = useCallback(async (
    queued: Job<DocumentAnalysisRecord>,
    documentId: number,
    controller: AbortController,
  ) => {
    try {
      const completed = await waitForJob(queued, { signal: controller.signal, onUpdate: setAnalysisJob })
      if (completed.status === "SUCCEEDED" && completed.result) {
        const record = completed.result
        if (record.documentIds.includes(documentId) && ANALYSIS_TYPES.has(record.type as DocumentAnalysisType)) {
          setAnalyses((current) => ({ ...current, [record.type]: record }))
          if (record.type === "summary") {
            const summary = String((record.result as { summary?: string }).summary || "")
            setDocuments((current) => current.map((document) => document.id === documentId ? { ...document, summary } : document))
          }
        }
        setAnalysisJob(null)
      } else if (completed.status !== "CANCELLED") {
        setAnalysisError(completed.error?.message || "文献分析失败")
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return
      setAnalysisError(err instanceof Error ? err.message : "无法获取分析任务状态")
    }
  }, [])

  useEffect(() => {
    analysisPollRef.current?.abort()
    setAnalyses({})
    setAnalysisJob(null)
    setAnalysisError(null)
    setActionMessage(null)
    if (!selected?.id) return

    const controller = new AbortController()
    analysisPollRef.current = controller
    setAnalysisLoading(true)
    void Promise.all([
      apiFetch<{ data: DocumentAnalysisRecord[] }>(`/documents/${selected.id}/analyses`),
      listJobs<DocumentAnalysisRecord>({ type: "document_analysis", pageSize: 100 }),
    ]).then(([response, jobs]) => {
      if (controller.signal.aborted) return
      const mapped: AnalysisMap = {}
      for (const record of response.data) {
        if (ANALYSIS_TYPES.has(record.type as DocumentAnalysisType)) mapped[record.type as DocumentAnalysisType] = record
      }
      setAnalyses(mapped)
      const active = jobs.data.find((job) => (
        (job.status === "QUEUED" || job.status === "RUNNING")
        && job.resource?.documentId === selected.id
      ))
      if (active) void followAnalysisJob(active, selected.id, controller)
    }).catch((err) => {
      if (!controller.signal.aborted) setAnalysisError(err instanceof Error ? err.message : "分析结果加载失败")
    }).finally(() => {
      if (!controller.signal.aborted) setAnalysisLoading(false)
    })
    return () => controller.abort()
  }, [followAnalysisJob, selected?.id])

  async function importPdf(file: File) {
    importPollRef.current?.abort()
    const controller = new AbortController()
    importPollRef.current = controller
    setError(null)
    const form = new FormData()
    form.set("file", file)
    try {
      const queued = await apiFetch<Job<ImportResult>>("/documents/import", { method: "POST", body: form })
      await followImportJob(queued, controller)
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return
      setError(err instanceof Error ? err.message : "PDF 导入失败")
    } finally {
      if (fileInputRef.current) fileInputRef.current.value = ""
    }
  }

  async function reimportPdf(file: File) {
    if (!selected) return
    importPollRef.current?.abort()
    const controller = new AbortController()
    importPollRef.current = controller
    setError(null)
    const form = new FormData()
    form.set("file", file)
    try {
      const queued = await apiFetch<Job<ImportResult>>(`/documents/${selected.id}/reimport`, { method: "POST", body: form })
      await followImportJob(queued, controller)
    } catch (reimportError) {
      if (reimportError instanceof DOMException && reimportError.name === "AbortError") return
      setError(reimportError instanceof Error ? reimportError.message : "PDF 重新导入失败")
    } finally {
      if (reimportInputRef.current) reimportInputRef.current.value = ""
    }
  }

  async function stopImport() {
    if (!uploadJob) return
    try {
      const cancelled = await cancelJob<ImportResult>(uploadJob.id)
      setUploadJob(cancelled)
      importPollRef.current?.abort()
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法取消导入任务")
    }
  }

  function openPdf() {
    if (!selected || selected.sourceType !== "pdf") return
    window.open(apiUrl(`/documents/${selected.id}/content`), "_blank", "noopener,noreferrer")
  }

  function exportDocuments() {
    if (documents.length === 0) return
    const rows = [
      ["id", "title", "sourceType", "fileName", "summary", "createdAt"],
      ...documents.map((document) => [document.id, document.title, document.sourceType, document.fileName || "", document.summary || "", document.createdAt]),
    ]
    const csv = rows.map((row) => row.map((value) => `"${String(value).replaceAll('"', '""')}"`).join(",")).join("\r\n")
    const url = URL.createObjectURL(new Blob(["\uFEFF", csv], { type: "text/csv;charset=utf-8" }))
    const anchor = document.createElement("a")
    anchor.href = url
    anchor.download = "chalk-documents.csv"
    anchor.click()
    URL.revokeObjectURL(url)
  }

  async function runAnalysis(type: DocumentAnalysisType, options: { chemicalName?: string; useLlmPageHints?: boolean } = {}) {
    if (!selected) return
    analysisPollRef.current?.abort()
    const controller = new AbortController()
    analysisPollRef.current = controller
    setAnalysisError(null)
    setActionMessage(null)
    try {
      const payload: Record<string, unknown> = { documentId: selected.id, analysisType: type }
      if (options.chemicalName) payload.chemicalName = options.chemicalName
      if (options.useLlmPageHints) payload.useLlmPageHints = true
      const queued = await createJob<DocumentAnalysisRecord>("document_analysis", payload)
      setAnalysisJob(queued)
      await followAnalysisJob(queued, selected.id, controller)
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return
      setAnalysisError(err instanceof Error ? err.message : "无法创建分析任务")
    }
  }

  async function stopAnalysis() {
    if (!analysisJob) return
    try {
      const cancelled = await cancelJob<DocumentAnalysisRecord>(analysisJob.id)
      setAnalysisJob(cancelled)
      analysisPollRef.current?.abort()
    } catch (err) {
      setAnalysisError(err instanceof Error ? err.message : "无法取消分析任务")
    }
  }

  async function deleteSelected() {
    if (!selected || !window.confirm(`确定删除“${selected.title}”吗？`)) return
    setError(null)
    try {
      await apiFetch<void>(`/documents/${selected.id}`, { method: "DELETE" })
      setSelectedId(null)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : "文献删除失败")
    }
  }

  function toggleCompare(documentId: number) {
    setCompareIds((current) => {
      const next = new Set(current)
      if (next.has(documentId)) {
        next.delete(documentId)
      } else if (next.size < 5) {
        next.add(documentId)
      } else {
        setError("一次最多选择 5 篇文献进行对比")
      }
      return next
    })
  }

  async function createLabRecord(reaction: ReactionItem) {
    if (!selected) return
    setActionMessage(null)
    setAnalysisError(null)
    const content = [
      "## 反应方程式", "", `${reaction.reactants.join(" + ")}  →  ${reaction.products.join(" + ")}`, "",
      "## 反应条件",
      `- **催化剂/试剂**：${reaction.catalyst || "—"}`,
      `- **溶剂**：${reaction.solvent || "—"}`,
      `- **温度**：${reaction.temperature || "—"}`,
      `- **时间**：${reaction.time || "—"}`,
      `- **压力**：${reaction.pressure || "—"}`,
      `- **pH**：${reaction.ph || "—"}`,
      `- **产率**：${reaction.yield || "—"}`, "",
      "## 后处理", reaction.workup || "（待补充）", "", "## 实验记录", "", "## 备注", reaction.notes || "",
    ].join("\n")
    try {
      await apiFetch("/lab-records", {
        method: "POST",
        body: JSON.stringify({
          title: reaction.name || "化学反应实验",
          content,
          relatedDocIds: JSON.stringify([selected.id]),
        }),
      })
      setActionMessage("已生成实验记录")
    } catch (err) {
      setAnalysisError(err instanceof Error ? err.message : "实验记录创建失败")
    }
  }

  async function saveGlossary(terms: TranslationAnalysisResult["glossary"]) {
    setActionMessage(null)
    setAnalysisError(null)
    try {
      const response = await apiFetch<{ count: number }>("/glossary/batch", {
        method: "POST",
        body: JSON.stringify({ terms: terms.map((term) => ({ enTerm: term.en, zhTerm: term.zh, note: term.note })) }),
      })
      setActionMessage(`已保存 ${response.count} 条术语`)
    } catch (err) {
      setAnalysisError(err instanceof Error ? err.message : "术语保存失败")
    }
  }

  async function openComparison() {
    if (comparisonDocuments.length < 2) return
    setComparisonOpen(true)
    setComparisonRecord(null)
    setComparisonError(null)
    const params = new URLSearchParams()
    for (const document of comparisonDocuments) params.append("documentId", String(document.id))
    try {
      const latest = await apiFetch<{ data: DocumentAnalysisRecord<ComparisonAnalysisResult> | null }>(`/document-comparisons/latest?${params}`)
      if (latest.data) {
        setComparisonRecord(latest.data)
      } else {
        await runComparison(comparisonDocuments.map((document) => document.id))
      }
    } catch (err) {
      setComparisonError(err instanceof Error ? err.message : "对比结果加载失败")
    }
  }

  async function runComparison(documentIds = comparisonDocuments.map((document) => document.id)) {
    if (documentIds.length < 2) return
    comparisonPollRef.current?.abort()
    const controller = new AbortController()
    comparisonPollRef.current = controller
    setComparisonError(null)
    try {
      const queued = await createJob<DocumentAnalysisRecord<ComparisonAnalysisResult>>(
        "document_compare",
        { documentIds: [...documentIds].sort((a, b) => a - b) },
      )
      setComparisonJob(queued)
      const completed = await waitForJob(queued, { signal: controller.signal, onUpdate: setComparisonJob })
      if (completed.status === "SUCCEEDED" && completed.result) {
        setComparisonRecord(completed.result)
        setComparisonJob(null)
      } else if (completed.status !== "CANCELLED") {
        setComparisonError(completed.error?.message || "文献对比失败")
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return
      setComparisonError(err instanceof Error ? err.message : "无法创建对比任务")
    }
  }

  async function stopComparison() {
    if (!comparisonJob) return
    try {
      const cancelled = await cancelJob<DocumentAnalysisRecord<ComparisonAnalysisResult>>(comparisonJob.id)
      setComparisonJob(cancelled)
      comparisonPollRef.current?.abort()
    } catch (err) {
      setComparisonError(err instanceof Error ? err.message : "无法取消对比任务")
    }
  }

  const importRunning = uploadJob && (uploadJob.status === "QUEUED" || uploadJob.status === "RUNNING")

  return (
    <div className="flex h-full flex-col gap-3">
      <Toolbar>
        <Input icon={Search} placeholder="搜索标题 / 摘要" value={query} onChange={(event) => setQuery(event.target.value)} className="w-56" />
        <input
          ref={fileInputRef}
          type="file"
          accept="application/pdf,.pdf"
          className="hidden"
          aria-label="选择 PDF 文件"
          onChange={(event) => {
            const file = event.target.files?.[0]
            if (file) void importPdf(file)
          }}
        />
        <input
          ref={reimportInputRef}
          type="file"
          accept="application/pdf,.pdf"
          className="hidden"
          aria-label="选择重新导入的 PDF"
          onChange={(event) => {
            const file = event.target.files?.[0]
            if (file) void reimportPdf(file)
          }}
        />
        <Btn variant="primary" icon={FileUp} disabled={Boolean(importRunning)} onClick={() => fileInputRef.current?.click()}>导入 PDF</Btn>
        <Btn icon={RefreshCw} disabled={!selected || Boolean(importRunning)} onClick={() => reimportInputRef.current?.click()}>重新导入</Btn>
        <Btn icon={RefreshCw} onClick={load} disabled={loading}>刷新</Btn>
        <Btn icon={MessageSquareText} disabled={!selected} onClick={() => selected && onAskDocument?.(selected.id)}>文献问答</Btn>
        <Btn icon={Eye} disabled={!selected || selected.sourceType !== "pdf"} onClick={openPdf}>阅读 PDF</Btn>
        <Btn icon={GitCompareArrows} disabled={comparisonDocuments.length < 2} onClick={() => void openComparison()}>对比分析</Btn>
        <Btn icon={Download} disabled={documents.length === 0} onClick={exportDocuments}>导出列表</Btn>
        <Btn variant="danger" icon={Trash2} disabled={!selected} onClick={deleteSelected}>删除</Btn>
        <span className="ml-auto text-xs text-muted-foreground">共 {documents.length} 篇 · 已选 {comparisonDocuments.length} 篇对比</span>
      </Toolbar>

      {uploadJob && (
        <div className="flex items-center gap-3 rounded-lg border border-border bg-card px-3 py-2 text-xs">
          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-secondary"><div className="h-full bg-primary transition-all" style={{ width: `${uploadJob.progress}%` }} /></div>
          <span className="min-w-12 text-right tabular-nums text-foreground">{uploadJob.progress}%</span>
          <span className="max-w-64 truncate text-muted-foreground">{uploadJob.message || "等待处理"}</span>
          {importRunning && <Btn size="xs" variant="ghost" icon={X} onClick={stopImport}>取消</Btn>}
        </div>
      )}

      {error && <div className="rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-xs text-danger">{error}</div>}

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-[340px_1fr]">
        <Panel title="文献列表" icon={FileText} noPadding className="min-h-64 lg:min-h-0" bodyClassName="overflow-y-auto">
          {loading ? <LoadingState /> : error && documents.length === 0 ? <ErrorState message={error} onRetry={load} /> : documents.length === 0 ? <NoDataState title="暂无文献" /> : (
            <ul className="divide-y divide-border">
              {documents.map((document) => (
                <li key={document.id} className={cn("flex items-start gap-2 px-3 py-2.5 transition-colors", selected?.id === document.id ? "bg-primary-soft" : "hover:bg-secondary")}>
                  <input
                    type="checkbox"
                    checked={compareIds.has(document.id)}
                    onChange={() => toggleCompare(document.id)}
                    aria-label={`选择 ${document.title} 用于对比`}
                    className="mt-1 size-4 accent-primary"
                  />
                  <button onClick={() => setSelectedId(document.id)} className="min-w-0 flex-1 text-left">
                    <p className={cn("line-clamp-2 text-[13px] font-medium leading-snug", selected?.id === document.id ? "text-primary" : "text-foreground")}>{document.title}</p>
                    <div className="mt-1 flex items-center gap-1.5 text-[11px] text-muted-foreground">
                      <Tag tone="blue">{document.sourceType}</Tag>
                      <span>{new Date(document.createdAt).toLocaleDateString()}</span>
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        {!selected ? <Panel title="文献详情" icon={FileText}><NoDataState title="请选择文献" /></Panel> : (
          <LiteratureAnalysisView
            key={selected.id}
            document={selected}
            analyses={analyses}
            loading={analysisLoading}
            error={analysisError}
            job={analysisJob}
            actionMessage={actionMessage}
            onRun={(type, options) => void runAnalysis(type, options)}
            onCancel={() => void stopAnalysis()}
            onCreateLabRecord={(reaction) => void createLabRecord(reaction)}
            onSaveGlossary={(terms) => void saveGlossary(terms)}
          />
        )}
      </div>

      <ComparisonDialog
        open={comparisonOpen}
        documents={comparisonDocuments}
        record={comparisonRecord}
        job={comparisonJob}
        error={comparisonError}
        onClose={() => setComparisonOpen(false)}
        onRun={() => void runComparison()}
        onCancel={() => void stopComparison()}
      />
    </div>
  )
}
