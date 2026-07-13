"use client"

import { useEffect, useRef, useState } from "react"
import {
  FileImage,
  FileSpreadsheet,
  History,
  Images,
  LoaderCircle,
  Play,
  RefreshCw,
  Trash2,
  Upload,
  X,
} from "lucide-react"
import { apiFetch, type Page } from "@/lib/api/client"
import { cancelJob, createJob, getLatestActiveJob, waitForJob } from "@/lib/api/jobs"
import {
  deleteMultimodalAsset,
  getMultimodalRun,
  listDocumentMultimodalSources,
  listMultimodalAssets,
  listMultimodalRuns,
  uploadMultimodalAssets,
} from "@/lib/api/science"
import type {
  DocumentMultimodalSource,
  DocumentRecord,
  Job,
  MultimodalAsset,
  MultimodalJobResult,
  MultimodalRun,
} from "@/lib/api/types"
import { cn } from "@/lib/utils"
import { NoDataState } from "../api-state"
import { Btn, Checkbox, FieldLabel, Panel, Select, Tag, Textarea, Toggle, Toolbar } from "../ui"
import { MultimodalResultWorkspace } from "./multimodal-result-workspace"


type SelectedSource =
  | { sourceType: "upload"; assetId: string; sheetNames: string[] }
  | { sourceType: "documentAsset"; documentId: number; assetId: string }

