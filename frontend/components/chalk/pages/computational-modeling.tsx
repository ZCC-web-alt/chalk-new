"use client"

import { useEffect, useRef, useState } from "react"
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  Copy,
  Cpu,
  Download,
  FileCode2,
  History,
  LoaderCircle,
  Play,
  Plus,
  Save,
  Trash2,
  Upload,
  X,
} from "lucide-react"
import { apiFetch, type Page } from "@/lib/api/client"
import { cancelJob, createJob, getLatestActiveJob, waitForJob } from "@/lib/api/jobs"
import {
  deleteModelingWorkspace,
  downloadModelingArtifact,
  getModelingOptions,
  getModelingStructure,
  getModelingWorkspace,
  listModelingWorkspaces,
  patchModelingWorkspace,
  uploadModelingStructure,
} from "@/lib/api/science"
import type {
  DocumentRecord,
  HypothesisSummary,
  Job,
  ModelingExportResult,
  ModelingField,
  ModelingGenerateResult,
  ModelingOptions,
  ModelingPotcarElement,
  ModelingStructure,
  ModelingWorkspace,
} from "@/lib/api/types"
import { cn } from "@/lib/utils"
import { NoDataState } from "../api-state"
import { Btn, FieldLabel, Input, Panel, Select, Tabs, Tag, Textarea, Toolbar } from "../ui"
import { StructureViewer } from "./structure-viewer"


const WORKSPACE_TABS = ["参数", "输入文件", "结构", "Materials Studio", "Workflow", "风险", "修订"]
type SourceType = "manual" | "document" | "hypothesis"

