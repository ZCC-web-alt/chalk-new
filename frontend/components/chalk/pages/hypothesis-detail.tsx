"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import {
  Download,
  Eye,
  FileJson,
  FileText,
  LoaderCircle,
  PackageOpen,
  RefreshCw,
  X,
} from "lucide-react"
import { apiRequest } from "@/lib/api/client"
import { createJob, waitForJob } from "@/lib/api/jobs"
import type { HypothesisArtifact, HypothesisDetail, Job } from "@/lib/api/types"
import { cn } from "@/lib/utils"
import { NoDataState } from "../api-state"
import { Btn, Tabs, Tag } from "../ui"

const DETAIL_TABS = [
  "假设",
  "实验设计",
  "引用",
  "迭代",
  "评审",
  "推理链",
  "辩论",
  "HITL",
  "多模态",
  "验证",
  "树搜索",
  "Workflow",
] as const

type DetailTab = (typeof DETAIL_TABS)[number]

const LABELS: Record<string, string> = {
  paperTitle: "标题",
  paperAbstract: "摘要",
  problemStatement: "研究问题",
  hypothesis: "核心假设",
  rationale: "科学依据",
  technicalDetails: "技术路线",
  methods: "方法",
  experiments: "实验方案",
  experimentalDesign: "实验设计",
  expectedResults: "预期结果",
  references: "参考文献",
  confidence: "置信度",
  feasibility: "可行性",
  limitations: "局限性",
  warnings: "警告",
  steps: "步骤",
  status: "状态",
  summary: "摘要",
  interactionHistory: "交互历史",
  feedbackRecords: "反馈记录",
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value)
}

function humanizeKey(key: string) {
  if (LABELS[key]) return LABELS[key]
  return key
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/_/g, " ")
    .replace(/^./, (value) => value.toUpperCase())
}

function compactRecord(value: Record<string, unknown>) {
  return Object.fromEntries(
    Object.entries(value).filter(([, nested]) => {
      if (nested == null || nested === "") return false
      if (Array.isArray(nested)) return nested.length > 0
      if (isRecord(nested)) return Object.keys(nested).length > 0
      return true
    }),
  )
}