export function MultimodalPage() {
  const inputRef = useRef<HTMLInputElement>(null)
  const pollRef = useRef<AbortController | null>(null)
  const [assets, setAssets] = useState<MultimodalAsset[]>([])
  const [documents, setDocuments] = useState<DocumentRecord[]>([])
  const [documentId, setDocumentId] = useState(0)
  const [documentSources, setDocumentSources] = useState<DocumentMultimodalSource[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [sheetSelections, setSheetSelections] = useState<Record<string, string[]>>({})
  const [runs, setRuns] = useState<MultimodalRun[]>([])
  const [run, setRun] = useState<MultimodalRun | null>(null)
  const [job, setJob] = useState<Job<MultimodalJobResult> | null>(null)
  const [question, setQuestion] = useState("")
  const [useLiteratureContext, setUseLiteratureContext] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    const load = async () => {
      setLoading(true)
      try {
        const [assetPage, documentPage, runPage, activeJob] = await Promise.all([
          listMultimodalAssets(),
          apiFetch<Page<DocumentRecord>>("/documents?pageSize=100"),
          listMultimodalRuns(),
          getLatestActiveJob<MultimodalJobResult>("multimodal_analyze"),
        ])
        if (!active) return
        setAssets((current) => mergeAssets(current, assetPage.data))
        setSheetSelections((current) => ({ ...defaultSheetSelections(assetPage.data), ...current }))
        setDocuments(documentPage.data)
        setRuns(runPage.data)
        setRun(runPage.data[0] ?? null)
        if (activeJob) void monitor(activeJob)
      } catch (caught) {
        if (active) setError(errorMessage(caught))
      } finally {
        if (active) setLoading(false)
      }
    }
    void load()
    return () => {
      active = false
      pollRef.current?.abort()
    }
  }, [])

  useEffect(() => {
    let active = true
    setDocumentSources([])
    setSelected((values) => new Set([...values].filter((key) => !key.startsWith("document:"))))
    if (!documentId) {
      return () => {
        active = false
      }
    }
    listDocumentMultimodalSources(documentId)
      .then((response) => {
        if (active) setDocumentSources(response.data)
      })
      .catch((caught) => {
        if (active) setError(errorMessage(caught))
      })
    return () => {
      active = false
    }
  }, [documentId])

  async function monitor(initial: Job<MultimodalJobResult>) {
    pollRef.current?.abort()
    const controller = new AbortController()
    pollRef.current = controller
    setJob(initial)
    try {
      const terminal = await waitForJob(initial, {
        signal: controller.signal,
        onUpdate: setJob,
      })
      if (terminal.status === "SUCCEEDED" && terminal.result?.runId) {
        const [latestRun, runPage] = await Promise.all([
          getMultimodalRun(terminal.result.runId),
          listMultimodalRuns(),
        ])
        setRun(latestRun)
        setRuns(runPage.data)
      } else if (terminal.status === "FAILED") {
        setError(terminal.error?.message || "多模态分析失败")
      }
    } catch (caught) {
      if (!(caught instanceof DOMException && caught.name === "AbortError")) setError(errorMessage(caught))
    }
  }

  const upload = async (files: File[]) => {
    if (!files.length) return
    setUploading(true)
    setError(null)
    try {
      const response = await uploadMultimodalAssets(files)
      setAssets((values) => [...response.data, ...values])
      setSheetSelections((values) => ({ ...values, ...defaultSheetSelections(response.data) }))
      setSelected((values) => {
        const next = new Set(values)
        response.data.forEach((asset) => next.add(sourceKey("upload", asset.id)))
        return next
      })
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setUploading(false)
      if (inputRef.current) inputRef.current.value = ""
    }
  }

  const analyze = async () => {
    const sources = selectedSources(selected, assets, documentSources, sheetSelections)
    if (!sources.length) return
    setError(null)
    try {
      const created = await createJob<MultimodalJobResult>("multimodal_analyze", {
        sources,
        question: question.trim(),
        useLiteratureContext,
      })
      void monitor(created)
    } catch (caught) {
      setError(errorMessage(caught))
    }
  }

  const removeAsset = async (assetId: string) => {
    try {
      await deleteMultimodalAsset(assetId)
      setAssets((values) => values.filter((asset) => asset.id !== assetId))
      setSheetSelections((values) => {
        const next = { ...values }
        delete next[assetId]
        return next
      })
      setSelected((values) => {
        const next = new Set(values)
        next.delete(sourceKey("upload", assetId))
        return next
      })
    } catch (caught) {
      setError(errorMessage(caught))
    }
  }

  const running = job?.status === "QUEUED" || job?.status === "RUNNING"
  const selectedSourceCount = selectedSources(selected, assets, documentSources, sheetSelections).length

  return (
    <div
      className="flex min-h-[760px] flex-col gap-3 xl:h-full"
      onDragOver={(event) => event.preventDefault()}
      onDrop={(event) => {
        event.preventDefault()
        void upload(Array.from(event.dataTransfer.files))
      }}
      onPaste={(event) => {
        const files = Array.from(event.clipboardData.files)
        if (files.length) void upload(files)
      }}
    >
      <Toolbar>
        <input
          ref={inputRef}
          type="file"
          multiple
          className="hidden"
          accept="image/*,.xlsx,.xls,.csv"
          onChange={(event) => void upload(Array.from(event.target.files ?? []))}
        />
        <Btn variant="primary" icon={Upload} disabled={uploading} onClick={() => inputRef.current?.click()}>
          {uploading ? "上传中" : "导入资源"}
        </Btn>
        <Btn icon={Play} disabled={running || selectedSourceCount === 0} onClick={analyze}>开始分析</Btn>
        <Btn icon={X} disabled={selectedSourceCount === 0 && !run} onClick={() => { setSelected(new Set()); setRun(null); setJob(null); setError(null) }}>清空工作区</Btn>
        {running && <Btn variant="danger" icon={X} onClick={() => job && cancelJob<MultimodalJobResult>(job.id).then(setJob)}>取消</Btn>}
        <div className="ml-auto flex min-w-52 items-center gap-2">
          <History className="size-4 text-muted-foreground" />
          <Select
            className="min-w-48 flex-1"
            value={run?.id ?? ""}
            onChange={(event) => setRun(runs.find((value) => value.id === event.target.value) ?? null)}
          >
            <option value="">分析历史</option>
            {runs.map((value) => <option key={value.id} value={value.id}>{new Date(value.updatedAt).toLocaleString()} · v{value.revision}</option>)}
          </Select>
          <Btn size="xs" variant="ghost" icon={RefreshCw} aria-label="刷新历史" title="刷新历史" onClick={() => listMultimodalRuns().then((response) => setRuns(response.data))} />
        </div>
      </Toolbar>

      {job && (
        <div className="flex items-center gap-3 rounded-md border border-border bg-card px-3 py-2 text-xs">
          <LoaderCircle className={cn("size-4 text-primary", running && "animate-spin")} />
          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-secondary"><div className="h-full bg-primary transition-all" style={{ width: `${job.progress}%` }} /></div>
          <span className="w-10 text-right tabular-nums">{job.progress}%</span>
          <span className="max-w-64 truncate text-muted-foreground">{job.message}</span>
        </div>
      )}
      {error && <div className="rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-xs text-danger">{error}</div>}

      <div className="grid min-h-64 grid-cols-1 gap-3 lg:grid-cols-[320px_minmax(0,1fr)]">
        <Panel title="分析来源" icon={Images} className="min-h-0" bodyClassName="overflow-auto">
          {loading ? <div className="flex h-32 items-center justify-center"><LoaderCircle className="size-4 animate-spin text-primary" /></div> : (
            <div className="space-y-4">
              <SourceSection title="独立资源" empty="暂无独立资源">
                {assets.map((asset) => {
                  const key = sourceKey("upload", asset.id)
                  const checked = selected.has(key)
                  return <div key={asset.id}>
                    <SourceRow
                      checked={checked}
                      onChecked={(nextChecked) => updateSelection(setSelected, key, nextChecked)}
                      icon={asset.assetType === "image" ? FileImage : FileSpreadsheet}
                      title={asset.fileName}
                      meta={asset.assetType === "workbook" ? `${asset.metadata.sheetCount ?? 0} 个工作表` : `${asset.metadata.width ?? 0} × ${asset.metadata.height ?? 0}`}
                      onDelete={() => void removeAsset(asset.id)}
                    />
                    {checked && asset.assetType === "workbook" && (
                      <WorkbookSheetPicker
                        sheetNames={asset.metadata.sheetNames ?? []}
                        selected={sheetSelections[asset.id] ?? []}
                        onChange={(values) => setSheetSelections((current) => ({ ...current, [asset.id]: values }))}
                      />
                    )}
                  </div>
                })}
              </SourceSection>
              <div className="border-t border-border pt-3">
                <FieldLabel>文献</FieldLabel>
                <Select aria-label="文献" className="w-full" value={documentId} onChange={(event) => setDocumentId(Number(event.target.value))}>
                  <option value={0}>选择文献</option>
                  {documents.map((document) => <option key={document.id} value={document.id}>{document.title}</option>)}
                </Select>
                <div className="mt-2 space-y-1">
                  {documentSources.map((source) => {
                    const key = sourceKey("documentAsset", source.id, source.documentId)
                    return <SourceRow
                      key={source.id}
                      checked={selected.has(key)}
                      onChecked={(checked) => updateSelection(setSelected, key, checked)}
                      icon={FileImage}
                      title={source.fileName}
                      meta={`DOC-${source.documentId}`}
                    />
                  })}
                  {documentId > 0 && documentSources.length === 0 && <p className="py-4 text-center text-xs text-muted-foreground">该文献暂无图片资源</p>}
                </div>
              </div>
              <div className="border-t border-border pt-3">
                <FieldLabel>分析问题</FieldLabel>
                <Textarea rows={4} value={question} onChange={(event) => setQuestion(event.target.value)} />
                <label className="mt-3 flex items-center justify-between gap-3 text-xs text-muted-foreground">
                  文献上下文
                  <Toggle checked={useLiteratureContext} onChange={setUseLiteratureContext} />
                </label>
              </div>
            </div>
          )}
        </Panel>
        <Panel title="运行概览" icon={Images} className="min-h-0">
          {!run ? <NoDataState title="尚未运行多模态分析" /> : (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
              <Metric label="资源" value={run.result.summary.total} />
              <Metric label="成功" value={run.result.summary.succeeded} tone="green" />
              <Metric label="失败" value={run.result.summary.failed} tone={run.result.summary.failed ? "orange" : "gray"} />
              <Metric label="数据点" value={run.result.summary.dataPoints} />
              <Metric label="关联" value={run.result.summary.associations} />
            </div>
          )}
        </Panel>
      </div>

      <MultimodalResultWorkspace run={run} onUpdated={(updated) => {
        setRun(updated)
        setRuns((values) => values.map((value) => value.id === updated.id ? updated : value))
      }} />
    </div>
  )
}