export function ModelingPage({ initialHypothesisId }: { initialHypothesisId?: number | null }) {
  const structureInputRef = useRef<HTMLInputElement>(null)
  const pollRef = useRef<AbortController | null>(null)
  const [options, setOptions] = useState<ModelingOptions | null>(null)
  const [documents, setDocuments] = useState<DocumentRecord[]>([])
  const [hypotheses, setHypotheses] = useState<HypothesisSummary[]>([])
  const [workspaces, setWorkspaces] = useState<ModelingWorkspace[]>([])
  const [workspace, setWorkspace] = useState<ModelingWorkspace | null>(null)
  const [sourceType, setSourceType] = useState<SourceType>("manual")
  const [manualText, setManualText] = useState("")
  const [documentId, setDocumentId] = useState(0)
  const [hypothesisId, setHypothesisId] = useState(0)
  const [mode, setMode] = useState<"vasp" | "ms" | "both">("vasp")
  const [calcType, setCalcType] = useState("auto")
  const [vaspkitTask, setVaspkitTask] = useState("")
  const [activeTab, setActiveTab] = useState("参数")
  const [incarDraft, setIncarDraft] = useState<Record<string, ModelingField>>({})
  const [kpointsDraft, setKpointsDraft] = useState({ mode: "Gamma", mesh: [6, 6, 6] })
  const [potcarDraft, setPotcarDraft] = useState<ModelingPotcarElement[]>([])
  const [structure, setStructure] = useState<ModelingStructure | null>(null)
  const [job, setJob] = useState<Job<ModelingGenerateResult | ModelingExportResult> | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)

  useEffect(() => {
    if (!initialHypothesisId) return
    setSourceType("hypothesis")
    setHypothesisId(initialHypothesisId)
  }, [initialHypothesisId])

  useEffect(() => {
    let active = true
    Promise.all([
      getModelingOptions(),
      apiFetch<Page<DocumentRecord>>("/documents?pageSize=100"),
      apiFetch<Page<HypothesisSummary>>("/hypotheses?pageSize=100"),
      listModelingWorkspaces(),
      getLatestActiveJob<ModelingGenerateResult>("modeling_generate"),
    ]).then(([nextOptions, documentPage, hypothesisPage, workspacePage, activeJob]) => {
      if (!active) return
      setOptions(nextOptions)
      setDocuments(documentPage.data)
      setHypotheses(hypothesisPage.data)
      setWorkspaces(workspacePage.data)
      setWorkspace(workspacePage.data[0] ?? null)
      if (activeJob) void monitorGenerate(activeJob)
    }).catch((caught) => {
      if (active) setError(errorMessage(caught))
    }).finally(() => {
      if (active) setLoading(false)
    })
    return () => {
      active = false
      pollRef.current?.abort()
    }
  }, [])

  useEffect(() => {
    setIncarDraft(cloneFields(workspace?.result.incar ?? {}))
    setKpointsDraft({
      mode: workspace?.result.kpoints?.mode ?? "Gamma",
      mesh: [...(workspace?.result.kpoints?.mesh ?? [6, 6, 6])],
    })
    setPotcarDraft((workspace?.result.potcarElements ?? []).map((item) => ({
      ...item,
      ...(item.warnings ? { warnings: [...item.warnings] } : {}),
    })))
    setMessage(null)
    setError(null)
  }, [workspace])

  const structureWorkspaceId = workspace?.id
  const activeStructureId = workspace?.activeStructureId

  useEffect(() => {
    if (!structureWorkspaceId || !activeStructureId) {
      setStructure(null)
      return
    }
    getModelingStructure(structureWorkspaceId, activeStructureId)
      .then(setStructure)
      .catch((caught) => setError(errorMessage(caught)))
  }, [structureWorkspaceId, activeStructureId])

  async function monitorGenerate(initial: Job<ModelingGenerateResult>) {
    pollRef.current?.abort()
    const controller = new AbortController()
    pollRef.current = controller
    setJob(initial)
    try {
      const terminal = await waitForJob(initial, { signal: controller.signal, onUpdate: setJob })
      if (terminal.status === "SUCCEEDED" && terminal.result?.workspaceId) {
        const [created, page] = await Promise.all([
          getModelingWorkspace(terminal.result.workspaceId),
          listModelingWorkspaces(),
        ])
        setWorkspace(created)
        setWorkspaces(page.data)
        setMessage("建模工作区已生成")
      } else if (terminal.status === "FAILED") {
        setError(terminal.error?.message || "计算建模生成失败")
      }
    } catch (caught) {
      if (!(caught instanceof DOMException && caught.name === "AbortError")) setError(errorMessage(caught))
    }
  }

  const generate = async () => {
    const source = sourcePayload(sourceType, manualText, documentId, hypothesisId)
    if (!source) return
    setError(null)
    setMessage(null)
    try {
      const created = await createJob<ModelingGenerateResult>("modeling_generate", {
        source,
        mode,
        calcType,
        vaspkitTask,
      })
      void monitorGenerate(created)
    } catch (caught) {
      setError(errorMessage(caught))
    }
  }

  const saveParameters = async () => {
    if (!workspace) return
    setSaving(true)
    setError(null)
    try {
      const updated = await patchModelingWorkspace(workspace.id, workspace.revision, {
        incar: incarDraft,
        kpoints: { ...kpointsDraft, source: "user" },
        potcarElements: potcarDraft,
      })
      replaceWorkspace(updated, setWorkspace, setWorkspaces)
      setMessage(`参数已保存为版本 ${updated.revision}`)
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setSaving(false)
    }
  }

  const uploadStructure = async (file: File) => {
    if (!workspace) return
    setError(null)
    try {
      const uploaded = await uploadModelingStructure(workspace.id, file)
      const updated = await patchModelingWorkspace(workspace.id, workspace.revision, {}, uploaded.id)
      setStructure(uploaded)
      replaceWorkspace(updated, setWorkspace, setWorkspaces)
      setActiveTab("结构")
      setMessage("结构已解析并设为当前结构")
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      if (structureInputRef.current) structureInputRef.current.value = ""
    }
  }

  const exportWorkspace = async () => {
    if (!workspace) return
    setError(null)
    try {
      const created = await createJob<ModelingExportResult>("modeling_export", {
        workspaceId: workspace.id,
        expectedRevision: workspace.revision,
      })
      setJob(created)
      const terminal = await waitForJob(created, { onUpdate: setJob })
      if (terminal.status !== "SUCCEEDED" || !terminal.result?.artifact) {
        throw new Error(terminal.error?.message || "导出失败")
      }
      await downloadModelingArtifact(workspace.id, terminal.result.artifact.id, terminal.result.artifact.fileName)
      const refreshed = await getModelingWorkspace(workspace.id)
      replaceWorkspace(refreshed, setWorkspace, setWorkspaces)
      setMessage("建模包已导出")
    } catch (caught) {
      setError(errorMessage(caught))
    }
  }

  const removeWorkspace = async () => {
    if (!workspace) return
    try {
      await deleteModelingWorkspace(workspace.id)
      const remaining = workspaces.filter((value) => value.id !== workspace.id)
      setWorkspaces(remaining)
      setWorkspace(remaining[0] ?? null)
    } catch (caught) {
      setError(errorMessage(caught))
    }
  }

  const running = job?.status === "QUEUED" || job?.status === "RUNNING"
  const dirty = workspace
    ? JSON.stringify(incarDraft) !== JSON.stringify(workspace.result.incar ?? {})
      || JSON.stringify(kpointsDraft) !== JSON.stringify({
        mode: workspace.result.kpoints?.mode ?? "Gamma",
        mesh: workspace.result.kpoints?.mesh ?? [6, 6, 6],
      })
      || JSON.stringify(potcarDraft) !== JSON.stringify(workspace.result.potcarElements ?? [])
    : false

  return (
    <div className="flex h-full min-h-[760px] flex-col gap-3">
      <Toolbar>
        <div><FieldLabel>来源</FieldLabel><Select value={sourceType} onChange={(event) => setSourceType(event.target.value as SourceType)}><option value="manual">手动文本</option><option value="document">文献</option><option value="hypothesis">假设工作流</option></Select></div>
        <div><FieldLabel>模式</FieldLabel><Select value={mode} onChange={(event) => setMode(event.target.value as typeof mode)}><option value="vasp">VASP</option><option value="ms">Materials Studio</option><option value="both">VASP + MS</option></Select></div>
        {mode !== "ms" && <div><FieldLabel>计算类型</FieldLabel><Select value={calcType} onChange={(event) => setCalcType(event.target.value)}><option value="auto">自动识别</option>{options?.calcTypes.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</Select></div>}
        {mode !== "ms" && <div><FieldLabel>VASPKIT</FieldLabel><Select value={vaspkitTask} onChange={(event) => setVaspkitTask(event.target.value)}><option value="">不指定</option>{options?.vaspkitTasks.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</Select></div>}
        <Btn variant="primary" icon={Play} disabled={running || !sourcePayload(sourceType, manualText, documentId, hypothesisId)} onClick={generate}>生成工作区</Btn>
        <Btn icon={Download} disabled={!workspace || running} onClick={exportWorkspace}>导出</Btn>
        {running && <Btn variant="danger" icon={X} onClick={() => job && cancelJob<ModelingGenerateResult | ModelingExportResult>(job.id).then(setJob)}>取消</Btn>}
        <div className="ml-auto flex items-center gap-2">
          <History className="size-4 text-muted-foreground" />
          <Select className="max-w-64" value={workspace?.id ?? ""} onChange={(event) => setWorkspace(workspaces.find((value) => value.id === event.target.value) ?? null)}><option value="">建模历史</option>{workspaces.map((value) => <option key={value.id} value={value.id}>{String(value.source.type || "source")} · v{value.revision} · {new Date(value.updatedAt).toLocaleString()}</option>)}</Select>
          <Btn size="xs" variant="danger" icon={Trash2} disabled={!workspace} aria-label="删除工作区" title="删除工作区" onClick={removeWorkspace} />
        </div>
      </Toolbar>

      {job && <div className="flex items-center gap-3 rounded-md border border-border bg-card px-3 py-2 text-xs"><LoaderCircle className={cn("size-4 text-primary", running && "animate-spin")} /><div className="h-1.5 flex-1 overflow-hidden rounded-full bg-secondary"><div className="h-full bg-primary" style={{ width: `${job.progress}%` }} /></div><span className="tabular-nums">{job.progress}%</span><span className="max-w-72 truncate text-muted-foreground">{job.message}</span></div>}
      {error && <div className="rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-xs text-danger">{error}</div>}
      {message && <div className="rounded-md border border-success/30 bg-success-soft px-3 py-2 text-xs text-success">{message}</div>}

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 xl:grid-cols-[340px_minmax(0,1fr)]">
        <Panel title="建模输入" icon={Cpu} className="min-h-0" bodyClassName="overflow-auto">
          {loading ? <div className="flex h-40 items-center justify-center"><LoaderCircle className="size-4 animate-spin text-primary" /></div> : (
            <div className="space-y-3">
              {sourceType === "manual" && <><FieldLabel>计算方法或建模描述</FieldLabel><Textarea aria-label="计算方法或建模描述" rows={20} value={manualText} onChange={(event) => setManualText(event.target.value)} maxLength={options?.limits.manualTextChars ?? 60000} /><p className="text-right text-[10px] tabular-nums text-muted-foreground">{manualText.length.toLocaleString()} / {(options?.limits.manualTextChars ?? 60000).toLocaleString()}</p></>}
              {sourceType === "document" && <><FieldLabel>文献</FieldLabel><Select className="w-full" value={documentId} onChange={(event) => setDocumentId(Number(event.target.value))}><option value={0}>选择文献</option>{documents.map((document) => <option key={document.id} value={document.id}>{document.title}</option>)}</Select></>}
              {sourceType === "hypothesis" && <><FieldLabel>假设</FieldLabel><Select className="w-full" value={hypothesisId} onChange={(event) => setHypothesisId(Number(event.target.value))}><option value={0}>选择假设</option>{hypotheses.map((hypothesis) => <option key={hypothesis.id} value={hypothesis.id}>{hypothesis.title}</option>)}</Select></>}
              {workspace?.result.coverage && <div className="border-t border-border pt-3"><h3 className="mb-2 text-xs font-semibold">输入覆盖</h3><pre className="whitespace-pre-wrap text-[11px] leading-5 text-muted-foreground">{JSON.stringify(workspace.result.coverage, null, 2)}</pre></div>}
            </div>
          )}
        </Panel>

        <Panel title={workspace ? `${workspace.result.systemName || "建模工作区"} · v${workspace.revision}` : "计算建模工作区"} icon={FileCode2} noPadding className="min-h-0" bodyClassName="flex min-h-0 flex-col" extra={workspace && <><Tag tone="orange">dry-run</Tag><Btn size="xs" icon={Save} disabled={!dirty || saving} onClick={saveParameters}>{saving ? "保存中" : "保存参数"}</Btn></>}>
          {!workspace ? <NoDataState title="尚未生成建模工作区" /> : <>
            <Tabs tabs={WORKSPACE_TABS} active={activeTab} onChange={setActiveTab} />
            <div className="min-h-0 flex-1 overflow-auto">
              {activeTab === "参数" && <ParameterEditor workspace={workspace} incar={incarDraft} setIncar={setIncarDraft} kpoints={kpointsDraft} setKpoints={setKpointsDraft} potcar={potcarDraft} setPotcar={setPotcarDraft} />}
              {activeTab === "输入文件" && <FilesView files={workspace.result.files ?? {}} />}
              {activeTab === "结构" && <StructureWorkspace workspace={workspace} structure={structure} onSelect={async (structureId) => {
                const updated = await patchModelingWorkspace(workspace.id, workspace.revision, {}, structureId)
                replaceWorkspace(updated, setWorkspace, setWorkspaces)
              }} inputRef={structureInputRef} onUpload={uploadStructure} />}
              {activeTab === "Materials Studio" && <JsonView value={workspace.result.msGuide} empty="暂无 Materials Studio 指南" />}
              {activeTab === "Workflow" && <JsonView value={workspace.result.workflow || workspace.result.atomate2DryRun} empty="暂无 workflow 数据" />}
              {activeTab === "风险" && <RiskView values={workspace.result.riskNotices ?? []} />}
              {activeTab === "修订" && <RevisionView workspace={workspace} />}
            </div>
          </>}
        </Panel>
      </div>
    </div>
  )
}

function ParameterEditor({ workspace, incar, setIncar, kpoints, setKpoints, potcar, setPotcar }: {
  workspace: ModelingWorkspace
  incar: Record<string, ModelingField>
  setIncar: React.Dispatch<React.SetStateAction<Record<string, ModelingField>>>
  kpoints: { mode: string; mesh: number[] }
  setKpoints: React.Dispatch<React.SetStateAction<{ mode: string; mesh: number[] }>>
  potcar: ModelingPotcarElement[]
  setPotcar: React.Dispatch<React.SetStateAction<ModelingPotcarElement[]>>
}) {
  return <div className="space-y-5 p-4">
    <div className="grid grid-cols-2 gap-3 text-xs md:grid-cols-4"><Meta label="体系" value={workspace.result.systemName || "未识别"} /><Meta label="泛函" value={workspace.result.functional || "未识别"} /><Meta label="类型" value={workspace.result.calcType || "未识别"} /><Meta label="体系属性" value={workspace.result.isMetal ? "金属" : "非金属/未确认"} /></div>
    <div><h3 className="mb-2 text-xs font-semibold">INCAR</h3><div className="overflow-auto border-y border-border"><table className="w-full text-left text-xs"><thead><tr className="border-b border-border text-muted-foreground"><th className="p-2">参数</th><th className="p-2">值</th><th className="p-2">来源</th></tr></thead><tbody>{Object.entries(incar).sort(([a], [b]) => a.localeCompare(b)).map(([key, field]) => <tr key={key} className="border-b border-border/60"><td className="p-2 font-mono font-semibold">{key}</td><td className="p-2"><Input value={String(field.value)} aria-label={`INCAR ${key}`} onChange={(event) => setIncar((values) => ({ ...values, [key]: { ...field, value: coerceValue(event.target.value, field.value), source: "user" } }))} /></td><td className="p-2"><Tag tone={sourceTone(field.source)}>{field.source}</Tag></td></tr>)}</tbody></table></div></div>
    <div><h3 className="mb-2 text-xs font-semibold">KPOINTS</h3><div className="flex flex-wrap items-end gap-2"><div><FieldLabel>模式</FieldLabel><Select value={kpoints.mode} onChange={(event) => setKpoints((value) => ({ ...value, mode: event.target.value }))}><option value="Gamma">Gamma</option><option value="Monkhorst-Pack">Monkhorst-Pack</option><option value="Line">Line</option></Select></div>{kpoints.mesh.map((value, index) => <div key={index}><FieldLabel>{["Kx", "Ky", "Kz"][index]}</FieldLabel><Input className="w-20" type="number" min={1} max={99} value={value} onChange={(event) => setKpoints((current) => ({ ...current, mesh: current.mesh.map((item, itemIndex) => itemIndex === index ? Math.max(1, Math.min(99, Number(event.target.value))) : item) }))} /></div>)}</div></div>
    <div>
      <div className="mb-2 flex items-center justify-between"><h3 className="text-xs font-semibold">POTCAR 建议与顺序</h3><Btn size="xs" variant="ghost" icon={Plus} onClick={() => setPotcar((values) => [...values, { element: "", potential: "", source: "user", warnings: [] }])}>添加元素</Btn></div>
      {potcar.length === 0 ? <p className="py-3 text-xs text-muted-foreground">暂无 POTCAR 元素建议</p> : <div className="overflow-auto border-y border-border"><table className="w-full text-left text-xs"><thead><tr className="border-b border-border text-muted-foreground"><th className="p-2">顺序</th><th className="p-2">元素</th><th className="p-2">势函数名称</th><th className="p-2">来源</th><th className="p-2"><span className="sr-only">操作</span></th></tr></thead><tbody>{potcar.map((item, index) => <tr key={`${item.element}-${index}`} className="border-b border-border/60"><td className="p-2 tabular-nums">{index + 1}</td><td className="p-2"><Input className="w-24" value={item.element} aria-label={`POTCAR ${item.element || index + 1} element`} onChange={(event) => setPotcar((values) => values.map((value, itemIndex) => itemIndex === index ? { ...value, element: event.target.value, source: "user" } : value))} /></td><td className="p-2"><Input value={item.potential} aria-label={`POTCAR ${item.element || index + 1} potential`} onChange={(event) => setPotcar((values) => values.map((value, itemIndex) => itemIndex === index ? { ...value, potential: event.target.value, source: "user" } : value))} /></td><td className="p-2"><Tag tone={sourceTone(item.source)}>{item.source}</Tag></td><td className="p-2"><div className="flex justify-end gap-1"><Btn size="xs" variant="ghost" icon={ArrowUp} aria-label={`上移 ${item.element || index + 1}`} title="上移" disabled={index === 0} onClick={() => setPotcar((values) => moveItem(values, index, index - 1))} /><Btn size="xs" variant="ghost" icon={ArrowDown} aria-label={`下移 ${item.element || index + 1}`} title="下移" disabled={index === potcar.length - 1} onClick={() => setPotcar((values) => moveItem(values, index, index + 1))} /><Btn size="xs" variant="ghost" icon={Trash2} aria-label={`删除 ${item.element || index + 1}`} title="删除" onClick={() => setPotcar((values) => values.filter((_, itemIndex) => itemIndex !== index))} /></div></td></tr>)}</tbody></table></div>}
    </div>
  </div>
}

function FilesView({ files }: { files: { incar?: string; kpoints?: string; potcarGuide?: string } }) {
  const [tab, setTab] = useState("INCAR")
  const values: Record<string, string> = { INCAR: files.incar || "", KPOINTS: files.kpoints || "", "POTCAR Guide": files.potcarGuide || "" }
  return <div className="flex h-full min-h-96 flex-col"><Tabs tabs={Object.keys(values)} active={tab} onChange={setTab} /><div className="relative min-h-0 flex-1"><Btn size="xs" variant="ghost" icon={Copy} className="absolute right-3 top-3 z-10" onClick={() => navigator.clipboard.writeText(values[tab])}>复制</Btn><pre className="h-full overflow-auto whitespace-pre-wrap bg-[#f7f9fb] p-4 font-mono text-xs leading-6">{values[tab] || "暂无内容"}</pre></div></div>
}

function StructureWorkspace({ workspace, structure, onSelect, inputRef, onUpload }: {
  workspace: ModelingWorkspace
  structure: ModelingStructure | null
  onSelect: (id: string) => void
  inputRef: React.RefObject<HTMLInputElement | null>
  onUpload: (file: File) => void
}) {
  return <div className="grid h-full min-h-[520px] grid-cols-1 lg:grid-cols-[220px_minmax(0,1fr)]"><div className="border-r border-border p-3"><input ref={inputRef} className="hidden" type="file" data-structure-upload data-supported-files=".cif,.vasp,POSCAR" onChange={(event) => event.target.files?.[0] && onUpload(event.target.files[0])} /><Btn icon={Upload} className="w-full" onClick={() => inputRef.current?.click()}>上传结构</Btn><div className="mt-3 space-y-2">{workspace.structures.map((item) => <button key={item.id} onClick={() => onSelect(item.id)} className={`w-full rounded-md border px-2 py-2 text-left text-xs ${workspace.activeStructureId === item.id ? "border-primary bg-primary-soft" : "border-border"}`}><p className="truncate font-medium">{item.fileName}</p><p className="mt-1 text-[10px] text-muted-foreground">{item.format}</p></button>)}{workspace.structures.length === 0 && <p className="py-6 text-center text-xs text-muted-foreground">暂无验证结构</p>}</div></div><div className="min-h-0">{structure?.geometry ? <StructureViewer geometry={structure.geometry} /> : <NoDataState title="请选择已解析结构" />}</div></div>
}

function RiskView({ values }: { values: string[] }) {
  if (!values.length) return <NoDataState title="暂无风险记录" />
  return <div className="space-y-2 p-4">{values.map((value) => <div key={value} className="flex gap-2 border-b border-border pb-2 text-xs leading-6"><AlertTriangle className="mt-1 size-4 shrink-0 text-warning" />{value}</div>)}</div>
}

function RevisionView({ workspace }: { workspace: ModelingWorkspace }) {
  if (!workspace.revisions.length) return <NoDataState title="尚无人工修订" />
  return <div className="space-y-3 p-4">{workspace.revisions.map((revision) => <div key={revision.id}><div className="mb-2 flex items-center gap-2"><Tag tone="blue">v{revision.revision}</Tag><span className="text-xs text-muted-foreground">{new Date(revision.createdAt).toLocaleString()}</span></div><div className="space-y-1">{revision.diff.map((item) => <div key={item.path} className="grid grid-cols-[180px_1fr_24px_1fr] items-center gap-2 border-b border-border py-1 text-xs"><code>{item.path}</code><span className="truncate text-danger">{String(item.before ?? "∅")}</span><span>→</span><span className="truncate text-success">{String(item.after ?? "∅")}</span></div>)}</div></div>)}</div>
}

function JsonView({ value, empty }: { value: unknown; empty: string }) {
  if (!value || (typeof value === "object" && Object.keys(value).length === 0)) return <NoDataState title={empty} />
  return <pre className="whitespace-pre-wrap p-4 text-xs leading-6">{JSON.stringify(value, null, 2)}</pre>
}

function Meta({ label, value }: { label: string; value: string }) {
  return <div><p className="text-muted-foreground">{label}</p><p className="mt-1 truncate font-medium">{value}</p></div>
}

function cloneFields(value: Record<string, ModelingField>) {
  return Object.fromEntries(Object.entries(value).map(([key, field]) => [key, { ...field, warnings: [...(field.warnings ?? [])] }]))
}

function coerceValue(value: string, original: ModelingField["value"]) {
  if (typeof original === "number") return Number.isFinite(Number(value)) ? Number(value) : value
  if (typeof original === "boolean") return value.toLowerCase() === "true"
  return value
}

function sourceTone(source: string): "blue" | "purple" | "green" | "orange" | "gray" {
  if (source === "user") return "green"
  if (source === "hypothesis") return "purple"
  if (source === "literature" || source === "manual") return "blue"
  if (source === "default") return "gray"
  return "orange"
}

function moveItem<T>(values: T[], from: number, to: number) {
  if (to < 0 || to >= values.length || from === to) return values
  const next = [...values]
  const [item] = next.splice(from, 1)
  next.splice(to, 0, item)
  return next
}

function sourcePayload(type: SourceType, text: string, documentId: number, hypothesisId: number) {
  if (type === "manual") return text.trim() ? { type, text: text.trim() } : null
  if (type === "document") return documentId > 0 ? { type, documentId } : null
  return hypothesisId > 0 ? { type, hypothesisId } : null
}

function replaceWorkspace(
  updated: ModelingWorkspace,
  setWorkspace: React.Dispatch<React.SetStateAction<ModelingWorkspace | null>>,
  setWorkspaces: React.Dispatch<React.SetStateAction<ModelingWorkspace[]>>,
) {
  setWorkspace(updated)
  setWorkspaces((values) => values.some((value) => value.id === updated.id)
    ? values.map((value) => value.id === updated.id ? updated : value)
    : [updated, ...values])
}

function errorMessage(value: unknown) {
  return value instanceof Error ? value.message : "请求失败"
}
