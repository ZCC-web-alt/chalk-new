"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { Database, Download, ExternalLink, Lightbulb, Play, PlusCircle, Square, Terminal } from "lucide-react"
import { apiFetch } from "@/lib/api/client"
import { cancelJob, createJob, getLatestActiveJob, waitForJob } from "@/lib/api/jobs"
import type { Job, LiteratureSearchJobResult, LiteratureSearchResult } from "@/lib/api/types"
import { ErrorState, NoDataState } from "../api-state"
import { Btn, Checkbox, FieldLabel, Panel, Select, Stepper, Tag, Textarea } from "../ui"

const SEARCH_PLATFORMS = ["Crossref", "arXiv", "Semantic Scholar", "DOAJ", "PMC"] as const

export function SearchPage({ onUseAsHypothesis }: { onUseAsHypothesis?: (context: string) => void }) {
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
  const controllerRef = useRef<AbortController | null>(null)

  const followSearchJob = useCallback(async (queued: Job<LiteratureSearchJobResult>, controller: AbortController) => {
    const completed = await waitForJob(queued, { signal: controller.signal, onUpdate: setJob })
    if (completed.status === "SUCCEEDED" && completed.result) {
      setDiagnostics(completed.result)
      setResults(completed.result.results)
      setSelectedIds(new Set())
    } else if (completed.status !== "CANCELLED") {
      setError(completed.error?.message || "文献检索失败")
    }
  }, [])

  useEffect(() => {
    void getLatestActiveJob<LiteratureSearchJobResult>("literature_search").then((activeJob) => {
      if (!activeJob) return
      const controller = new AbortController()
      controllerRef.current = controller
      return followSearchJob(activeJob, controller)
    }).catch((err) => setError(err instanceof Error ? err.message : "无法恢复检索任务"))
    return () => controllerRef.current?.abort()
  }, [followSearchJob])

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
    setActionMessage(null)
    try {
      const queued = await createJob<LiteratureSearchJobResult>("literature_search", {
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
  const selectedResults = results.filter((result) => selectedIds.has(result.id))

  function toggleResult(resultId: string) {
    setSelectedIds((current) => {
      const next = new Set(current)
      if (next.has(resultId)) next.delete(resultId)
      else next.add(resultId)
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
    if (selectedResults.length === 0) return
    const lines = ["【开放文献检索输入】", ...selectedResults.flatMap((result, index) => {
      const metadata = `[D${index + 1}] ${result.title} | ${result.authors} | ${result.journal} (${result.year}) | DOI=${result.doi} | ${result.sourcePlatform}`
      return result.abstract ? [metadata, `摘要: ${result.abstract}`] : [metadata]
    })]
    onUseAsHypothesis?.(lines.join("\n"))
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
            <FieldLabel>检索式 / DOI / 研究问题</FieldLabel>
            <Textarea rows={2} value={queryText} onChange={(event) => setQueryText(event.target.value)} />
          </div>
          <div className="grid grid-cols-2 gap-2 lg:grid-cols-1">
            <div>
              <FieldLabel>研究领域</FieldLabel>
              <Select className="w-full" value={domain} onChange={(event) => setDomain(event.target.value)}>
                <option value="">不限定</option>
                <option value="catalysis">催化</option>
                <option value="materials">材料</option>
                <option value="energy">能源</option>
                <option value="chemistry">化学</option>
              </Select>
            </div>
            <div>
              <FieldLabel>每平台数量</FieldLabel>
              <Stepper value={perPlatform} onChange={setPerPlatform} min={1} max={100} />
            </div>
          </div>
        </div>

        <div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-[1fr_auto] lg:items-end">
          <div>
            <FieldLabel>检索平台</FieldLabel>
            <div className="flex flex-wrap items-center gap-4">
              {SEARCH_PLATFORMS.map((platform) => (
                <Checkbox key={platform} checked={platforms.has(platform)} onChange={() => togglePlatform(platform)} label={platform} />
              ))}
            </div>
          </div>
          <div className="flex flex-wrap items-end gap-2">
            <label className="text-xs text-muted-foreground">起始年份<input aria-label="起始年份" value={yearFrom} onChange={(event) => setYearFrom(event.target.value.replace(/\D/g, "").slice(0, 4))} className="ml-1 h-8 w-20 rounded-md border border-input bg-card px-2 text-foreground" /></label>
            <label className="text-xs text-muted-foreground">结束年份<input aria-label="结束年份" value={yearTo} onChange={(event) => setYearTo(event.target.value.replace(/\D/g, "").slice(0, 4))} className="ml-1 h-8 w-20 rounded-md border border-input bg-card px-2 text-foreground" /></label>
            {running ? (
              <Btn variant="danger" icon={Square} onClick={stopSearch}>取消</Btn>
            ) : (
              <Btn variant="primary" icon={Play} disabled={!queryText.trim() || platforms.size === 0} onClick={startSearch}>开始检索</Btn>
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
      </Panel>

      {error && <ErrorState message={error} onRetry={startSearch} />}
      {(results.length > 0 || actionMessage) && (
        <div className="flex flex-wrap items-center gap-2 border border-border bg-card px-3 py-2">
          <span className="text-xs text-muted-foreground">已选择 {selectedResults.length} / {results.length}</span>
          <Btn size="xs" icon={Lightbulb} disabled={selectedResults.length === 0} onClick={useAsHypothesis}>加入假设输入</Btn>
          <Btn size="xs" icon={PlusCircle} disabled={selectedResults.length === 0} onClick={() => void addToEvidence()}>加入证据库</Btn>
          <Btn size="xs" icon={Download} disabled={results.length === 0} onClick={exportReferences}>导出 References</Btn>
          {actionMessage && <span className="ml-auto text-xs text-success">{actionMessage}</span>}
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