function SourceSection({ title, empty, children }: { title: string; empty: string; children: React.ReactNode }) {
  const values = Array.isArray(children) ? children.filter(Boolean) : children
  return <div><h3 className="mb-2 text-xs font-semibold text-foreground">{title}</h3>{Array.isArray(values) && values.length === 0 ? <p className="py-4 text-center text-xs text-muted-foreground">{empty}</p> : <div className="space-y-1">{children}</div>}</div>
}

function mergeAssets(current: MultimodalAsset[], loaded: MultimodalAsset[]) {
  const byId = new Map(loaded.map((asset) => [asset.id, asset]))
  for (const asset of current) byId.set(asset.id, asset)
  return [...byId.values()].sort((left, right) => right.createdAt.localeCompare(left.createdAt))
}

function SourceRow({ checked, onChecked, icon: Icon, title, meta, onDelete }: {
  checked: boolean
  onChecked: (checked: boolean) => void
  icon: typeof FileImage
  title: string
  meta: string
  onDelete?: () => void
}) {
  return <div className="flex items-center gap-2 rounded-md border border-border px-2 py-2">
    <Checkbox checked={checked} onChange={onChecked} ariaLabel={`选择 ${title}`} />
    <Icon className="size-4 shrink-0 text-primary" />
    <div className="min-w-0 flex-1"><p className="truncate text-xs font-medium">{title}</p><p className="truncate text-[10px] text-muted-foreground">{meta}</p></div>
    {onDelete && <Btn size="xs" variant="ghost" icon={Trash2} aria-label="删除资源" title="删除资源" onClick={onDelete} />}
  </div>
}

