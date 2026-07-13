"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  FileText,
  Lightbulb,
  LoaderCircle,
  Network,
  Play,
  Search,
  ShieldCheck,
  WandSparkles,
  X,
} from "lucide-react"
import { ApiError, apiFetch, type Page } from "@/lib/api/client"
import {
  cancelJob,
  createJob,
  getJob,
  getLatestActiveJob,
  submitHypothesisFeedback,
  waitForJob,
} from "@/lib/api/jobs"
import { listMultimodalRuns } from "@/lib/api/science"
import type {
  DocumentRecord,
  HypothesisDetail,
  HypothesisFeedbackPayload,
  HypothesisGenerationResult,
  Job,
  MultimodalRun,
} from "@/lib/api/types"
import { cn } from "@/lib/utils"
import { ErrorState, LoadingState, NoDataState } from "../api-state"
import { Btn, Checkbox, FieldLabel, Input, Panel, Select, Stepper, Textarea, Toggle } from "../ui"
import { HypothesisDetailView } from "./hypothesis-detail"
import { HypothesisReviewDialog } from "./hypothesis-review-dialog"

const PIPELINE_STAGES = ["文献证据", "事实提取", "推理链", "假设生成", "批评修订", "多角色辩论", "科学验证", "报告与资源"]

const DOMAINS = [
  ["", "自动识别"],
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

function isActive(job: Job | null) {
  return Boolean(job && ["QUEUED", "RUNNING", "WAITING_FOR_FEEDBACK"].includes(job.status))
}

export function HypothesisPage({ seed }: { seed?: { id: number; text: string } | null }) {
  const [documents, setDocuments] = useState<DocumentRecord[]>([])
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [multimodalRuns, setMultimodalRuns] = useState<MultimodalRun[]>([])
  const [selectedMultimodal, setSelectedMultimodal] = useState<Set<string>>(new Set())
  const [documentQuery, setDocumentQuery] = useState("")
  const [question, setQuestion] = useState("")
  const [supplementalContext, setSupplementalContext] = useState("")
  const [domain, setDomain] = useState("")
  const [iterations, setIterations] = useState(2)
  const [hitlEnabled, setHitlEnabled] = useState(true)
  const [autoVerify, setAutoVerify] = useState(true)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [job, setJob] = useState<Job<HypothesisGenerationResult> | null>(null)
  const [detail, setDetail] = useState<HypothesisDetail | null>(null)
  const [feedbackSubmitting, setFeedbackSubmitting] = useState(false)
  const [feedbackError, setFeedbackError] = useState<string | null>(null)
  const [polishing, setPolishing] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    if (!seed?.text) return
    setSupplementalContext((current) => [current.trim(), seed.text.trim()].filter(Boolean).join("\n\n").slice(0, 40000))
  }, [seed])

  const loadDocuments = useCallback(async () => {
    const response = await apiFetch<Page<DocumentRecord>>("/documents?pageSize=100")
    setDocuments(response.data)
  }, [])

  const loadDetail = useCallback(async (hypothesisId: number) => {
    const loaded = await apiFetch<HypothesisDetail>(`/hypotheses/${hypothesisId}`)
    setDetail(loaded)
  }, [])

  const monitorJob = useCallback(async (initial: Job<HypothesisGenerationResult>) => {
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    setJob(initial)
    try {
      const completed = await waitForJob(initial, {
        signal: controller.signal,
        intervalMs: 700,
        onUpdate: setJob,
      })
      if (completed.status === "SUCCEEDED" && completed.result?.hypothesisId) {
        await loadDetail(completed.result.hypothesisId)
        setError(null)
      } else if (completed.status === "FAILED") {
        setError(completed.error?.message || "假设生成失败")
      } else if (completed.status === "CANCELLED") {
        setError("假设任务已取消")
      }
    } catch (monitorError) {
      if (monitorError instanceof DOMException && monitorError.name === "AbortError") return
      setError(monitorError instanceof Error ? monitorError.message : "任务状态读取失败")
    }
  }, [loadDetail])

  useEffect(() => {
    let active = true
    setLoading(true)
    setError(null)
    Promise.all([
      loadDocuments(),
      listMultimodalRuns().catch(() => ({ data: [], pagination: { page: 1, pageSize: 100, totalItems: 0, totalPages: 0 } })),
      getLatestActiveJob<HypothesisGenerationResult>("hypothesis_generate"),
    ])
      .then(([, runPage, activeJob]) => {
        if (active) setMultimodalRuns(runPage.data)
        if (active && activeJob) void monitorJob(activeJob)
      })
      .catch((loadError) => {
        if (active) setError(loadError instanceof Error ? loadError.message : "假设工作区加载失败")
      })
      .finally(() => { if (active) setLoading(false) })
    return () => {
      active = false
      abortRef.current?.abort()
    }
  }, [loadDocuments, monitorJob])

  const filteredDocuments = useMemo(() => {
    const query = documentQuery.trim().toLocaleLowerCase()
    if (!query) return documents
    return documents.filter((document) => document.title.toLocaleLowerCase().includes(query))
  }, [documentQuery, documents])

  const canStart = Boolean(question.trim() || supplementalContext.trim() || selected.size > 0) && !isActive(job)

  function toggleDocument(documentId: number) {
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(documentId)) {
        next.delete(documentId)
        setError(null)
      } else if (next.size >= 20) {
        setError("一次最多选择 20 篇文献")
      } else {
        next.add(documentId)
        setError(null)
      }
      return next
    })
  }

  async function startGeneration() {
    if (!canStart) return
    setError(null)
    setDetail(null)
    try {
      const created = await createJob<HypothesisGenerationResult>("hypothesis_generate", {
        researchQuestion: question.trim(),
        sourceDocIds: [...selected].sort((a, b) => a - b),
        supplementalContext: supplementalContext.trim(),
        domain,
        maxIterations: iterations,
        hitlEnabled,
        autoVerify,
        multimodalRunIds: [...selectedMultimodal],
      })
      void monitorJob(created)
    } catch (startError) {
      if (startError instanceof ApiError && startError.code === "HYPOTHESIS_JOB_ACTIVE") {
        const details = startError.details as { existingJobId?: string } | undefined
        if (details?.existingJobId) {
          try {
            const existing = await getJob<HypothesisGenerationResult>(details.existingJobId)
            void monitorJob(existing)
          } catch (recoveryError) {
            setError(recoveryError instanceof Error ? recoveryError.message : "活动任务恢复失败")
          }
          return
        }
      }
      setError(startError instanceof Error ? startError.message : "假设任务创建失败")
    }
  }

  async function polishQuestion() {
    const text = question.trim()
    if (!text || polishing) return
    setPolishing(true)
    setError(null)
    try {
      const created = await createJob<{ text: string }>("prompt_polish", { text })
      const completed = await waitForJob(created, { intervalMs: 700 })
      if (completed.status !== "SUCCEEDED" || !completed.result?.text) {
        throw new Error(completed.error?.message || "提示词润色失败")
      }
      setQuestion(completed.result.text)
    } catch (polishError) {
      setError(polishError instanceof Error ? polishError.message : "提示词润色失败")
    } finally {
      setPolishing(false)
    }
  }

  async function cancelCurrentJob() {
    if (!job) return
    try {
      const cancelled = await cancelJob<HypothesisGenerationResult>(job.id)
      setJob(cancelled)
      setFeedbackError(null)
    } catch (cancelError) {
      setError(cancelError instanceof Error ? cancelError.message : "任务取消失败")
    }
  }

  async function submitFeedback(payload: HypothesisFeedbackPayload) {
    if (!job) return
    setFeedbackSubmitting(true)
    setFeedbackError(null)
    try {
      const resumed = await submitHypothesisFeedback<HypothesisGenerationResult>(job.id, payload)
      setJob(resumed)
    } catch (submitError) {
      setFeedbackError(submitError instanceof Error ? submitError.message : "审核反馈提交失败")
    } finally {
      setFeedbackSubmitting(false)
    }
  }

  const completedStages = Math.min(PIPELINE_STAGES.length, Math.floor((job?.progress ?? 0) / (100 / PIPELINE_STAGES.length)))
  const running = isActive(job)

  return (
    <div className="grid min-h-0 grid-cols-1 gap-3 xl:h-full xl:grid-cols-[380px_1fr]">
      <div className="flex flex-col gap-3 xl:min-h-0 xl:overflow-y-auto">
        <Panel title="生成输入" icon={Lightbulb} className="shrink-0">
          <div className="space-y-3">
            <div>
              <FieldLabel>研究问题</FieldLabel>
              <Textarea
                data-testid="hypothesis-question"
                rows={3}
                maxLength={12000}
                placeholder="输入需要验证的科学问题"
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
              />
              <div className="mt-1 flex justify-end">
                <Btn size="xs" variant="ghost" icon={WandSparkles} disabled={!question.trim() || polishing || running} onClick={() => void polishQuestion()}>
                  {polishing ? "润色中" : "润色问题"}
                </Btn>
              </div>
            </div>
            <div>
              <FieldLabel>补充背景</FieldLabel>
              <Textarea
                rows={3}
                maxLength={40000}
                placeholder="可补充实验背景、约束条件或已有观察"
                value={supplementalContext}
                onChange={(event) => setSupplementalContext(event.target.value)}
              />
              <div className="mt-1 text-right text-[11px] tabular-nums text-muted-foreground">{supplementalContext.length.toLocaleString()} / 40,000</div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <FieldLabel>研究领域</FieldLabel>
                <Select className="w-full" value={domain} onChange={(event) => setDomain(event.target.value)}>
                  {DOMAINS.map(([value, label]) => <option key={value || "auto"} value={value}>{label}</option>)}
                </Select>
              </div>
              <div><FieldLabel>迭代轮数</FieldLabel><Stepper value={iterations} onChange={setIterations} min={1} max={5} /></div>
            </div>
            <div className="grid grid-cols-2 gap-3 border-y border-border py-2">
              <label className="flex items-center justify-between gap-2 text-xs text-foreground">人工审核<Toggle checked={hitlEnabled} onChange={setHitlEnabled} /></label>
              <label className="flex items-center justify-between gap-2 text-xs text-foreground">自动验证<Toggle checked={autoVerify} onChange={setAutoVerify} /></label>
            </div>
            <Btn data-testid="start-hypothesis" variant="purple" icon={running ? LoaderCircle : Play} className="w-full" disabled={!canStart} onClick={() => void startGeneration()}>
              {running ? "任务进行中" : "开始生成"}
            </Btn>
          </div>
        </Panel>

        <Panel
          title="来源文献"
          icon={FileText}
          className="min-h-64 shrink-0 xl:max-h-72"
          extra={<span className="text-xs tabular-nums text-muted-foreground">{selected.size} / 20</span>}
          bodyClassName="flex min-h-0 flex-col gap-2"
        >
          <Input icon={Search} placeholder="筛选文献" value={documentQuery} onChange={(event) => setDocumentQuery(event.target.value)} />
          <div className="min-h-0 flex-1 overflow-auto">
            {loading ? <LoadingState /> : documents.length === 0 ? <NoDataState title="暂无文献" /> : (
              <div className="divide-y divide-border">
                {filteredDocuments.map((document) => (
                  <div key={document.id} className="py-2">
                    <Checkbox
                      checked={selected.has(document.id)}
                      onChange={() => toggleDocument(document.id)}
                      label={<span className="line-clamp-2 text-xs leading-5">{document.title}</span>}
                    />
                  </div>
                ))}
              </div>
            )}
          </div>
        </Panel>

        <Panel
          title="多模态证据"
          icon={Network}
          className="max-h-52 shrink-0"
          extra={<span className="text-xs tabular-nums text-muted-foreground">{selectedMultimodal.size} / 20</span>}
          bodyClassName="overflow-auto"
        >
          {multimodalRuns.length === 0 ? <NoDataState title="暂无多模态分析" /> : (
            <div className="divide-y divide-border">
              {multimodalRuns.map((run) => (
                <div key={run.id} className="py-2">
                  <Checkbox
                    checked={selectedMultimodal.has(run.id)}
                    onChange={(checked) => setSelectedMultimodal((values) => {
                      const next = new Set(values)
                      if (checked && next.size < 20) next.add(run.id)
                      else if (!checked) next.delete(run.id)
                      return next
                    })}
                    label={(
                      <span className="min-w-0 text-xs">
                        <span className="block truncate">{run.result.items.map((item) => item.source.fileName).filter(Boolean).join("、") || "多模态分析"}</span>
                        <span className="text-[10px] text-muted-foreground">v{run.revision} · {run.result.summary.dataPoints} 数据点</span>
                      </span>
                    )}
                  />
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>

      <div className="flex flex-col gap-3 xl:min-h-0">
        <Panel title="工作流" icon={Network} className="shrink-0">
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
            {PIPELINE_STAGES.map((stage, index) => {
              const activeStage = running && index === completedStages
              const completed = index < completedStages || job?.status === "SUCCEEDED"
              return (
                <div key={stage} className={cn(
                  "flex min-h-10 items-center gap-2 rounded-md border px-2 py-2 text-xs",
                  completed ? "border-success/30 bg-success-soft text-success" : activeStage ? "border-primary/40 bg-primary-soft text-primary" : "border-border text-muted-foreground",
                )}>
                  <span className="flex size-5 shrink-0 items-center justify-center rounded-sm bg-card font-mono text-[10px]">{index + 1}</span>
                  <span>{stage}</span>
                </div>
              )
            })}
          </div>
          {job && (
            <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-3 text-xs">
              <LoaderCircle className={cn("size-4 text-primary", running && job.status !== "WAITING_FOR_FEEDBACK" && "animate-spin")} />
              <div className="h-1.5 min-w-32 flex-1 overflow-hidden rounded-full bg-secondary"><div className="h-full bg-primary transition-all" style={{ width: `${job.progress}%` }} /></div>
              <span className="tabular-nums text-foreground">{job.progress}%</span>
              <span className="max-w-80 truncate text-muted-foreground">{job.stage || job.message || job.status}</span>
              {job.status === "WAITING_FOR_FEEDBACK" && <span className="inline-flex items-center gap-1 text-purple"><ShieldCheck className="size-4" />等待审核</span>}
              {running && <Btn size="xs" variant="ghost" icon={X} onClick={() => void cancelCurrentJob()}>取消</Btn>}
            </div>
          )}
        </Panel>

        {error && <div className="border border-danger/30 bg-danger-soft px-3 py-2 text-xs text-danger">{error}</div>}
        {job?.result?.warnings && job.result.warnings.length > 0 && (
          <div className="border border-warning/40 bg-warning-soft px-3 py-2 text-xs text-[#ad6800]">
            {job.result.warnings.join("；")}
          </div>
        )}
        <Panel title={detail?.title || "假设结果"} icon={Lightbulb} className="min-h-96 xl:min-h-0 xl:flex-1" noPadding bodyClassName="flex min-h-0 flex-col">
          {detail ? (
            <HypothesisDetailView detail={detail} onRefresh={() => loadDetail(detail.id)} />
          ) : running ? (
            <div className="flex h-full min-h-72 items-center justify-center gap-2 text-sm text-muted-foreground"><LoaderCircle className="size-4 animate-spin" />正在生成假设</div>
          ) : error && !job ? (
            <ErrorState message={error} />
          ) : (
            <div data-testid="hypothesis-empty"><NoDataState title="尚未生成假设" /></div>
          )}
        </Panel>
      </div>

      {job?.status === "WAITING_FOR_FEEDBACK" && job.feedbackPrompt?.kind === "hypothesis_review" && (
        <HypothesisReviewDialog
          prompt={job.feedbackPrompt}
          submitting={feedbackSubmitting}
          error={feedbackError}
          onSubmit={submitFeedback}
          onCancel={() => void cancelCurrentJob()}
        />
      )}
    </div>
  )
}