export function StructuredDataView({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (value == null || value === "") {
    return <p className="text-xs text-muted-foreground">暂无数据</p>
  }
  if (typeof value === "string") {
    return <p className="whitespace-pre-wrap break-words text-[13px] leading-7 text-foreground">{value}</p>
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return <span className="font-mono text-[13px] text-foreground">{String(value)}</span>
  }
  if (depth > 10) {
    return <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words font-mono text-xs text-foreground">{JSON.stringify(value, null, 2).slice(0, 8000)}</pre>
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return <p className="text-xs text-muted-foreground">暂无数据</p>
    return (
      <div className="divide-y divide-border border-y border-border">
        {value.map((item, index) => (
          <div key={index} className="grid grid-cols-[28px_1fr] gap-2 py-3">
            <span className="flex size-6 items-center justify-center rounded-sm bg-secondary font-mono text-[10px] text-muted-foreground">
              {index + 1}
            </span>
            <div className="min-w-0"><StructuredDataView value={item} depth={depth + 1} /></div>
          </div>
        ))}
      </div>
    )
  }
  if (isRecord(value)) {
    const entries = Object.entries(compactRecord(value))
    if (entries.length === 0) return <p className="text-xs text-muted-foreground">暂无数据</p>
    return (
      <div className={cn("divide-y divide-border", depth === 0 && "border-y border-border")}>
        {entries.map(([key, nested]) => {
          const nestedStructure = Array.isArray(nested) || isRecord(nested)
          return (
            <section
              key={key}
              className={cn(
                "py-3",
                nestedStructure ? "space-y-2" : "grid gap-2 sm:grid-cols-[150px_1fr]",
              )}
            >
              <h4 className="text-xs font-semibold text-muted-foreground">{humanizeKey(key)}</h4>
              <div className="min-w-0"><StructuredDataView value={nested} depth={depth + 1} /></div>
            </section>
          )
        })}
      </div>
    )
  }
  return <p className="text-[13px] text-foreground">{String(value)}</p>
}

function tabValue(detail: HypothesisDetail, tab: DetailTab) {
  const hypothesis = detail.hypothesis
  if (tab === "假设") {
    const preferred = compactRecord({
      paperTitle: hypothesis.paperTitle,
      problemStatement: hypothesis.problemStatement,
      hypothesis: hypothesis.hypothesis,
      rationale: hypothesis.rationale,
      paperAbstract: hypothesis.paperAbstract,
      confidence: hypothesis.confidence ?? detail.confidence,
      feasibility: hypothesis.feasibility ?? detail.feasibility,
      novelty: hypothesis.novelty,
      limitations: hypothesis.limitations,
    })
    return Object.keys(preferred).length > 0 ? preferred : hypothesis
  }
  if (tab === "实验设计") {
    return compactRecord({
      technicalDetails: hypothesis.technicalDetails,
      methods: hypothesis.methods,
      experimentalDesign: hypothesis.experimentalDesign ?? hypothesis.experiments,
      datasets: hypothesis.datasets,
      expectedResults: hypothesis.expectedResults,
      validationPlan: hypothesis.validationPlan,
    })
  }
  if (tab === "引用") {
    return compactRecord({
      references: hypothesis.references ?? hypothesis.citations,
      scientificEvidence: detail.scientificEvidence,
      sourceDocuments: detail.sourceDocuments,
    })
  }
  if (tab === "迭代") return detail.iterations
  if (tab === "评审") return detail.critiqueHistory
  if (tab === "推理链") return detail.reasoningChain
  if (tab === "辩论") return detail.debateHistory
  if (tab === "HITL") return detail.hitl
  if (tab === "多模态") {
    return compactRecord({
      multimodalEvidence: detail.multimodalEvidence,
      quantitativeReport: detail.quantitativeReport,
      sourceRuns: detail.multimodalRuns,
    })
  }
  if (tab === "验证") return detail.verification
  if (tab === "树搜索") {
    return hypothesis.hypothesisTree ?? hypothesis.treeSearch ?? hypothesis.hypothesisEvolution ?? null
  }
  return detail.workflow
}

function artifactByKind(artifacts: HypothesisArtifact[], kind: string) {
  return artifacts.find((artifact) => artifact.kind === kind) ?? null
}

export function HypothesisDetailView({
  detail,
  onRefresh,
}: {
  detail: HypothesisDetail
  onRefresh: () => Promise<void>
}) {
  const [tab, setTab] = useState<DetailTab>("假设")
  const [artifactJob, setArtifactJob] = useState<Job | null>(null)
  const [artifactError, setArtifactError] = useState<string | null>(null)
  const [reportHtml, setReportHtml] = useState<string | null>(null)
  const [previewOpen, setPreviewOpen] = useState(false)
  const artifactAbortRef = useRef<AbortController | null>(null)
  const value = useMemo(() => tabValue(detail, tab), [detail, tab])
  const report = artifactByKind(detail.artifacts, "interactive_report_html")
  const trace = artifactByKind(detail.artifacts, "agent_trace_json")
  const workflow = artifactByKind(detail.artifacts, "workflow_package_zip")

  useEffect(() => {
    setPreviewOpen(false)
    setReportHtml(null)
    artifactAbortRef.current?.abort()
    return () => artifactAbortRef.current?.abort()
  }, [detail.id])

  async function downloadArtifact(artifact: HypothesisArtifact) {
    setArtifactError(null)
    try {
      const response = await apiRequest(`/hypotheses/${detail.id}/artifacts/${artifact.id}`)
      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement("a")
      anchor.href = url
      anchor.download = artifact.fileName
      anchor.click()
      URL.revokeObjectURL(url)
    } catch (error) {
      setArtifactError(error instanceof Error ? error.message : "资源下载失败")
    }
  }

  async function previewReport() {
    if (!report) return
    setArtifactError(null)
    try {
      const response = await apiRequest(`/hypotheses/${detail.id}/artifacts/${report.id}`)
      setReportHtml(await response.text())
      setPreviewOpen(true)
    } catch (error) {
      setArtifactError(error instanceof Error ? error.message : "报告加载失败")
    }
  }

  async function runArtifactJob(type: "hypothesis_report" | "hypothesis_workflow_export") {
    setArtifactError(null)
    artifactAbortRef.current?.abort()
    const controller = new AbortController()
    artifactAbortRef.current = controller
    try {
      const created = await createJob(type, {
        hypothesisId: detail.id,
        ...(type === "hypothesis_report" ? { enhance: true } : {}),
      })
      const completed = await waitForJob(created, {
        signal: controller.signal,
        intervalMs: 700,
        onUpdate: setArtifactJob,
      })
      if (completed.status !== "SUCCEEDED") {
        throw new Error(completed.error?.message || "资源生成失败")
      }
      await onRefresh()
      setArtifactJob(null)
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return
      setArtifactError(error instanceof Error ? error.message : "资源生成失败")
      setArtifactJob(null)
    } finally {
      if (artifactAbortRef.current === controller) artifactAbortRef.current = null
    }
  }

  return (
    <div data-testid="hypothesis-detail" className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
        <Tag tone={detail.status === "approved" ? "green" : detail.status === "rejected" ? "red" : "blue"}>
          {detail.status}
        </Tag>
        {detail.domain && <Tag tone="gray">{detail.domain}</Tag>}
        <span className="text-xs text-muted-foreground">置信度 {detail.confidence}/10</span>
        <span className="text-xs text-muted-foreground">迭代 {detail.iterationCount}</span>
        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          <Btn data-testid="preview-hypothesis-report" size="xs" icon={report ? Eye : FileText} onClick={() => report ? void previewReport() : void runArtifactJob("hypothesis_report")}>
            {report ? "预览报告" : "生成报告"}
          </Btn>
          {trace && <Btn size="xs" icon={FileJson} onClick={() => void downloadArtifact(trace)}>下载 Trace</Btn>}
          <Btn size="xs" icon={workflow ? Download : PackageOpen} onClick={() => workflow ? void downloadArtifact(workflow) : void runArtifactJob("hypothesis_workflow_export")}>
            {workflow ? "下载 Workflow" : "生成 Workflow"}
          </Btn>
          <Btn size="xs" variant="ghost" icon={RefreshCw} onClick={() => void onRefresh()} aria-label="刷新假设详情" />
        </div>
      </div>
      {artifactJob && (
        <div className="flex items-center gap-2 border-b border-border bg-secondary/50 px-3 py-2 text-xs text-muted-foreground">
          <LoaderCircle className="size-4 animate-spin text-primary" />
          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-secondary">
            <div className="h-full bg-primary transition-all" style={{ width: `${artifactJob.progress}%` }} />
          </div>
          <span className="tabular-nums">{artifactJob.progress}%</span>
          <span className="max-w-72 truncate">{artifactJob.message}</span>
        </div>
      )}
      {artifactError && <div className="border-b border-danger/30 bg-danger-soft px-3 py-2 text-xs text-danger">{artifactError}</div>}
      <Tabs tabs={[...DETAIL_TABS]} active={tab} onChange={(next) => setTab(next as DetailTab)} />
      <div className="min-h-0 flex-1 overflow-auto p-4">
        {(value == null || (Array.isArray(value) && value.length === 0) || (isRecord(value) && Object.keys(value).length === 0)) ? (
          <NoDataState title="暂无相关数据" />
        ) : (
          <StructuredDataView value={value} />
        )}
      </div>

      {previewOpen && reportHtml && (
        <div className="fixed inset-0 z-50 flex flex-col bg-background" role="dialog" aria-modal="true" aria-label="交互式假设报告">
          <header className="flex h-12 shrink-0 items-center gap-2 border-b border-border bg-card px-3">
            <FileText className="size-4 text-primary" />
            <h2 className="min-w-0 flex-1 truncate text-sm font-semibold">{detail.title}</h2>
            {report && <Btn size="xs" icon={Download} onClick={() => void downloadArtifact(report)}>下载 HTML</Btn>}
            <Btn size="xs" variant="ghost" icon={X} onClick={() => setPreviewOpen(false)} aria-label="关闭报告预览" />
          </header>
          <iframe
            data-testid="hypothesis-report-frame"
            className="min-h-0 flex-1 bg-white"
            title="交互式假设报告"
            sandbox="allow-scripts allow-downloads"
            srcDoc={reportHtml}
          />
        </div>
      )}
    </div>
  )
}