function WorkbookSheetPicker({ sheetNames, selected, onChange }: {
  sheetNames: string[]
  selected: string[]
  onChange: (values: string[]) => void
}) {
  if (!sheetNames.length) return <p className="ml-6 py-2 text-xs text-danger">未发现可读取的工作表</p>
  return <div className="ml-6 mt-1 max-h-32 overflow-auto border-l border-border pl-3">
    <div className="mb-1 flex items-center justify-between text-[10px] text-muted-foreground">
      <span>工作表</span><span className="tabular-nums">{selected.length}/10</span>
    </div>
    <div className="grid grid-cols-1 gap-1 sm:grid-cols-2">
      {sheetNames.map((sheetName) => {
        const checked = selected.includes(sheetName)
        return <Checkbox
          key={sheetName}
          checked={checked}
          disabled={!checked && selected.length >= 10}
          label={<span className="max-w-28 truncate" title={sheetName}>{sheetName}</span>}
          ariaLabel={sheetName}
          onChange={(nextChecked) => onChange(nextChecked
            ? [...selected, sheetName]
            : selected.filter((value) => value !== sheetName))}
        />
      })}
    </div>
  </div>
}

function Metric({ label, value, tone = "gray" }: { label: string; value: number; tone?: "gray" | "green" | "orange" }) {
  return <div className="border-r border-border last:border-r-0"><p className="text-xs text-muted-foreground">{label}</p><div className="mt-1 flex items-baseline gap-2"><strong className="text-xl tabular-nums">{value}</strong><Tag tone={tone}>{label}</Tag></div></div>
}

function sourceKey(type: "upload" | "documentAsset", assetId: string, documentId?: number) {
  return type === "upload" ? `upload:${assetId}` : `document:${documentId}:${assetId}`
}

function updateSelection(setter: React.Dispatch<React.SetStateAction<Set<string>>>, key: string, checked: boolean) {
  setter((values) => {
    const next = new Set(values)
    if (checked) next.add(key)
    else next.delete(key)
    return next
  })
}

function selectedSources(
  selected: Set<string>,
  assets: MultimodalAsset[],
  documentSources: DocumentMultimodalSource[],
  sheetSelections: Record<string, string[]>,
): SelectedSource[] {
  const values: SelectedSource[] = []
  for (const asset of assets) {
    if (selected.has(sourceKey("upload", asset.id))) {
      const sheetNames = asset.assetType === "workbook" ? (sheetSelections[asset.id] ?? []).slice(0, 10) : []
      if (asset.assetType === "workbook" && sheetNames.length === 0) continue
      values.push({
        sourceType: "upload",
        assetId: asset.id,
        sheetNames,
      })
    }
  }
  for (const source of documentSources) {
    if (selected.has(sourceKey("documentAsset", source.id, source.documentId))) {
      values.push({ sourceType: "documentAsset", documentId: source.documentId, assetId: source.id })
    }
  }
  return values
}

function defaultSheetSelections(assets: MultimodalAsset[]) {
  return Object.fromEntries(assets
    .filter((asset) => asset.assetType === "workbook")
    .map((asset) => [asset.id, (asset.metadata.sheetNames ?? []).slice(0, 10)]))
}

function errorMessage(value: unknown) {
  return value instanceof Error ? value.message : "请求失败"
}
