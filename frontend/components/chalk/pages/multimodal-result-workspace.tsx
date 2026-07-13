"use client"

import { useEffect, useMemo, useState } from "react"
import { AlertTriangle, ChevronLeft, ChevronRight, Copy, LoaderCircle, Save, Trash2 } from "lucide-react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { apiUrl } from "@/lib/api/client"
import { getMultimodalTablePreview, patchMultimodalRun } from "@/lib/api/science"
import type {
  MultimodalAnnotation,
  MultimodalDataPoint,
  MultimodalRun,
  MultimodalRunItem,
  MultimodalTablePreview,
} from "@/lib/api/types"
import { NoDataState } from "../api-state"
import { Btn, Checkbox, Input, Panel, Select, Tabs, Tag, Textarea } from "../ui"
import { MultimodalAnnotationCanvas } from "./multimodal-annotation"


const RESULT_TABS = ["解读", "数据", "关联", "定量", "覆盖"]

export function MultimodalResultWorkspace({
  run,
  onUpdated,
}: {
  run: MultimodalRun | null
  onUpdated: (run: MultimodalRun) => void
}) {
  const [itemIndex, setItemIndex] = useState(0)
  const [activeTab, setActiveTab] = useState("解读")
  const [points, setPoints] = useState<MultimodalDataPoint[]>([])
  const [annotations, setAnnotations] = useState<MultimodalAnnotation[]>([])
  const [selectedAnnotation, setSelectedAnnotation] = useState<string | null>(null)
  const [tablePreview, setTablePreview] = useState<MultimodalTablePreview | null>(null)
  const [tableSheet, setTableSheet] = useState("")
  const [tablePage, setTablePage] = useState(1)
  const [tableLoading, setTableLoading] = useState(false)
  const [tableError, setTableError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const item = run?.result.items[itemIndex] ?? null
  const isWorkbook = Boolean(item && item.source.sourceType === "upload" && (
    item.source.mimeType === "text/csv" || item.source.mimeType?.includes("spreadsheet") || item.source.mimeType?.includes("excel")
  ))
  const tableSheets = useMemo(() => isWorkbook && Array.isArray(item?.coverage)
    ? item.coverage.map((value) => String(value.sheetName || "")).filter(Boolean)
    : [], [isWorkbook, item])
  const resultTabs = isWorkbook ? ["解读", "表格", ...RESULT_TABS.slice(1)] : RESULT_TABS

  useEffect(() => {
    setItemIndex(0)
  }, [run?.id])

  useEffect(() => {
    setPoints(item?.dataPoints.map((point) => ({ ...point })) ?? [])
    setAnnotations(item?.annotations.map((annotation) => ({ ...annotation })) ?? [])
    setSelectedAnnotation(null)
    setMessage(null)
    setError(null)
    setTablePreview(null)
    setTableSheet(tableSheets[0] ?? "")
    setTablePage(1)
    setTableError(null)
  }, [item, tableSheets])

  useEffect(() => {
    if (!isWorkbook && activeTab === "表格") setActiveTab("解读")
  }, [activeTab, isWorkbook])

  useEffect(() => {
    if (!item || !isWorkbook || activeTab !== "表格" || !tableSheet) return
    let active = true
    setTableLoading(true)
    setTableError(null)
    getMultimodalTablePreview(item.source.assetId, tableSheet, tablePage)
      .then((value) => {
        if (active) setTablePreview(value)
      })
      .catch((caught) => {
        if (active) setTableError(caught instanceof Error ? caught.message : "表格预览失败")
      })
      .finally(() => {
        if (active) setTableLoading(false)
      })
    return () => {
      active = false
    }
  }, [activeTab, isWorkbook, item, tablePage, tableSheet])

  const selected = annotations.find((annotation) => annotation.id === selectedAnnotation) ?? null
  const imageUrl = useMemo(() => item ? sourceContentUrl(item) : null, [item])

  if (!run) {
    return <Panel className="min-h-0"><NoDataState title="尚未运行多模态分析" /></Panel>
  }

  const dirty = item
    ? JSON.stringify(points) !== JSON.stringify(item.dataPoints) || JSON.stringify(annotations) !== JSON.stringify(item.annotations)
    : false

  const save = async () => {
    if (!item) return
    setSaving(true)
    setError(null)
    setMessage(null)
    try {
      const updated = await patchMultimodalRun(run.id, run.revision, {
        itemCorrections: [{ itemIndex, dataPoints: points, annotations }],
      })
      onUpdated(updated)
      setMessage(`修订已保存为版本 ${updated.revision}`)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "保存修订失败")
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="grid min-h-[1050px] flex-none grid-cols-1 grid-rows-[minmax(620px,1fr)_minmax(400px,auto)] gap-3 xl:min-h-0 xl:flex-1 xl:grid-cols-[minmax(0,1fr)_340px] xl:grid-rows-1">
      <Panel
        title={`分析结果 · v${run.revision}`}
        noPadding
        className="min-h-0"
        bodyClassName="flex min-h-0 flex-col"
        extra={<>
          <Tag tone={run.result.summary.failed ? "orange" : "green"}>{run.result.summary.succeeded}/{run.result.summary.total}</Tag>
          <Btn size="xs" icon={Copy} onClick={() => void navigator.clipboard.writeText(JSON.stringify(run.result, null, 2))}>复制结果</Btn>
          <Btn size="xs" icon={Save} disabled={!dirty || saving} onClick={save}>{saving ? "保存中" : "保存修订"}</Btn>
        </>}
      >
        <div className="flex gap-1 overflow-x-auto border-b border-border px-2 py-2">
          {run.result.items.map((entry, index) => (
            <button
              key={`${entry.source.assetId}-${index}`}
              onClick={() => setItemIndex(index)}
              className={`h-8 max-w-48 shrink-0 truncate rounded-md border px-2 text-xs ${index === itemIndex ? "border-primary bg-primary-soft text-primary" : "border-border text-muted-foreground"}`}
            >
              {entry.source.fileName || `资源 ${index + 1}`}
            </button>
          ))}
        </div>
        {error && <div className="border-b border-danger/30 bg-danger-soft px-3 py-2 text-xs text-danger">{error}</div>}
        {message && <div className="border-b border-success/30 bg-success-soft px-3 py-2 text-xs text-success">{message}</div>}
        {!item || item.status === "failed" ? (
          <NoDataState title={item?.error?.message || "该资源分析失败"} />
        ) : (
          <>
            <Tabs tabs={resultTabs} active={activeTab} onChange={setActiveTab} />
            <div className="min-h-0 flex-1 overflow-auto">
              {activeTab === "解读" && (
                imageUrl ? (
                  <div className="grid h-full min-h-[520px] grid-rows-[minmax(280px,1fr)_minmax(180px,0.65fr)]">
                    <MultimodalAnnotationCanvas
                      imageUrl={imageUrl}
                      annotations={annotations}
                      onCreate={(annotation) => setAnnotations((values) => [...values, annotation])}
                      selectedId={selectedAnnotation}
                      onSelect={setSelectedAnnotation}
                    />
                    <div className="overflow-auto border-t border-border p-4 text-[13px] leading-7">
                      <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>{item.analysisMarkdown || ""}</ReactMarkdown>
                    </div>
                  </div>
                ) : (
                  <div className="p-4 text-[13px] leading-7">
                    <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>{item.analysisMarkdown || ""}</ReactMarkdown>
                  </div>
                )
              )}
              {activeTab === "数据" && <DataPointTable points={points} onChange={setPoints} />}
              {activeTab === "表格" && <TablePreview
                preview={tablePreview}
                loading={tableLoading}
                error={tableError}
                sheets={tableSheets}
                sheet={tableSheet}
                onSheet={(value) => {
                  setTableSheet(value)
                  setTablePage(1)
                }}
                page={tablePage}
                onPage={setTablePage}
              />}
              {activeTab === "关联" && <JsonList values={run.result.associations} empty="尚未发现跨资源关联" />}
              {activeTab === "定量" && <QuantitativeView value={run.result.quantitative} />}
              {activeTab === "覆盖" && <pre className="whitespace-pre-wrap p-4 text-xs leading-6 text-muted-foreground">{JSON.stringify(item.coverage || {}, null, 2)}</pre>}
            </div>
          </>
        )}
      </Panel>

      <Panel title="人工校正" icon={AlertTriangle} className="min-h-0" bodyClassName="overflow-auto">
        {!item || item.status === "failed" ? <NoDataState title="请选择成功的分析资源" /> : (
          <div className="space-y-4">
            <div>
              <h3 className="mb-2 text-xs font-semibold text-foreground">数据点</h3>
              <div className="space-y-2">
                {points.map((point, index) => (
                  <div key={`${point.parameter}-${index}`} className="border-b border-border pb-2">
                    <div className="grid grid-cols-[1fr_90px_62px_28px] gap-1">
                      <Input value={point.parameter} aria-label={`参数 ${index + 1}`} onChange={(event) => updatePoint(points, setPoints, index, "parameter", event.target.value)} />
                      <Input value={String(point.value)} aria-label={`数值 ${index + 1}`} onChange={(event) => updatePoint(points, setPoints, index, "value", event.target.value)} />
                      <Input value={point.unit} aria-label={`单位 ${index + 1}`} onChange={(event) => updatePoint(points, setPoints, index, "unit", event.target.value)} />
                      <Btn size="xs" variant="ghost" icon={Trash2} aria-label="删除数据点" title="删除数据点" onClick={() => setPoints((values) => values.filter((_, itemIndex) => itemIndex !== index))} />
                    </div>
                    <div className="mt-1 grid grid-cols-[110px_minmax(0,1fr)] items-center gap-2">
                      <Checkbox
                        checked={Boolean(point.isSuspicious)}
                        label="待复核"
                        onChange={(checked) => setPoints(points.map((value, itemIndex) => itemIndex === index ? { ...value, isSuspicious: checked } : value))}
                      />
                      <Input
                        value={point.note ?? point.suspiciousReason ?? ""}
                        aria-label={`备注 ${index + 1}`}
                        placeholder="备注"
                        onChange={(event) => updatePoint(points, setPoints, index, "note", event.target.value)}
                      />
                    </div>
                  </div>
                ))}
                {points.length === 0 && <p className="text-xs text-muted-foreground">暂无结构化数据点</p>}
              </div>
            </div>
            <div className="border-t border-border pt-3">
              <h3 className="mb-2 text-xs font-semibold text-foreground">区域标注</h3>
              {annotations.length === 0 ? <p className="text-xs text-muted-foreground">暂无区域标注</p> : (
                <div className="space-y-2">
                  {annotations.map((annotation, index) => (
                    <button
                      key={annotation.id}
                      onClick={() => setSelectedAnnotation(annotation.id)}
                      className={`flex w-full items-center justify-between rounded-md border px-2 py-2 text-left text-xs ${selectedAnnotation === annotation.id ? "border-primary bg-primary-soft" : "border-border"}`}
                    >
                      <span>区域 {index + 1}</span>
                      <span className="font-mono text-[10px] text-muted-foreground">{annotation.width.toFixed(2)} × {annotation.height.toFixed(2)}</span>
                    </button>
                  ))}
                </div>
              )}
              {selected && (
                <div className="mt-3 space-y-2">
                  <Textarea
                    rows={4}
                    value={selected.note}
                    aria-label="区域备注"
                    onChange={(event) => setAnnotations((values) => values.map((annotation) => annotation.id === selected.id ? { ...annotation, note: event.target.value } : annotation))}
                  />
                  <Btn size="xs" variant="danger" icon={Trash2} onClick={() => {
                    setAnnotations((values) => values.filter((annotation) => annotation.id !== selected.id))
                    setSelectedAnnotation(null)
                  }}>删除标注</Btn>
                </div>
              )}
            </div>
          </div>
        )}
      </Panel>
    </div>
  )
}

function sourceContentUrl(item: MultimodalRunItem) {
  if (!item.source.assetId || item.source.mimeType?.includes("spreadsheet") || item.source.mimeType === "text/csv") return null
  return item.source.sourceType === "documentAsset" && item.source.documentId
    ? apiUrl(`/documents/${item.source.documentId}/assets/${item.source.assetId}`)
    : apiUrl(`/multimodal/assets/${item.source.assetId}/content`)
}

function updatePoint(
  points: MultimodalDataPoint[],
  setPoints: (value: MultimodalDataPoint[]) => void,
  index: number,
  key: "parameter" | "value" | "unit" | "note",
  value: string,
) {
  setPoints(points.map((point, pointIndex) => pointIndex === index ? { ...point, [key]: value } : point))
}

function TablePreview({ preview, loading, error, sheets, sheet, onSheet, page, onPage }: {
  preview: MultimodalTablePreview | null
  loading: boolean
  error: string | null
  sheets: string[]
  sheet: string
  onSheet: (value: string) => void
  page: number
  onPage: (value: number) => void
}) {
  if (loading && !preview) return <div className="flex h-48 items-center justify-center"><LoaderCircle className="size-4 animate-spin text-primary" /></div>
  if (error) return <NoDataState title={error} />
  if (!preview) return <NoDataState title="暂无表格预览" />
  return <div className="flex h-full min-h-96 flex-col">
    <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
      <Select aria-label="预览工作表" value={sheet} onChange={(event) => onSheet(event.target.value)}>
        {sheets.map((value) => <option key={value} value={value}>{value}</option>)}
      </Select>
      <span className="text-xs text-muted-foreground">{preview.pagination.totalItems.toLocaleString()} 行</span>
      <div className="ml-auto flex items-center gap-1">
        <Btn size="xs" variant="ghost" icon={ChevronLeft} aria-label="上一页" title="上一页" disabled={page <= 1} onClick={() => onPage(page - 1)} />
        <span className="min-w-16 text-center text-xs tabular-nums">{page} / {Math.max(preview.pagination.totalPages, 1)}</span>
        <Btn size="xs" variant="ghost" icon={ChevronRight} aria-label="下一页" title="下一页" disabled={page >= preview.pagination.totalPages} onClick={() => onPage(page + 1)} />
      </div>
    </div>
    <div className="min-h-0 flex-1 overflow-auto">
      <table className="min-w-full whitespace-nowrap text-left text-xs">
        <thead className="sticky top-0 bg-card"><tr>{preview.columns.map((column, index) => <th key={`${column}-${index}`} className="border-b border-r border-border px-3 py-2 font-semibold">{column}</th>)}</tr></thead>
        <tbody>{preview.rows.map((row, rowIndex) => <tr key={rowIndex}>{preview.columns.map((_, columnIndex) => <td key={columnIndex} className="border-b border-r border-border/70 px-3 py-2">{String(row[columnIndex] ?? "")}</td>)}</tr>)}</tbody>
      </table>
    </div>
  </div>
}

function DataPointTable({ points, onChange }: { points: MultimodalDataPoint[]; onChange: (value: MultimodalDataPoint[]) => void }) {
  return (
    <div className="overflow-auto p-3">
      {points.length === 0 ? <NoDataState title="暂无结构化数据点" /> : (
        <table className="w-full text-left text-xs">
          <thead><tr className="border-b border-border text-muted-foreground"><th className="p-2">参数</th><th className="p-2">数值</th><th className="p-2">单位</th><th className="p-2">状态</th></tr></thead>
          <tbody>{points.map((point, index) => <tr key={`${point.parameter}-${index}`} className="border-b border-border/70"><td className="p-2">{point.parameterCn || point.parameter}</td><td className="p-2 font-mono">{String(point.value)}</td><td className="p-2">{point.unit}</td><td className="p-2"><button onClick={() => onChange(points.map((value, itemIndex) => itemIndex === index ? { ...value, isSuspicious: !value.isSuspicious } : value))}><Tag tone={point.isSuspicious ? "orange" : "green"}>{point.isSuspicious ? "待复核" : "已确认"}</Tag></button></td></tr>)}</tbody>
        </table>
      )}
    </div>
  )
}

function JsonList({ values, empty }: { values: Array<Record<string, unknown>>; empty: string }) {
  if (!values.length) return <NoDataState title={empty} />
  return <div className="space-y-2 p-3">{values.map((value, index) => <pre key={index} className="whitespace-pre-wrap rounded-md border border-border bg-secondary/40 p-3 text-xs leading-6">{JSON.stringify(value, null, 2)}</pre>)}</div>
}

function QuantitativeView({ value }: { value: MultimodalRun["result"]["quantitative"] }) {
  const relations = value.scalingRelations ?? []
  const correlations = value.correlations ?? []
  if (!relations.length && !correlations.length) return <NoDataState title="尚无满足样本量要求的定量关系" />
  return <div className="space-y-3 p-3"><JsonList values={relations} empty="暂无标度关系" /><JsonList values={correlations} empty="暂无相关关系" /></div>
}
