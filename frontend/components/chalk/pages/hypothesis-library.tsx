"use client"

import { useCallback, useEffect, useState } from "react"
import {
  Check,
  Cpu,
  Database,
  Edit3,
  Filter,
  FlaskConical,
  Library,
  RefreshCw,
  Search as SearchIcon,
  Trash2,
  X,
} from "lucide-react"
import { apiFetch, toQuery, type Page } from "@/lib/api/client"
import type { DocumentRecord, HypothesisDetail, HypothesisSummary } from "@/lib/api/types"
import { cn } from "@/lib/utils"
import { ErrorState, LoadingState, NoDataState } from "../api-state"
import { Btn, FieldLabel, Input, Panel, Select, Tag, Toolbar } from "../ui"
import { HypothesisDetailView } from "./hypothesis-detail"

const DOMAIN_OPTIONS = [
  ["", "全部领域"],
  ["electrocatalysis", "电催化"],
  ["photocatalysis", "光催化"],
  ["battery_materials", "电池材料"],
  ["synthetic_chemistry", "合成化学"],
  ["surface_interface_chemistry", "表面与界面化学"],
  ["physical_chemistry", "物理化学"],
  ["quantum_chemistry", "量子化学"],
  ["analytical_chemistry", "分析化学"],
  ["organic_chemistry", "有机化学"],
  ["inorganic_chemistry", "无机化学"],
  ["polymer_chemistry", "高分子化学"],
  ["chemical_biology", "化学生物学"],
  ["materials_chemistry", "材料化学"],
  ["energy_chemistry", "能源化学"],
  ["environmental_chemistry", "环境化学"],
  ["nano_chemistry", "纳米化学"],
  ["cluster_chemistry", "团簇化学"],
  ["green_chemical_engineering", "绿色化工"],
] as const

type Filters = {
  keyword: string
  status: string
  domain: string
  tag: string
  sourceDocumentId: string
}

const EMPTY_FILTERS: Filters = { keyword: "", status: "", domain: "", tag: "", sourceDocumentId: "" }

function statusTone(status: string): "green" | "red" | "blue" | "gray" {
  if (status === "approved") return "green"
  if (status === "rejected") return "red"
  if (status === "reviewed") return "blue"
  return "gray"
}

