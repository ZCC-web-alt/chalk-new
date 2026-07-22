"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Eye, FilePlus2, FileUp, RefreshCw, Trash2 } from "lucide-react"
import { apiFetch, apiUrl } from "@/lib/api/client"
import {
  extractDocumentPages,
  listDocumentsForEvidence,
  type DocumentPageExcerpt,
} from "@/lib/api/documents"
import { waitForJob } from "@/lib/api/jobs"
import type { DocumentRecord, Job } from "@/lib/api/types"
import { ErrorState, LoadingState, NoDataState } from "../api-state"
import { Btn, FieldLabel, Input, Panel, Select, Tag } from "../ui"

type ImportResult = { documentId: number; title: string; chunkCount: number }
type StoredPageSelection = {
  documentId: number
  pages: number[]
  pdfSha256: string
  textSha256: string
  maxChars: number
}

const MAX_SELECTED_PAGES = 20

export function parsePageSelection(value: string): number[] {
  const pages = new Set<number>()
  const tokens = value.split(",").map((token) => token.trim()).filter(Boolean)
  if (tokens.length === 0) throw new Error("请输入 PDF 页码，例如 2, 4-5。")
  for (const token of tokens) {
    const match = token.match(/^(\d+)(?:\s*-\s*(\d+))?$/)
    if (!match) throw new Error("页码格式无效，请使用 2, 4-5 这样的格式。")
    const start = Number(match[1])
    const end = Number(match[2] || match[1])
    if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end)) throw new Error("页码必须是有限的整数。")
    if (start < 1 || end < start) throw new Error("页码必须从 1 开始，且范围终点不能小于起点。")
    if (end - start + 1 > MAX_SELECTED_PAGES) throw new Error(`一次最多选择 ${MAX_SELECTED_PAGES} 页。`)
    for (let page = start; page <= end; page += 1) {
      pages.add(page)
      if (pages.size > MAX_SELECTED_PAGES) throw new Error(`一次最多选择 ${MAX_SELECTED_PAGES} 页。`)
    }
  }
  return [...pages].sort((left, right) => left - right)
}

function compressPages(pages: number[]) {
  if (pages.length === 0) return ""
  const ranges: string[] = []
  let start = pages[0]
  let previous = pages[0]
  for (const page of pages.slice(1)) {
    if (page === previous + 1) {
      previous = page
      continue
    }
    ranges.push(start === previous ? String(start) : `${start}-${previous}`)
    start = page
    previous = page
  }
  ranges.push(start === previous ? String(start) : `${start}-${previous}`)
  return ranges.join(", ")
}

function loadStoredSelections(storageKey: string | undefined): StoredPageSelection[] {
  if (!storageKey || typeof window === "undefined") return []
  try {
    const parsed: unknown = JSON.parse(window.sessionStorage.getItem(storageKey) || "[]")
    if (!Array.isArray(parsed)) return []
    return parsed.flatMap((item): StoredPageSelection[] => {
      if (typeof item !== "object" || item === null) return []
      const candidate = item as Partial<StoredPageSelection>
      if (!Number.isInteger(candidate.documentId) || !Array.isArray(candidate.pages)) return []
      const pages = candidate.pages.filter((page): page is number => Number.isInteger(page) && page > 0)
      if (pages.length === 0 || pages.length > MAX_SELECTED_PAGES) return []
      if (!/^[0-9a-f]{64}$/.test(candidate.pdfSha256 || "") || !/^[0-9a-f]{64}$/.test(candidate.textSha256 || "")) return []
      if (!Number.isInteger(candidate.maxChars) || (candidate.maxChars as number) < 1 || (candidate.maxChars as number) > 50_000) return []
      return [{
        documentId: candidate.documentId as number,
        pages: [...new Set(pages)].sort((a, b) => a - b),
        pdfSha256: candidate.pdfSha256 as string,
        textSha256: candidate.textSha256 as string,
        maxChars: candidate.maxChars as number,
      }]
    }).slice(0, 10)
  } catch {
    return []
  }
}

