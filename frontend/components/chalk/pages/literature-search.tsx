"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Database, Download, ExternalLink, Lightbulb, Play, PlusCircle, Square, Terminal } from "lucide-react"
import { apiFetch } from "@/lib/api/client"
import { cancelJob, createJob, getLatestActiveJob, getLatestCompletedJob, waitForJob } from "@/lib/api/jobs"
import type { Job, LiteratureSearchJobResult, LiteratureSearchResult } from "@/lib/api/types"
import type { DocumentPageExcerpt } from "@/lib/api/documents"
import type { Science125QuestionProfile } from "@/lib/api/science125"
import { ErrorState, NoDataState } from "../api-state"
import { Btn, Checkbox, FieldLabel, Panel, Select, Stepper, Tag, Textarea } from "../ui"
import { DocumentPageEvidence, formatDocumentPageContext } from "./document-page-evidence"

const SEARCH_PLATFORMS = ["Crossref", "arXiv", "Semantic Scholar", "DOAJ", "PMC"] as const
const MAX_REVIEW_HANDOFF_CHARS = 36_000

export type LiteratureReviewState = {
  jobId: string | null
  selectedResultIds: string[]
  literatureContext: string
  pageExcerpts: DocumentPageExcerpt[]
  confirmed: boolean
  restorationComplete: boolean
}

type StoredReviewSelection = {
  jobId: string
  selectedIds: string[]
  confirmed: boolean
}

