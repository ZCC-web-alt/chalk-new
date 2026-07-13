"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Database, Download, Network, RefreshCw, Search as SearchIcon, Square, Upload } from "lucide-react"
import { apiFetch, toQuery } from "@/lib/api/client"
import { cancelJob, waitForJob } from "@/lib/api/jobs"
import type { EvidenceResponse, Job } from "@/lib/api/types"
import { ErrorState, LoadingState, NoDataState } from "../api-state"
import { Btn, Input, Panel, Select, Tag, Toolbar } from "../ui"
import { cn } from "@/lib/utils"

type EvidenceNode = {
  id: string
  label: string
  kind: string
  reliabilityLevel?: string
  reliability_level?: string
}

type EvidenceEdge = { source: string; target: string; label?: string; relationType?: string; relation_type?: string }
type ManifestResult = { imported: number; skipped: number; errors: string[]; fileName: string }

function textValue(record: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = record[key]
    if (typeof value === "string" && value) return value
    if (typeof value === "number") return String(value)
  }
  return ""
}

export function EvidencePage() {
  const [records, setRecords] = useState<Record<string, unknown>[]>([])
  const [graph, setGraph] = useState<{ nodes: EvidenceNode[]; edges: EvidenceEdge[] }>({ nodes: [], edges: [] })
  const [selectedUid, setSelectedUid] = useState<string | null>(null)
  const [keyword, setKeyword] = useState("")
  const [kind, setKind] = useState("")
  const [domain, setDomain] = useState("")
  const [datasetName, setDatasetName] = useState("")
  const [reactionType, setReactionType] = useState("")
  const [material, setMaterial] = useState("")
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const graphRef = useRef<HTMLDivElement>(null)
  const manifestInputRef = useRef<HTMLInputElement>(null)
  const manifestPollRef = useRef<AbortController | null>(null)
  const [manifestJob, setManifestJob] = useState<Job<ManifestResult> | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await apiFetch<EvidenceResponse>(
        `/evidence${toQuery({ keyword, kind, domain, datasetName, reactionType, material, pageSize: 100 })}`,
      )
      setRecords(response.data)
      setGraph(response.graph as { nodes: EvidenceNode[]; edges: EvidenceEdge[] })
      setSelectedUid((current) => current && response.data.some((record) => String(record.evidenceUid ?? record.evidence_uid ?? record.id) === current)
        ? current
        : String(response.data[0]?.evidenceUid ?? response.data[0]?.evidence_uid ?? response.data[0]?.id ?? "") || null)
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "证据数据库加载失败")
    } finally {
      setLoading(false)
    }
  }, [datasetName, domain, keyword, kind, material, reactionType])

  useEffect(() => {
    void load()
    return () => manifestPollRef.current?.abort()
  }, [load])

  const selected = useMemo(
    () => records.find((record) => String(record.evidenceUid ?? record.evidence_uid ?? record.id) === selectedUid) ?? records[0],
    [records, selectedUid],
  )

  async function importCurated() {
    setError(null)
    setMessage(null)
    try {
      const result = await apiFetch<Record<string, number>>("/evidence/curated", { method: "POST" })
      setMessage(`当前包含 ${(result.literatureEvidence || 0) + (result.domainEvidence || 0)} 条可审计公开证据`)
      await load()
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "内置证据导入失败")
    }
  }

  async function showGraph() {
    await load()
    graphRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" })
  }

  async function importManifest(file: File) {
    manifestPollRef.current?.abort()
    const controller = new AbortController()
    manifestPollRef.current = controller
    setError(null)
    setMessage(null)
    const form = new FormData()
    form.set("file", file)
    form.set("datasetName", "OC dataset")
    try {
      const created = await apiFetch<Job<ManifestResult>>("/evidence/manifests", { method: "POST", body: form })
      setManifestJob(created)
      const completed = await waitForJob(created, { signal: controller.signal, onUpdate: setManifestJob })
      if (completed.status !== "SUCCEEDED" || !completed.result) throw new Error(completed.error?.message || "manifest 导入失败")
      setMessage(`已导入 ${completed.result.imported} 条计算证据，跳过 ${completed.result.skipped} 条`)
      setManifestJob(null)
      await load()
    } catch (importError) {
      if (importError instanceof DOMException && importError.name === "AbortError") return
      setError(importError instanceof Error ? importError.message : "manifest 导入失败")
    } finally {
      if (manifestInputRef.current) manifestInputRef.current.value = ""
    }
  }

  async function cancelManifest() {
    if (!manifestJob) return
    try {
      setManifestJob(await cancelJob<ManifestResult>(manifestJob.id))
      manifestPollRef.current?.abort()
    } catch (cancelError) {
      setError(cancelError instanceof Error ? cancelError.message : "manifest 任务取消失败")
    }
  }

  const manifestRunning = Boolean(manifestJob && ["QUEUED", "RUNNING"].includes(manifestJob.status))

  return (
    <div className="flex h-full min-h-[760px] flex-col gap-3 xl:min-h-0">
      <Toolbar>
        <input ref={manifestInputRef} type="file" accept=".csv,.json,.jsonl,.ndjson" className="hidden" aria-label="选择证据 manifest" onChange={(event) => { const file = event.target.files?.[0]; if (file) void importManifest(file) }} />
        <Select value={kind} onChange={(event) => setKind(event.target.value)}>
          <option value="">全部类型</option>
          <option value="literature">文献证据</option>
          <option value="domain">领域证据</option>
          <option value="computational">计算证据</option>
        </Select>
        <Select value={domain} onChange={(event) => setDomain(event.target.value)}>
          <option value="">全部领域</option>
          <option value="electrocatalysis">电催化</option>
          <option value="battery">电池</option>
        </Select>
        <Select value={datasetName} onChange={(event) => setDatasetName(event.target.value)}>
          <option value="">全部数据集</option>
          {['OC20', 'OC20-Dense', 'OC22', 'OC25', 'ODAC23'].map((dataset) => <option key={dataset} value={dataset}>{dataset}</option>)}
        </Select>
        <Input placeholder="反应/任务" className="w-28" value={reactionType} onChange={(event) => setReactionType(event.target.value)} />
        <Input placeholder="材料体系" className="w-32" value={material} onChange={(event) => setMaterial(event.target.value)} />
        <Input icon={SearchIcon} placeholder="关键词" className="w-48" value={keyword} onChange={(event) => setKeyword(event.target.value)} />
        <Btn icon={RefreshCw} onClick={load}>刷新</Btn>
        <Btn icon={Download} onClick={() => void importCurated()}>导入公开证据</Btn>
        {manifestRunning ? <Btn variant="danger" icon={Square} onClick={() => void cancelManifest()}>取消 manifest</Btn> : <Btn icon={Upload} onClick={() => manifestInputRef.current?.click()}>导入 manifest</Btn>}
        <Btn variant="purple" icon={Network} disabled={records.length === 0} onClick={() => void showGraph()}>生成关系图</Btn>
        <span className="ml-auto text-xs text-muted-foreground">
          {records.length} 条记录 · {graph.nodes.length} 节点 · {graph.edges.length} 关系
        </span>
      </Toolbar>

      {manifestJob && <div className="flex items-center gap-2 border border-border bg-card px-3 py-2 text-xs text-muted-foreground"><div className="h-1.5 flex-1 overflow-hidden rounded-full bg-secondary"><div className="h-full bg-primary" style={{ width: `${manifestJob.progress}%` }} /></div><span>{manifestJob.progress}%</span><span className="max-w-64 truncate">{manifestJob.message}</span></div>}

      {error && <ErrorState message={error} onRetry={load} />}
      {message && <div className="border border-success/30 bg-success-soft px-3 py-2 text-xs text-success">{message}</div>}

      <Panel title="证据条目" icon={Database} noPadding className="min-h-64 flex-1" bodyClassName="overflow-auto">
        {loading ? <LoadingState /> : records.length === 0 ? <NoDataState title="暂无证据" /> : (
          <table className="w-full min-w-[900px] text-[13px]">
            <thead className="sticky top-0 z-10 bg-secondary text-xs text-muted-foreground">
              <tr>{["ID", "类型", "来源/标题", "材料体系", "反应/任务", "关键数据", "可靠性"].map((heading) => <th key={heading} className="px-3 py-2 text-left font-medium">{heading}</th>)}</tr>
            </thead>
            <tbody className="divide-y divide-border">
              {records.map((record) => {
                const uid = String(record.evidenceUid ?? record.evidence_uid ?? record.id)
                const reliability = textValue(record, ["reliabilityLevel", "reliability_level"])
                return (
                  <tr key={uid} onClick={() => setSelectedUid(uid)} className={cn("cursor-pointer transition-colors hover:bg-secondary", selectedUid === uid && "bg-primary-soft")}>
                    <td className="px-3 py-2 font-mono text-xs text-primary">{uid}</td>
                    <td className="px-3 py-2"><Tag tone="blue">{textValue(record, ["kind"]) || "evidence"}</Tag></td>
                    <td className="px-3 py-2 text-foreground">{textValue(record, ["displayTitle", "display_title", "title", "sourceLabel", "source_label"])}</td>
                    <td className="px-3 py-2 text-foreground">{textValue(record, ["materialSystem", "material_system"]) || "-"}</td>
                    <td className="px-3 py-2 text-muted-foreground">{textValue(record, ["reactionType", "reaction_type", "reactionContext", "reaction_context"]) || "-"}</td>
                    <td className="px-3 py-2 font-mono text-xs text-foreground">{textValue(record, ["keyData", "key_data", "metricValue", "metric_value", "adsorptionEnergy", "adsorption_energy"]) || "-"}</td>
                    <td className="px-3 py-2"><Tag tone={reliability.includes("verified") || reliability === "lab_record" ? "green" : "orange"}>{reliability || "needs_verification"}</Tag></td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </Panel>

      <div className="grid min-h-0 shrink-0 grid-cols-1 gap-3 xl:grid-cols-[minmax(300px,0.8fr)_minmax(520px,1.2fr)]">
        <Panel title="证据详情" icon={Database}>
          {!selected ? <NoDataState title="请选择证据" /> : (
            <pre className="max-h-64 overflow-auto border border-border bg-secondary p-3 text-xs leading-relaxed text-foreground">{JSON.stringify(selected, null, 2)}</pre>
          )}
        </Panel>
        <div ref={graphRef}>
          <Panel title="关系图" icon={Network} bodyClassName="overflow-auto">
            {graph.nodes.length === 0 ? <NoDataState title="暂无可绘制关系" /> : <EvidenceGraph graph={graph} selectedId={selectedUid} onSelect={setSelectedUid} />}
          </Panel>
        </div>
      </div>
    </div>
  )
}

function EvidenceGraph({ graph, selectedId, onSelect }: { graph: { nodes: EvidenceNode[]; edges: EvidenceEdge[] }; selectedId: string | null; onSelect: (id: string) => void }) {
  const nodes = graph.nodes.slice(0, 30)
  const groups = { literature: [] as EvidenceNode[], domain: [] as EvidenceNode[], computational: [] as EvidenceNode[] }
  for (const node of nodes) (groups[node.kind as keyof typeof groups] || groups.domain).push(node)
  const xByKind = { literature: 100, domain: 440, computational: 780 }
  const positions = new Map<string, { x: number; y: number }>()
  for (const [kind, values] of Object.entries(groups)) values.forEach((node, index) => positions.set(node.id, { x: xByKind[kind as keyof typeof xByKind], y: 54 + index * 54 }))
  const height = Math.max(220, ...Object.values(groups).map((values) => 100 + values.length * 54))
  const visibleEdges = graph.edges.filter((edge) => positions.has(edge.source) && positions.has(edge.target)).slice(0, 80)
  const palette = { literature: "#2563eb", domain: "#16a34a", computational: "#ea580c" }
  return (
    <svg viewBox={`0 0 900 ${height}`} className="min-w-[720px]" style={{ height: Math.min(420, height) }} role="img" aria-label="证据关系图">
      <g className="fill-muted-foreground text-[12px] font-semibold"><text x="60" y="20">文献证据</text><text x="410" y="20">领域指标</text><text x="745" y="20">计算证据</text></g>
      {visibleEdges.map((edge, index) => {
        const source = positions.get(edge.source)!
        const target = positions.get(edge.target)!
        return <line key={`${edge.source}-${edge.target}-${index}`} x1={source.x} y1={source.y} x2={target.x} y2={target.y} stroke="#cbd5e1" strokeWidth="1.5"><title>{edge.label || edge.relationType || edge.relation_type}</title></line>
      })}
      {nodes.map((node) => {
        const position = positions.get(node.id)!
        const color = palette[node.kind as keyof typeof palette] || "#64748b"
        return (
          <g key={node.id} role="button" tabIndex={0} onClick={() => onSelect(node.id)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") onSelect(node.id) }} className="cursor-pointer">
            <circle cx={position.x} cy={position.y} r={selectedId === node.id ? 15 : 11} fill={color} stroke={selectedId === node.id ? "#111827" : "white"} strokeWidth="3" />
            <text x={position.x + 20} y={position.y + 4} className="fill-foreground text-[11px]">{shorten(node.label || node.id, 34)}</text>
            <title>{node.label || node.id}</title>
          </g>
        )
      })}
    </svg>
  )
}

function shorten(value: string, max: number) {
  return value.length > max ? `${value.slice(0, max - 1)}…` : value
}