export function DocumentPageEvidence({
  excerpts,
  storageKey,
  onChange,
  onRestorationChange,
}: {
  excerpts: DocumentPageExcerpt[]
  storageKey?: string
  onChange: (excerpts: DocumentPageExcerpt[]) => void
  onRestorationChange: (complete: boolean) => void
}) {
  const [documents, setDocuments] = useState<DocumentRecord[]>([])
  const [documentId, setDocumentId] = useState("")
  const [pageText, setPageText] = useState("")
  const [loading, setLoading] = useState(true)
  const [extracting, setExtracting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [importJob, setImportJob] = useState<Job<ImportResult> | null>(null)
  const [restoredFor, setRestoredFor] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const importControllerRef = useRef<AbortController | null>(null)
  const restoreGenerationRef = useRef(0)

  const loadDocuments = useCallback(async (preferredDocumentId?: number) => {
    setLoading(true)
    setError(null)
    try {
      const response = await listDocumentsForEvidence()
      const pdfs = response.data.filter((document) => document.sourceType === "pdf")
      setDocuments(pdfs)
      setDocumentId((current) => {
        const preferred = preferredDocumentId ? String(preferredDocumentId) : current
        return pdfs.some((document) => String(document.id) === preferred) ? preferred : String(pdfs[0]?.id || "")
      })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取文献列表。")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadDocuments()
    return () => importControllerRef.current?.abort()
  }, [loadDocuments])

  useEffect(() => {
    const generation = restoreGenerationRef.current + 1
    restoreGenerationRef.current = generation
    setRestoredFor(null)
    setError(null)
    onRestorationChange(false)
    onChange([])
    const stored = loadStoredSelections(storageKey)
    if (stored.length === 0) {
      setRestoredFor(storageKey || "unscoped")
      onRestorationChange(true)
      return
    }
    void Promise.allSettled(stored.map(async (selection) => {
      const excerpt = await extractDocumentPages(selection.documentId, selection.pages, selection.maxChars)
      if (excerpt.provenance.pdfSha256 !== selection.pdfSha256 || excerpt.hash !== selection.textSha256) {
        throw new Error("SOURCE_PAGE_SNAPSHOT_CHANGED")
      }
      return excerpt
    }))
      .then((results) => {
        if (restoreGenerationRef.current !== generation) return
        const restored = results.flatMap((result) => result.status === "fulfilled" ? [result.value] : [])
        onChange(restored)
        if (restored.length !== stored.length) setError("部分 PDF 页内容已变化或不可用，请重新审核相应页。")
      })
      .finally(() => {
        if (restoreGenerationRef.current === generation) {
          setRestoredFor(storageKey || "unscoped")
          onRestorationChange(true)
        }
      })
  }, [onChange, onRestorationChange, storageKey])

  useEffect(() => {
    if (!storageKey || restoredFor !== storageKey || typeof window === "undefined") return
    const selections = excerpts.map((excerpt) => ({
      documentId: excerpt.documentId,
      pages: excerpt.pages,
      pdfSha256: excerpt.provenance.pdfSha256,
      textSha256: excerpt.hash,
      maxChars: excerpt.provenance.maxChars,
    }))
    window.sessionStorage.setItem(storageKey, JSON.stringify(selections))
  }, [excerpts, restoredFor, storageKey])

  const selectedDocument = useMemo(
    () => documents.find((document) => String(document.id) === documentId) || null,
    [documentId, documents],
  )

  async function addExcerpt() {
    if (!selectedDocument) return
    setError(null)
    let pages: number[]
    try {
      pages = parsePageSelection(pageText)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "页码格式无效。")
      return
    }
    setExtracting(true)
    try {
      const excerpt = await extractDocumentPages(selectedDocument.id, pages)
      onChange([...excerpts.filter((item) => item.documentId !== excerpt.documentId), excerpt])
      setPageText("")
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法提取所选文献页。")
    } finally {
      setExtracting(false)
    }
  }

  async function importPdf(file: File) {
    importControllerRef.current?.abort()
    const controller = new AbortController()
    importControllerRef.current = controller
    setError(null)
    const form = new FormData()
    form.set("file", file)
    try {
      const queued = await apiFetch<Job<ImportResult>>("/documents/import", { method: "POST", body: form })
      setImportJob(queued)
      const completed = await waitForJob(queued, { signal: controller.signal, onUpdate: setImportJob })
      if (completed.status !== "SUCCEEDED" || !completed.result) {
        throw new Error(completed.error?.message || "PDF 导入失败。")
      }
      await loadDocuments(completed.result.documentId)
      setImportJob(null)
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") return
      setError(reason instanceof Error ? reason.message : "PDF 导入失败。")
    } finally {
      setImportJob(null)
      if (fileInputRef.current) fileInputRef.current.value = ""
    }
  }

  return <Panel
    title="自备文献页"
    icon={FilePlus2}
    extra={<span className="text-xs tabular-nums text-muted-foreground">{excerpts.length} / 10</span>}
    bodyClassName="space-y-3"
  >
    <input
      ref={fileInputRef}
      type="file"
      accept="application/pdf,.pdf"
      className="hidden"
      aria-label="选择要导入的 PDF"
      onChange={(event) => {
        const file = event.target.files?.[0]
        if (file) void importPdf(file)
      }}
    />
    <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_180px_auto] lg:items-end">
      <div>
        <FieldLabel>已导入 PDF</FieldLabel>
        <Select
          data-testid="science125-document-select"
          className="w-full"
          value={documentId}
          disabled={loading || documents.length === 0}
          onChange={(event) => setDocumentId(event.target.value)}
        >
          {documents.length === 0 && <option value="">暂无可用 PDF</option>}
          {documents.map((document) => <option key={document.id} value={document.id}>{document.title}</option>)}
        </Select>
      </div>
      <div>
        <FieldLabel>PDF 页码</FieldLabel>
        <Input
          data-testid="science125-page-input"
          value={pageText}
          placeholder="例如 2, 4-5"
          disabled={!selectedDocument || extracting}
          onChange={(event) => setPageText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault()
              void addExcerpt()
            }
          }}
        />
      </div>
      <div className="flex flex-wrap gap-2">
        <Btn
          data-testid="science125-add-page-excerpt"
          variant="primary"
          icon={FilePlus2}
          disabled={!selectedDocument || !pageText.trim() || extracting || excerpts.length >= 10}
          onClick={() => void addExcerpt()}
        >{extracting ? "提取中" : "加入审核"}</Btn>
        <Btn icon={FileUp} disabled={Boolean(importJob)} onClick={() => fileInputRef.current?.click()}>导入 PDF</Btn>
        <Btn icon={RefreshCw} disabled={loading} onClick={() => void loadDocuments()}>刷新</Btn>
      </div>
    </div>
    <p className="text-xs leading-5 text-muted-foreground">页码按 PDF 阅读器显示的页码，从 1 开始。服务器只提取所选页，并记录 PDF 与文本哈希。</p>
    {importJob && <p role="status" className="text-xs text-muted-foreground">{importJob.message || "正在导入 PDF"} · {importJob.progress}%</p>}
    {error && <ErrorState message={error} />}
    {loading ? <LoadingState label="正在加载文献..." /> : excerpts.length === 0 ? (
      <NoDataState title="尚未加入自备文献页" hint="可从文献管理导入 PDF，或在此直接导入。" className="py-5" />
    ) : <div className="divide-y divide-border border-y border-border">
      {excerpts.map((excerpt) => <article data-testid={`science125-page-excerpt-${excerpt.documentId}`} className="space-y-2 py-3" key={`${excerpt.documentId}:${excerpt.hash}`}>
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="text-sm font-medium text-foreground">{excerpt.title}</p>
            <div className="mt-1 flex flex-wrap items-center gap-2"><Tag tone="blue">PDF 页 {compressPages(excerpt.pages)}</Tag><span className="text-[11px] text-muted-foreground">校验 {excerpt.hash.slice(0, 12)}</span></div>
          </div>
          <div className="flex gap-1">
            <Btn size="xs" variant="ghost" icon={Eye} onClick={() => window.open(`${apiUrl(`/documents/${excerpt.documentId}/content`)}#page=${excerpt.pages[0]}`, "_blank", "noopener,noreferrer")}>查看</Btn>
            <Btn size="xs" variant="ghost" icon={Trash2} onClick={() => onChange(excerpts.filter((item) => item.documentId !== excerpt.documentId))}>移除</Btn>
          </div>
        </div>
        <p className="max-h-28 overflow-y-auto whitespace-pre-wrap border-l-2 border-primary/30 pl-3 text-xs leading-5 text-muted-foreground">{excerpt.text}</p>
      </article>)}
    </div>}
  </Panel>
}

export function formatDocumentPageContext(excerpts: DocumentPageExcerpt[]) {
  if (excerpts.length === 0) return ""
  return [
    "【人工审核的自备 PDF 页摘录】",
    ...excerpts.flatMap((excerpt, index) => [
      `[P${index + 1}] ${excerpt.title} | documentId=${excerpt.documentId} | PDF pages=${compressPages(excerpt.pages)} | textHash=${excerpt.hash}`,
      excerpt.text,
    ]),
  ].join("\n")
}