export function SearchPage({
  onUseAsHypothesis,
  onSelectionChange,
  onReviewStateChange,
  initialQuery,
  science125Id,
  science125Profile,
  selectionStorageKey,
}: {
  onUseAsHypothesis?: (context: string, review: LiteratureReviewState) => void
  onSelectionChange?: (context: string) => void
  onReviewStateChange?: (review: LiteratureReviewState) => void
  initialQuery?: string
  science125Id?: string
  science125Profile?: Science125QuestionProfile | null
  selectionStorageKey?: string
}) {
  const [queryText, setQueryText] = useState("")
  const [domain, setDomain] = useState("")
  const [platforms, setPlatforms] = useState<Set<string>>(new Set(SEARCH_PLATFORMS))
  const [perPlatform, setPerPlatform] = useState(20)
  const [yearFrom, setYearFrom] = useState("")
  const [yearTo, setYearTo] = useState("")
  const [results, setResults] = useState<LiteratureSearchResult[]>([])
  const [diagnostics, setDiagnostics] = useState<LiteratureSearchJobResult | null>(null)
  const [job, setJob] = useState<Job<LiteratureSearchJobResult> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [actionMessage, setActionMessage] = useState<string | null>(null)
  const [storedSelection, setStoredSelection] = useState<StoredReviewSelection | null>(null)
  const [selectionRestoredFor, setSelectionRestoredFor] = useState<string | null>(null)
  const [selectionAppliedFor, setSelectionAppliedFor] = useState<string | null>(null)
  const [confirmedJobId, setConfirmedJobId] = useState<string | null>(null)
  const [pageReviewConfirmed, setPageReviewConfirmed] = useState(false)
  const [restorationComplete, setRestorationComplete] = useState(false)
  const [pageExcerpts, setPageExcerpts] = useState<DocumentPageExcerpt[]>([])
  const [pageEvidenceRestored, setPageEvidenceRestored] = useState(!science125Id)
  const controllerRef = useRef<AbortController | null>(null)
  const science125Ready = !science125Id || science125Profile?.ready === true

  useEffect(() => {
    if (!science125Id) setPageEvidenceRestored(true)
  }, [science125Id])

  useEffect(() => {
    if (initialQuery?.trim()) setQueryText(initialQuery)
  }, [initialQuery])

  const applyCompletedSearch = useCallback((completed: Job<LiteratureSearchJobResult>, preserveSelection: boolean) => {
    if (completed.status !== "SUCCEEDED" || !completed.result) return
    setJob(completed)
    setDiagnostics(completed.result)
    setResults(completed.result.results)
    setSelectionAppliedFor(null)
    if (!preserveSelection) {
      setSelectedIds(new Set())
      setConfirmedJobId(null)
      setPageReviewConfirmed(false)
    }
  }, [])

  const followSearchJob = useCallback(async (queued: Job<LiteratureSearchJobResult>, controller: AbortController) => {
    const completed = await waitForJob(queued, { signal: controller.signal, onUpdate: setJob })
    if (completed.status === "SUCCEEDED" && completed.result) {
      applyCompletedSearch(completed, false)
    } else if (completed.status !== "CANCELLED") {
      setError(completed.error?.message || "文献检索失败")
    }
  }, [applyCompletedSearch])

  useEffect(() => {
    let active = true
    setJob(null)
    setResults([])
    setDiagnostics(null)
    setSelectedIds(new Set())
    setConfirmedJobId(null)
    setPageReviewConfirmed(false)
    setRestorationComplete(false)
    void (async () => {
      try {
        const activeJob = await getLatestActiveJob<LiteratureSearchJobResult>("literature_search", { science125Id })
        if (!active) return
        if (activeJob) {
          setRestorationComplete(true)
          const controller = new AbortController()
          controllerRef.current = controller
          await followSearchJob(activeJob, controller)
          return
        }
        if (!science125Id) return
        const completedJob = await getLatestCompletedJob<LiteratureSearchJobResult>("literature_search", { science125Id })
        if (active && completedJob) applyCompletedSearch(completedJob, true)
      } catch (err) {
        if (active) setError(err instanceof Error ? err.message : "无法恢复检索任务")
      } finally {
        if (active) setRestorationComplete(true)
      }
    })()
    return () => {
      active = false
      controllerRef.current?.abort()
    }
  }, [applyCompletedSearch, followSearchJob, science125Id])

  useEffect(() => {
    setSelectionRestoredFor(null)
    setSelectionAppliedFor(null)
    setStoredSelection(null)
    setConfirmedJobId(null)
    setPageReviewConfirmed(false)
    if (!selectionStorageKey || typeof window === "undefined") return
    try {
      const parsed: unknown = JSON.parse(window.sessionStorage.getItem(selectionStorageKey) || "[]")
      if (
        typeof parsed === "object" && parsed !== null
        && typeof (parsed as StoredReviewSelection).jobId === "string"
        && Array.isArray((parsed as StoredReviewSelection).selectedIds)
        && typeof (parsed as StoredReviewSelection).confirmed === "boolean"
      ) {
        setStoredSelection({
          jobId: (parsed as StoredReviewSelection).jobId,
          selectedIds: (parsed as StoredReviewSelection).selectedIds.filter((value): value is string => typeof value === "string").slice(0, 1000),
          confirmed: (parsed as StoredReviewSelection).confirmed,
        })
      }
    } catch {
      // Ignore malformed client-side cache data and let the researcher review again.
    }
    setSelectionRestoredFor(selectionStorageKey)
  }, [selectionStorageKey])

  useEffect(() => {
    if (!selectionStorageKey || selectionRestoredFor !== selectionStorageKey || job?.status !== "SUCCEEDED") return
    const resultIds = new Set(results.map((result) => result.id))
    const isMatchingJob = storedSelection?.jobId === job.id
    setSelectedIds(new Set((isMatchingJob ? storedSelection.selectedIds : []).filter((resultId) => resultIds.has(resultId))))
    setConfirmedJobId(isMatchingJob && storedSelection.confirmed ? job.id : null)
    setSelectionAppliedFor(selectionStorageKey)
  }, [job, results, selectionRestoredFor, selectionStorageKey, storedSelection])

  useEffect(() => {
    if (!selectionStorageKey || selectionAppliedFor !== selectionStorageKey || job?.status !== "SUCCEEDED" || typeof window === "undefined") return
    window.sessionStorage.setItem(selectionStorageKey, JSON.stringify({
      jobId: job.id,
      selectedIds: [...selectedIds],
      confirmed: confirmedJobId === job.id,
    } satisfies StoredReviewSelection))
  }, [confirmedJobId, job, results.length, selectedIds, selectionAppliedFor, selectionStorageKey])

  function togglePlatform(platform: string) {
    setPlatforms((current) => {
      const next = new Set(current)
      if (next.has(platform)) next.delete(platform)
      else next.add(platform)
      return next
    })
  }

  async function startSearch() {
    const query = queryText.trim()
    if (!query || platforms.size === 0) return
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    setError(null)
    setResults([])
    setDiagnostics(null)
    setSelectedIds(new Set())
    setStoredSelection(null)
    setConfirmedJobId(null)
    setPageReviewConfirmed(false)
    setSelectionAppliedFor(null)
    if (selectionStorageKey && typeof window !== "undefined") window.sessionStorage.removeItem(selectionStorageKey)
    setActionMessage(null)
    try {
      const queued = await createJob<LiteratureSearchJobResult>("literature_search", science125Id ? {
        queryText: query,
        science125Id,
      } : {
        queryText: query,
        domain,
        platforms: Array.from(platforms),
        maxResults: perPlatform,
        yearFrom,
        yearTo,
      })
      await followSearchJob(queued, controller)
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return
      setError(err instanceof Error ? err.message : "文献检索失败")
    }
  }

  async function stopSearch() {
    if (!job) return
    try {
      setJob(await cancelJob<LiteratureSearchJobResult>(job.id))
      controllerRef.current?.abort()
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法取消检索任务")
    }
  }

  const running = job && ["QUEUED", "RUNNING"].includes(job.status)
  const selectedResults = useMemo(() => results.filter((result) => selectedIds.has(result.id)), [results, selectedIds])
  const literatureContext = useMemo(() => formatHypothesisContext(selectedResults), [selectedResults])
  const selectedContext = useMemo(() => {
    const combined = [
      literatureContext,
      formatDocumentPageContext(pageExcerpts),
    ].filter(Boolean).join("\n\n")
    return combined.length > MAX_REVIEW_HANDOFF_CHARS
      ? `${combined.slice(0, MAX_REVIEW_HANDOFF_CHARS)}\n[审核输入预览已截断；页摘录的结构化页码与哈希仍保留。]`
      : combined
  }, [literatureContext, pageExcerpts])
  const selectedEvidenceCount = selectedResults.length + pageExcerpts.length
  const reviewedFullTextResults = selectedResults.filter((result) => !["", "metadata", "metadata_only", "needs_verification"].includes((result.accessStatus || "").toLowerCase()))
  const reviewedProviderFamilies = new Set([
    ...reviewedFullTextResults.map((result) => result.providerFamily || result.sourcePlatform),
    ...(pageExcerpts.length > 0 ? ["user_pdf"] : []),
  ].filter(Boolean))
  const science125EvidenceReady = !science125Id || (
    reviewedFullTextResults.length + pageExcerpts.length >= 3
    && reviewedProviderFamilies.size >= 2
  )
  const canConfirmReview = selectedEvidenceCount > 0 && (
    job?.status === "SUCCEEDED" || pageExcerpts.length > 0
  ) && science125EvidenceReady
  const reviewState = useMemo<LiteratureReviewState>(() => ({
    jobId: job?.status === "SUCCEEDED" ? job.id : null,
    selectedResultIds: selectedResults.map((result) => result.id),
    literatureContext,
    pageExcerpts,
    confirmed: (job?.status === "SUCCEEDED" && confirmedJobId === job.id) || pageReviewConfirmed,
    restorationComplete: restorationComplete && (
      !selectionStorageKey
      || job?.status !== "SUCCEEDED"
      || selectionAppliedFor === selectionStorageKey
    ) && pageEvidenceRestored,
  }), [confirmedJobId, job, literatureContext, pageEvidenceRestored, pageExcerpts, pageReviewConfirmed, restorationComplete, selectedResults, selectionAppliedFor, selectionStorageKey])

  const updatePageExcerpts = useCallback((next: DocumentPageExcerpt[]) => {
    setPageExcerpts(next)
    setConfirmedJobId(null)
    setPageReviewConfirmed(false)
  }, [])

  const updatePageEvidenceRestored = useCallback((complete: boolean) => {
    setPageEvidenceRestored(complete)
  }, [])

  useEffect(() => {
    onSelectionChange?.(selectedContext)
  }, [onSelectionChange, selectedContext])

  useEffect(() => {
    onReviewStateChange?.(reviewState)
  }, [onReviewStateChange, reviewState])

  function toggleResult(resultId: string) {
    setSelectedIds((current) => {
      const next = new Set(current)
      if (next.has(resultId)) next.delete(resultId)
      else next.add(resultId)
      setConfirmedJobId(null)
      setPageReviewConfirmed(false)
      return next
    })
  }

  async function addToEvidence() {
    if (selectedResults.length === 0) return
    setError(null)
    setActionMessage(null)
    try {
      const references = selectedResults.map(({ id, title, authors, journal, year, doi, abstract, sourcePlatform, url, isOpenAccess, accessStatus, warning }) => ({
        id, title, authors, journal, year, doi, abstract, sourcePlatform, url, isOpenAccess, accessStatus, warning,
      }))
      const response = await apiFetch<{ count: number }>("/evidence/literature", {
        method: "POST",
        body: JSON.stringify({ domain, references }),
      })
      setActionMessage(`已写入 ${response.count} 条文献证据`)
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "加入证据库失败")
    }
  }

  function useAsHypothesis() {
    if (!canConfirmReview) return
    const confirmedReview = { ...reviewState, confirmed: true }
    setConfirmedJobId(reviewState.jobId)
    setPageReviewConfirmed(true)
    onReviewStateChange?.(confirmedReview)
    onUseAsHypothesis?.(selectedContext, confirmedReview)
  }

  function exportReferences() {
    if (results.length === 0) return
    const lines = ["# Literature References", "", ...results.map((result, index) => (
      `${index + 1}. ${result.authors}. **${result.title}**. ${result.journal} (${result.year}). DOI: ${result.doi}. [${result.sourcePlatform}]`
    ))]
    downloadText("literature-references.md", lines.join("\n"), "text/markdown;charset=utf-8")
  }

  return (
    <div className="flex h-full flex-col gap-3">
      <Panel title="检索条件" icon={Database}>
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-[1fr_220px]">
          <div>
            <FieldLabel>{science125Id ? "文献检索式 / DOI / 关键词" : "检索式 / DOI / 研究问题"}</FieldLabel>
            <Textarea data-testid={science125Id ? "science125-literature-query" : undefined} rows={2} value={queryText} onChange={(event) => setQueryText(event.target.value)} />
          </div>
          {!science125Id && <div className="grid grid-cols-2 gap-2 lg:grid-cols-1">
            {!science125Id && <div>
              <FieldLabel>研究领域</FieldLabel>
              <Select className="w-full" value={domain} onChange={(event) => setDomain(event.target.value)}>
                <option value="">不限定</option>
                <option value="catalysis">催化</option>
                <option value="materials">材料</option>
                <option value="energy">能源</option>
                <option value="chemistry">化学</option>
              </Select>
            </div>}
            <div>
              <FieldLabel>每平台数量</FieldLabel>
              <Stepper value={perPlatform} onChange={setPerPlatform} min={1} max={100} />
            </div>
          </div>}
        </div>

        <div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-[1fr_auto] lg:items-end">
           <div>
             <FieldLabel>{science125Id ? "服务器自动选择的领域源" : "检索平台"}</FieldLabel>
             {science125Id ? (
               <div data-testid="science125-provider-routing" className="flex flex-wrap items-center gap-2">
                 {(science125Profile?.providers || []).map((provider) => <span key={provider.providerId} className={`border px-2 py-1 text-xs ${provider.isRequired ? "border-primary/40 text-primary" : "border-border text-muted-foreground"}`}>{provider.displayName}{provider.isRequired ? " · 必需" : ""}</span>)}
                 {!science125Profile && <span className="text-xs text-muted-foreground">正在读取检索配置...</span>}
               </div>
             ) : <div className="flex flex-wrap items-center gap-4">
               {SEARCH_PLATFORMS.map((platform) => (
                 <Checkbox key={platform} checked={platforms.has(platform)} onChange={() => togglePlatform(platform)} label={platform} />
               ))}
             </div>}
           </div>
          <div className="flex flex-wrap items-end gap-2">
            {!science125Id && <>
            <label className="text-xs text-muted-foreground">起始年份<input aria-label="起始年份" value={yearFrom} onChange={(event) => setYearFrom(event.target.value.replace(/\D/g, "").slice(0, 4))} className="ml-1 h-8 w-20 rounded-md border border-input bg-card px-2 text-foreground" /></label>
            <label className="text-xs text-muted-foreground">结束年份<input aria-label="结束年份" value={yearTo} onChange={(event) => setYearTo(event.target.value.replace(/\D/g, "").slice(0, 4))} className="ml-1 h-8 w-20 rounded-md border border-input bg-card px-2 text-foreground" /></label>
            </>}
            {running ? (
              <Btn variant="danger" icon={Square} onClick={stopSearch}>取消</Btn>
            ) : (
               <Btn data-testid="start-literature-presearch" variant="primary" icon={Play} disabled={!queryText.trim() || (!science125Id && platforms.size === 0) || !science125Ready} onClick={startSearch}>开始检索</Btn>
            )}
          </div>
        </div>

        {job && (
          <div className="mt-3 flex items-center gap-3 text-xs text-muted-foreground">
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-secondary">
              <div className="h-full bg-primary transition-all" style={{ width: `${job.progress}%` }} />
            </div>
            <span className="tabular-nums">{job.progress}%</span>
            <span className="max-w-64 truncate">{job.message}</span>
          </div>
        )}
        {science125Id && science125Profile && science125Profile.providerReadiness.some((item) => !item.ready) && <p data-testid="science125-provider-readiness" className="mt-2 text-xs text-warning">必需检索源尚未就绪：{science125Profile.providerReadiness.filter((item) => !item.ready).flatMap((item) => item.missingConfigurationCodes).join("、") || "等待 provider cooldown 或服务恢复"}</p>}
        {science125Id && job?.status === "SUCCEEDED" && diagnostics?.evidenceStatus === "evidence_insufficient" && <p data-testid="science125-evidence-insufficient" className="mt-2 text-xs text-warning">检索结果尚不足以作为生成证据：需审核至少 3 条全文证据，并覆盖至少两个来源家族。</p>}
      </Panel>

      {science125Id && <DocumentPageEvidence
        excerpts={pageExcerpts}
        storageKey={selectionStorageKey ? `${selectionStorageKey}:pdf-pages` : undefined}
        onChange={updatePageExcerpts}
        onRestorationChange={updatePageEvidenceRestored}
      />}

      {error && <ErrorState message={error} onRetry={startSearch} />}
      {(results.length > 0 || pageExcerpts.length > 0 || actionMessage) && (
        <div className="flex flex-wrap items-center gap-2 border border-border bg-card px-3 py-2">
          <span className="text-xs text-muted-foreground">已审核 {selectedResults.length} 条检索结果 · {pageExcerpts.length} 组 PDF 页</span>
          <Btn data-testid="use-literature-for-hypothesis" size="xs" icon={Lightbulb} disabled={!canConfirmReview} onClick={useAsHypothesis}>加入假设输入</Btn>
          <Btn size="xs" icon={PlusCircle} disabled={selectedResults.length === 0} onClick={() => void addToEvidence()}>加入证据库</Btn>
          <Btn size="xs" icon={Download} disabled={results.length === 0} onClick={exportReferences}>导出 References</Btn>
          {actionMessage && <span className="ml-auto text-xs text-success">{actionMessage}</span>}
          {science125Id && !science125EvidenceReady && <span className="text-xs text-warning">全文证据或来源家族不足，暂不能生成。</span>}
        </div>
      )}

      <Panel title="检索结果" noPadding className="min-h-0" bodyClassName="overflow-auto">
        {!job && !diagnostics ? (
          <NoDataState title="尚未运行检索" />
        ) : running ? (
          <div className="flex min-h-40 items-center justify-center text-sm text-muted-foreground">正在检索文献...</div>
        ) : results.length === 0 ? (
          <NoDataState title="未找到结果" />
        ) : (
          <table className="w-full min-w-[980px] text-[13px]">
            <thead className="sticky top-0 z-10 bg-secondary text-xs text-muted-foreground">
              <tr>
                <th className="w-10 px-2 py-2"><span className="sr-only">选择</span></th>
                <th className="px-2 py-2 text-left font-medium">标题</th>
                <th className="px-2 py-2 text-left font-medium">作者</th>
                <th className="px-2 py-2 text-left font-medium">期刊</th>
                <th className="px-2 py-2 text-left font-medium">年份</th>
                <th className="px-2 py-2 text-left font-medium">DOI</th>
                <th className="px-2 py-2 text-left font-medium">平台</th>
                <th className="px-2 py-2 text-left font-medium">访问状态</th>
                <th className="px-2 py-2 text-left font-medium">相关度</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {results.map((result) => <SearchResultRow key={result.id} result={result} checked={selectedIds.has(result.id)} onToggle={() => toggleResult(result.id)} />)}
            </tbody>
          </table>
        )}
      </Panel>

      <Panel title="检索诊断" icon={Terminal} className="shrink-0">
        {!diagnostics ? (
          <NoDataState title="暂无诊断信息" className="py-4" />
        ) : (
          <div className="max-h-40 space-y-2 overflow-y-auto text-xs">
            {Object.entries(diagnostics.platformStatus).map(([platform, status]) => (
              <div key={platform} className="flex flex-wrap items-center gap-2 rounded-md border border-border px-2 py-1.5">
                <Tag tone="gray">{platform}</Tag>
                <span className="break-all text-muted-foreground">{formatDiagnostic(status)}</span>
              </div>
            ))}
            {diagnostics.warnings.map((warning, index) => (
              <p key={`${warning}-${index}`} className="rounded-md border border-warning/40 bg-warning-soft px-2 py-1.5 text-[#ad6800]">{warning}</p>
            ))}
          </div>
        )}
      </Panel>
    </div>
  )
}