export function HypothesisLibraryPage({
  onOpenLab,
  onOpenModeling,
}: {
  onOpenLab?: () => void
  onOpenModeling?: (hypothesisId: number) => void
}) {
  const [items, setItems] = useState<HypothesisSummary[]>([])
  const [documents, setDocuments] = useState<DocumentRecord[]>([])
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS)
  const [appliedFilters, setAppliedFilters] = useState<Filters>(EMPTY_FILTERS)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [detail, setDetail] = useState<HypothesisDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [editTitle, setEditTitle] = useState("")
  const [editStatus, setEditStatus] = useState("draft")
  const [editTags, setEditTags] = useState("")
  const [editError, setEditError] = useState<string | null>(null)
  const [actionMessage, setActionMessage] = useState<string | null>(null)

  const loadList = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await apiFetch<Page<HypothesisSummary>>(
        `/hypotheses${toQuery({ ...appliedFilters, pageSize: 100 })}`,
      )
      setItems(response.data)
      setSelectedId((current) => {
        if (current && response.data.some((item) => item.id === current)) return current
        return response.data[0]?.id ?? null
      })
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "假设库加载失败")
    } finally {
      setLoading(false)
    }
  }, [appliedFilters])

  const loadDetail = useCallback(async (hypothesisId: number) => {
    setDetailLoading(true)
    setDetailError(null)
    try {
      const loaded = await apiFetch<HypothesisDetail>(`/hypotheses/${hypothesisId}`)
      setDetail(loaded)
    } catch (loadError) {
      setDetailError(loadError instanceof Error ? loadError.message : "假设详情加载失败")
      setDetail(null)
    } finally {
      setDetailLoading(false)
    }
  }, [])

  useEffect(() => { void loadList() }, [loadList])

  useEffect(() => {
    apiFetch<Page<DocumentRecord>>("/documents?pageSize=100")
      .then((response) => setDocuments(response.data))
      .catch(() => setDocuments([]))
  }, [])

  useEffect(() => {
    if (selectedId == null) {
      setDetail(null)
      return
    }
    void loadDetail(selectedId)
  }, [loadDetail, selectedId])

  function applyFilters() {
    setAppliedFilters({ ...filters })
  }

  function clearFilters() {
    setFilters(EMPTY_FILTERS)
    setAppliedFilters(EMPTY_FILTERS)
  }

  function openEditor() {
    if (!detail) return
    setEditTitle(detail.title)
    setEditStatus(detail.status)
    setEditTags(detail.tags.join(", "))
    setEditError(null)
    setEditing(true)
  }

  async function saveMetadata() {
    if (!detail || !editTitle.trim()) return
    setEditError(null)
    try {
      await apiFetch<HypothesisSummary>(`/hypotheses/${detail.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          title: editTitle.trim(),
          status: editStatus,
          tags: editTags.split(/[,，]/).map((value) => value.trim()).filter(Boolean),
        }),
      })
      setEditing(false)
      await Promise.all([loadList(), loadDetail(detail.id)])
    } catch (saveError) {
      setEditError(saveError instanceof Error ? saveError.message : "元数据保存失败")
    }
  }

  async function deleteSelected() {
    if (!detail || !window.confirm(`删除假设“${detail.title}”？`)) return
    try {
      await apiFetch<void>(`/hypotheses/${detail.id}`, { method: "DELETE" })
      setDetail(null)
      setSelectedId(null)
      await loadList()
    } catch (deleteError) {
      setDetailError(deleteError instanceof Error ? deleteError.message : "假设删除失败")
    }
  }

  async function createLabRecord() {
    if (!detail) return
    setDetailError(null)
    try {
      const content = [
        `# ${detail.title}`,
        detail.researchQuestion ? `\n## 研究问题\n${detail.researchQuestion}` : "",
        `\n## 假设与实验设计\n${JSON.stringify(detail.hypothesis, null, 2)}`,
        "\n## 实验记录\n- 日期：\n- 操作：\n- 现象：\n- 原始数据：\n- 下一步：",
      ].filter(Boolean).join("\n")
      await apiFetch("/lab-records", {
        method: "POST",
        body: JSON.stringify({
          title: `实验记录草稿 - ${detail.title}`.slice(0, 255),
          content,
          relatedDocIds: detail.sourceDocumentIds.length ? JSON.stringify(detail.sourceDocumentIds) : null,
        }),
      })
      onOpenLab?.()
    } catch (actionError) {
      setDetailError(actionError instanceof Error ? actionError.message : "实验记录创建失败")
    }
  }

  async function indexEvidence() {
    if (!detail) return
    setDetailError(null)
    try {
      const result = await apiFetch<{ literatureEvidence: number; domainEvidence: number }>(`/evidence/hypotheses/${detail.id}/index`, { method: "POST" })
      setActionMessage(`已索引 ${result.literatureEvidence || 0} 条文献证据和 ${result.domainEvidence || 0} 条领域证据`)
    } catch (actionError) {
      setDetailError(actionError instanceof Error ? actionError.message : "假设证据索引失败")
    }
  }

  return (
    <div className="flex h-full min-h-[720px] flex-col gap-3 xl:min-h-0">
      <Toolbar>
        <Input
          icon={SearchIcon}
          placeholder="搜索标题、问题或标签"
          value={filters.keyword}
          onChange={(event) => setFilters((current) => ({ ...current, keyword: event.target.value }))}
          className="w-full sm:w-60"
          onKeyDown={(event) => { if (event.key === "Enter") applyFilters() }}
        />
        <Select value={filters.status} onChange={(event) => setFilters((current) => ({ ...current, status: event.target.value }))}>
          <option value="">全部状态</option>
          <option value="draft">草稿</option>
          <option value="reviewed">已审核</option>
          <option value="approved">已批准</option>
          <option value="rejected">已拒绝</option>
        </Select>
        <Select value={filters.domain} onChange={(event) => setFilters((current) => ({ ...current, domain: event.target.value }))}>
          {DOMAIN_OPTIONS.map(([value, label]) => <option key={value || "all"} value={value}>{label}</option>)}
        </Select>
        <Input className="w-32" placeholder="标签" value={filters.tag} onChange={(event) => setFilters((current) => ({ ...current, tag: event.target.value }))} />
        <Select value={filters.sourceDocumentId} onChange={(event) => setFilters((current) => ({ ...current, sourceDocumentId: event.target.value }))}>
          <option value="">全部来源文献</option>
          {documents.map((document) => <option key={document.id} value={document.id}>{document.title}</option>)}
        </Select>
        <Btn icon={Filter} onClick={applyFilters}>筛选</Btn>
        <Btn variant="ghost" icon={X} onClick={clearFilters}>清空</Btn>
        <Btn variant="ghost" icon={RefreshCw} onClick={() => void loadList()} aria-label="刷新假设库" />
        <span className="ml-auto text-xs tabular-nums text-muted-foreground">{items.length} 条</span>
      </Toolbar>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 xl:grid-cols-[340px_1fr]">
        <Panel title="假设列表" icon={Library} noPadding className="min-h-64" bodyClassName="min-h-0 overflow-auto">
          {loading ? <LoadingState /> : error ? <ErrorState message={error} onRetry={loadList} /> : items.length === 0 ? <NoDataState title="暂无假设" /> : (
            <div className="divide-y divide-border">
              {items.map((item) => (
                <button
                  key={item.id}
                  className={cn(
                    "w-full px-3 py-3 text-left transition-colors",
                    selectedId === item.id ? "bg-primary-soft" : "hover:bg-secondary/70",
                  )}
                  onClick={() => setSelectedId(item.id)}
                >
                  <div className="mb-1.5 flex items-center gap-2">
                    <span className="font-mono text-[10px] text-muted-foreground">H-{item.id}</span>
                    <Tag tone={statusTone(item.status)}>{item.status}</Tag>
                    <span className="ml-auto text-[11px] tabular-nums text-muted-foreground">{item.confidence}/10</span>
                  </div>
                  <h3 className="line-clamp-2 text-[13px] font-semibold leading-5 text-foreground">{item.title}</h3>
                  {item.researchQuestion && <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">{item.researchQuestion}</p>}
                  <div className="mt-2 flex flex-wrap items-center gap-1">
                    {item.domain && <Tag tone="gray">{item.domain}</Tag>}
                    {item.tags.slice(0, 3).map((tag) => <Tag key={tag} tone="purple">{tag}</Tag>)}
                    <span className="ml-auto text-[10px] text-muted-foreground">{new Date(item.updatedAt).toLocaleDateString()}</span>
                  </div>
                </button>
              ))}
            </div>
          )}
        </Panel>

        <Panel
          title={detail?.title || "假设详情"}
          icon={Library}
          noPadding
          className="min-h-0"
          bodyClassName="flex min-h-0 flex-col"
          extra={detail ? (
            <>
              <Btn size="xs" icon={FlaskConical} onClick={() => void createLabRecord()}>转实验记录</Btn>
              <Btn size="xs" icon={Cpu} onClick={() => onOpenModeling?.(detail.id)}>计算建模</Btn>
              <Btn size="xs" icon={Database} onClick={() => void indexEvidence()}>加入证据库</Btn>
              <Btn data-testid="edit-hypothesis" size="xs" icon={Edit3} onClick={openEditor}>编辑</Btn>
              <Btn size="xs" variant="danger" icon={Trash2} onClick={() => void deleteSelected()}>删除</Btn>
            </>
          ) : null}
        >
          {detailLoading ? <LoadingState label="正在加载假设详情" /> : detailError ? <ErrorState message={detailError} onRetry={() => selectedId ? loadDetail(selectedId) : undefined} /> : detail ? (
            <><HypothesisDetailView detail={detail} onRefresh={() => loadDetail(detail.id)} />{actionMessage && <div className="border-t border-border px-3 py-2 text-xs text-success">{actionMessage}</div>}</>
          ) : (
            <NoDataState title="请选择假设" />
          )}
        </Panel>
      </div>

      {editing && detail && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 p-3" role="dialog" aria-modal="true" aria-label="编辑假设元数据">
          <div className="w-full max-w-lg overflow-hidden rounded-lg border border-border bg-card shadow-xl">
            <header className="flex h-11 items-center gap-2 border-b border-border px-3">
              <Edit3 className="size-4 text-primary" />
              <h2 className="text-sm font-semibold">编辑假设元数据</h2>
              <Btn className="ml-auto" size="xs" variant="ghost" icon={X} onClick={() => setEditing(false)} aria-label="关闭编辑窗口" />
            </header>
            <div className="space-y-3 p-4">
              <div><FieldLabel>标题</FieldLabel><Input data-testid="hypothesis-edit-title" value={editTitle} onChange={(event) => setEditTitle(event.target.value)} /></div>
              <div><FieldLabel>状态</FieldLabel><Select data-testid="hypothesis-edit-status" className="w-full" value={editStatus} onChange={(event) => setEditStatus(event.target.value)}><option value="draft">草稿</option><option value="reviewed">已审核</option><option value="approved">已批准</option><option value="rejected">已拒绝</option></Select></div>
              <div><FieldLabel>标签</FieldLabel><Input data-testid="hypothesis-edit-tags" value={editTags} onChange={(event) => setEditTags(event.target.value)} placeholder="使用逗号分隔" /></div>
              {editError && <p className="text-xs text-danger">{editError}</p>}
            </div>
            <footer className="flex items-center justify-end gap-2 border-t border-border px-3 py-2">
              <Btn onClick={() => setEditing(false)}>取消</Btn>
              <Btn data-testid="save-hypothesis-metadata" variant="primary" icon={Check} disabled={!editTitle.trim()} onClick={() => void saveMetadata()}>保存</Btn>
            </footer>
          </div>
        </div>
      )}
    </div>
  )
}