function formatHypothesisContext(results: LiteratureSearchResult[]) {
  if (results.length === 0) return ""
  const lines = ["【开放文献检索输入】", ...results.flatMap((result, index) => {
    const metadata = `[D${index + 1}] ${result.title} | ${result.authors} | ${result.journal} (${result.year}) | DOI=${result.doi} | ${result.sourcePlatform}`
    return result.abstract ? [metadata, `摘要: ${result.abstract}`] : [metadata]
  })]
  return lines.join("\n")
}

function SearchResultRow({ result, checked, onToggle }: { result: LiteratureSearchResult; checked: boolean; onToggle: () => void }) {
  const url = safeExternalUrl(result.url || (result.doi ? `https://doi.org/${result.doi}` : ""))
  const relevance = Math.max(0, Math.min(100, Math.round(result.relevanceScore * 100)))
  return (
    <tr className="transition-colors hover:bg-secondary">
      <td className="px-2 py-2"><input type="checkbox" checked={checked} onChange={onToggle} aria-label={`选择检索结果 ${result.title}`} className="size-4 accent-primary" /></td>
      <td className="max-w-sm px-2 py-2"><span className="line-clamp-2 font-medium text-foreground">{result.title || "未命名文献"}</span></td>
      <td className="max-w-52 px-2 py-2 text-muted-foreground">{result.authors || "-"}</td>
      <td className="px-2 py-2 text-muted-foreground">{result.journal || "-"}</td>
      <td className="px-2 py-2 tabular-nums text-muted-foreground">{result.year || "-"}</td>
      <td className="px-2 py-2">
        {url ? <a href={url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-mono text-xs text-primary hover:underline">{result.doi || "打开来源"}<ExternalLink className="size-3" /></a> : "-"}
      </td>
      <td className="px-2 py-2"><Tag tone="gray">{result.sourcePlatform || "未知"}</Tag></td>
      <td className="px-2 py-2"><Tag tone={result.isOpenAccess ? "green" : "orange"}>{result.accessStatus || (result.isOpenAccess ? "open_access" : "needs_verification")}</Tag></td>
      <td className="px-2 py-2"><span className="tabular-nums text-xs font-semibold">{relevance}%</span></td>
    </tr>
  )
}

function downloadText(fileName: string, content: string, mimeType: string) {
  const url = URL.createObjectURL(new Blob([content], { type: mimeType }))
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = fileName
  anchor.click()
  URL.revokeObjectURL(url)
}

function safeExternalUrl(value: string) {
  if (!value) return null
  try {
    const url = new URL(value)
    return url.protocol === "http:" || url.protocol === "https:" ? url.toString() : null
  } catch {
    return null
  }
}

function formatDiagnostic(value: Record<string, unknown>) {
  return Object.entries(value).map(([key, item]) => `${key}: ${String(item)}`).join(" · ") || "无详细信息"
}
