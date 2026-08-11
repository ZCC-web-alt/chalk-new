"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import { AlertTriangle, ArrowLeft, BookOpenCheck, CircleCheck, Download, FileText, Library, Pause, Play, RefreshCw, Search, Trash2 } from "lucide-react"
import { useRouter, useSearchParams } from "next/navigation"
import {
  createScience125Batch,
  createScience125BatchExport,
  createScience125ReportExport,
  deleteScience125Batch,
  getScience125Report,
  listScience125Batches,
  listScience125Reports,
  pauseScience125Batch,
  resumeScience125Batch,
  science125ExportUrl,
  type Science125BatchSummary,
  type Science125Report,
  type Science125ReportStatus,
  type Science125ReportSummary,
} from "@/lib/api/science125"
import { ErrorState, LoadingState, NoDataState } from "../api-state"
import { Btn, Input, Panel, Select, Tag } from "../ui"

const REPORT_STATUSES: Array<Science125ReportStatus | ""> = ["", "PENDING", "RETRIEVING", "BLOCKED_EVIDENCE", "GENERATING", "SUCCEEDED", "FAILED", "RETRYING"]
const PREPRODUCTION_QUESTION_IDS = [
  "S125-001", "S125-004", "S125-006", "S125-013", "S125-024",
  "S125-043", "S125-054", "S125-069", "S125-107", "S125-118",
]

function statusTone(status: string) {
  if (status === "SUCCEEDED") return "green" as const
  if (status === "FAILED" || status === "BLOCKED_EVIDENCE") return "red" as const
  if (status === "RUNNING" || status === "GENERATING" || status === "RETRIEVING") return "blue" as const
  return "gray" as const
}

function progressLabel(batch: Science125BatchSummary) {
  return `${batch.succeededCount}/${batch.totalCount} 成功 · ${batch.blockedEvidenceCount} 证据阻断 · ${batch.failedCount} 失败`
}

function openExport(exportId: string) {
  window.location.href = science125ExportUrl(exportId)
}

export function Science125ReportLibraryPage() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const [batches, setBatches] = useState<Science125BatchSummary[]>([])
  const [reports, setReports] = useState<Science125ReportSummary[]>([])
  const [activeReport, setActiveReport] = useState<Science125Report | null>(null)
  const [selectedReportIds, setSelectedReportIds] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState("")
  const [status, setStatus] = useState("")
  const [domain, setDomain] = useState("")
  const [activeBatchId, setActiveBatchId] = useState("")

  const load = useCallback(async () => {
    setError(null)
    const [batchRows, reportRows] = await Promise.all([
      listScience125Batches(),
      listScience125Reports({ batchId: activeBatchId || undefined, status: status || undefined, benchmarkDomain: domain || undefined, sortBy: "questionId", sortOrder: "asc" }),
    ])
    setBatches(batchRows)
    setReports(reportRows)
  }, [activeBatchId, domain, status])

  useEffect(() => {
    let active = true
    setLoading(true)
    load()
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "无法加载 Science 125 报告库") })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [load])

  useEffect(() => {
    const reportId = searchParams.get("report")
    if (!reportId) return
    let active = true
    getScience125Report(reportId)
      .then((report) => { if (active) setActiveReport(report) })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "无法打开 Science 125 报告") })
    return () => { active = false }
  }, [searchParams])

  useEffect(() => {
    if (!batches.some((batch) => batch.status === "RUNNING")) return
    const timer = window.setInterval(() => {
      void load().catch((reason) => setError(reason instanceof Error ? reason.message : "报告库刷新失败"))
    }, 2500)
    return () => window.clearInterval(timer)
  }, [batches, load])

  const visibleReports = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase()
    if (!needle) return reports
    return reports.filter((report) => [
      report.questionId,
      report.question,
      report.questionZh || "",
      report.benchmarkDomain,
      report.primarySubdomain,
      report.selectedHypothesisId || "",
    ].join(" ").toLocaleLowerCase().includes(needle))
  }, [query, reports])

  const domains = useMemo(() => Array.from(new Set(reports.map((report) => report.benchmarkDomain))).sort(), [reports])
  const activeBatch = useMemo(() => batches.find((batch) => batch.batchId === activeBatchId) || batches[0] || null, [activeBatchId, batches])
  const succeededReports = useMemo(() => reports.filter((report) => report.status === "SUCCEEDED"), [reports])
  const allCompletedSelected = succeededReports.length > 0 && succeededReports.every((report) => selectedReportIds.has(report.reportId))

  async function runAction(label: string, action: () => Promise<void>) {
    setBusy(label)
    setError(null)
    try {
      await action()
      await load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Science 125 报告库操作失败")
    } finally {
      setBusy(null)
    }
  }

  async function openReport(reportId: string) {
    await runAction("open-report", async () => {
      setActiveReport(await getScience125Report(reportId))
    })
  }

  async function exportReport(reportId: string, format: "docx" | "json") {
    await runAction(`export-report-${reportId}-${format}`, async () => {
      const artifact = await createScience125ReportExport(reportId, format)
      openExport(artifact.exportId)
    })
  }

  async function exportBatch(batchId: string, format: "docx" | "json") {
    await runAction(`export-batch-${batchId}-${format}`, async () => {
      const artifact = await createScience125BatchExport(batchId, format)
      openExport(artifact.exportId)
    })
  }

  async function deleteBatch(batchId: string) {
    if (!window.confirm("确定删除这个批次吗？该批次的题目报告和导出文件也会一并删除，此操作无法撤销。")) return
    await runAction(`delete-batch-${batchId}`, async () => {
      await deleteScience125Batch(batchId)
      setActiveBatchId("")
      setActiveReport(null)
      setSelectedReportIds(new Set())
    })
  }

  return <main className="min-h-screen bg-background text-foreground">
    <header className="border-b border-border bg-topbar text-topbar-foreground">
      <div className="mx-auto flex min-h-14 max-w-7xl items-center gap-3 px-4">
        <Btn size="xs" variant="ghost" icon={ArrowLeft} onClick={() => router.push("/research/general/new")}>返回工作台</Btn>
        <Library className="size-5 text-primary" />
        <span className="font-semibold">Science 125 假设报告库</span>
      </div>
    </header>

    <div className="mx-auto grid max-w-7xl gap-4 px-4 py-5 xl:grid-cols-[360px_minmax(0,1fr)]">
      <aside className="space-y-4">
        <Panel title="批次" icon={BookOpenCheck} bodyClassName="space-y-3">
          <Btn className="w-full" size="xs" icon={BookOpenCheck} disabled={Boolean(busy)} onClick={() => void runAction("create-preproduction", async () => { await createScience125Batch(PREPRODUCTION_QUESTION_IDS) })}>创建 10 题预生产批次</Btn>
          <Btn className="w-full" size="xs" variant="primary" icon={FileText} disabled={Boolean(busy)} onClick={() => void runAction("create-full", async () => { await createScience125Batch() })}>创建 125 题正式批次</Btn>
          <p className="text-xs leading-5 text-muted-foreground">预生产批次固定覆盖 10 道跨领域代表题，用于先验证证据获取、限频、费用和恢复流程。</p>
          {batches.length === 0 ? <NoDataState title="暂无批次" hint="创建批次后仍需手动点击开始；服务端会按题号顺序单并发执行。" /> : <div className="space-y-2">
            {batches.map((batch) => <button key={batch.batchId} type="button" onClick={() => setActiveBatchId(batch.batchId)} className={`w-full border px-3 py-2 text-left text-xs ${activeBatchId === batch.batchId || (!activeBatchId && batch === activeBatch) ? "border-primary/50 bg-primary-soft" : "border-border hover:bg-secondary"}`}>
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-[11px] text-primary">{batch.batchId.slice(0, 8)}</span>
                <Tag tone={statusTone(batch.status)}>{batch.status}</Tag>
              </div>
              <p className="mt-1 text-muted-foreground">{progressLabel(batch)}</p>
              <div className="mt-2 h-1.5 overflow-hidden rounded bg-secondary">
                <div className="h-full bg-primary" style={{ width: `${Math.round(batch.totalCount ? batch.succeededCount / batch.totalCount * 100 : 0)}%` }} />
              </div>
            </button>)}
          </div>}
          {activeBatch && <div className="grid grid-cols-2 gap-2 border-t border-border pt-3">
            <Btn size="xs" icon={Play} disabled={Boolean(busy) || activeBatch.status === "RUNNING"} onClick={() => void runAction("resume", async () => { await resumeScience125Batch(activeBatch.batchId) })}>开始/继续</Btn>
            <Btn size="xs" variant="ghost" icon={Pause} disabled={Boolean(busy) || activeBatch.status !== "RUNNING"} onClick={() => void runAction("pause", async () => { await pauseScience125Batch(activeBatch.batchId) })}>暂停</Btn>
            <Btn size="xs" variant="ghost" icon={Download} disabled={Boolean(busy)} onClick={() => void exportBatch(activeBatch.batchId, "json")}>导出 JSON</Btn>
            <Btn size="xs" variant="ghost" icon={Download} disabled={Boolean(busy) || activeBatch.totalCount !== 125 || activeBatch.succeededCount !== 125} onClick={() => void exportBatch(activeBatch.batchId, "docx")}>最终 DOCX</Btn>
            <Btn className="col-span-2" size="xs" variant="danger" icon={Trash2} disabled={Boolean(busy) || activeBatch.status === "RUNNING"} onClick={() => void deleteBatch(activeBatch.batchId)}>删除批次</Btn>
          </div>}
        </Panel>

        <Panel title="筛选" icon={Search} bodyClassName="space-y-3">
          <Input icon={Search} value={query} placeholder="搜索题号、题目、领域或假设 ID" onChange={(event) => setQuery(event.target.value)} />
          <Select value={status} onChange={(event) => setStatus(event.target.value)} aria-label="报告状态">
            {REPORT_STATUSES.map((item) => <option key={item || "all"} value={item}>{item || "全部状态"}</option>)}
          </Select>
          <Select value={domain} onChange={(event) => setDomain(event.target.value)} aria-label="领域">
            <option value="">全部领域</option>
            {domains.map((item) => <option key={item} value={item}>{item}</option>)}
          </Select>
          <div className="grid grid-cols-2 gap-2">
            <Btn size="xs" variant="ghost" icon={CircleCheck} onClick={() => setSelectedReportIds(new Set(succeededReports.map((report) => report.reportId)))}>全选已完成</Btn>
            <Btn size="xs" variant="ghost" icon={RefreshCw} onClick={() => void load()}>刷新</Btn>
          </div>
          <p className="text-xs text-muted-foreground">已选 {selectedReportIds.size} 个报告。最终 DOCX 仍以批次 125/125 成功为硬门禁。</p>
        </Panel>
      </aside>

      <section className="min-w-0 space-y-4">
        {loading ? <LoadingState label="正在加载报告库..." /> : error ? <ErrorState message={error} onRetry={() => void load()} /> : <>
          <Panel title="报告列表" icon={Library} bodyClassName="p-0">
            {visibleReports.length === 0 ? <NoDataState title="暂无报告" hint="批处理生成成功或证据阻断后，这里会显示单题报告。" className="py-12" /> : <div className="overflow-auto">
              <table className="min-w-full divide-y divide-border text-sm">
                <thead className="bg-secondary text-left text-xs text-muted-foreground">
                  <tr>
                    <th className="px-3 py-2">选择</th>
                    <th className="px-3 py-2">题目</th>
                    <th className="px-3 py-2">状态</th>
                    <th className="px-3 py-2">证据</th>
                    <th className="px-3 py-2">自动选中</th>
                    <th className="px-3 py-2">操作</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {visibleReports.map((report) => <tr key={report.reportId} className="align-top">
                    <td className="px-3 py-3">
                      <input
                        aria-label={`选择 ${report.questionId}`}
                        type="checkbox"
                        checked={selectedReportIds.has(report.reportId)}
                        onChange={(event) => setSelectedReportIds((current) => {
                          const next = new Set(current)
                          if (event.target.checked) next.add(report.reportId)
                          else next.delete(report.reportId)
                          return next
                        })}
                      />
                    </td>
                    <td className="max-w-xl px-3 py-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-xs text-primary">{report.questionId}</span>
                        <span className="text-xs text-muted-foreground">{report.benchmarkDomain} / {report.primarySubdomain}</span>
                        {report.sourceType === "interactive_job" && <Tag tone="green">交互任务</Tag>}
                      </div>
                      <p className="mt-1 leading-6">{report.questionZh || report.question}</p>
                      {report.questionZh && <p className="mt-1 text-xs leading-5 text-muted-foreground">{report.question}</p>}
                    </td>
                    <td className="px-3 py-3"><Tag tone={statusTone(report.status)}>{report.status}</Tag></td>
                    <td className="px-3 py-3 text-xs text-muted-foreground">{report.selectedEvidenceCount} 条 / {report.providerFamilies.length} 家族<br />{report.evidenceStatus}</td>
                    <td className="px-3 py-3 text-xs">
                      {report.selectedHypothesisId ? <><span className="font-medium text-foreground">{report.selectedHypothesisId}</span><br /><span className="text-muted-foreground">{Math.round((report.selectedHypothesisConfidence || 0) * 100)}%</span></> : <span className="text-muted-foreground">未选中</span>}
                    </td>
                    <td className="space-y-2 px-3 py-3">
                      <Btn size="xs" variant="ghost" icon={FileText} onClick={() => void openReport(report.reportId)}>查看</Btn>
                      <Btn size="xs" variant="ghost" icon={Download} disabled={report.status !== "SUCCEEDED"} onClick={() => void exportReport(report.reportId, "docx")}>DOCX</Btn>
                      <Btn size="xs" variant="ghost" icon={Download} onClick={() => void exportReport(report.reportId, "json")}>JSON</Btn>
                    </td>
                  </tr>)}
                </tbody>
              </table>
            </div>}
          </Panel>

          <ReportDetail report={activeReport} />

          {allCompletedSelected && <div className="border border-success/30 bg-success-soft px-3 py-2 text-xs text-success">已选中所有已完成报告；如果当前批次达到 125/125，可直接导出最终 DOCX。</div>}
        </>}
        {busy && <div role="status" className="fixed bottom-4 right-4 border border-border bg-card px-3 py-2 text-xs text-muted-foreground shadow-sm">处理中：{busy}</div>}
      </section>
    </div>
  </main>
}

function ReportDetail({ report }: { report: Science125Report | null }) {
  if (!report) return null
  const selectedEvidence = Array.isArray(report.evidenceSnapshot.selectedEvidence) ? report.evidenceSnapshot.selectedEvidence : []
  const hypotheses = report.researchOutput?.hypotheses || []
  const nullHypothesis = report.researchOutput?.nullHypothesis
  return <Panel title={`${report.questionId} 报告详情`} icon={FileText} bodyClassName="space-y-4">
    <div className="grid gap-4 lg:grid-cols-3">
      <div>
        <p className="text-xs text-muted-foreground">自动选中</p>
        <p className="mt-1 text-sm font-medium">{report.selectedHypothesisId || "未选中"} {report.selectedHypothesisConfidence != null ? `· ${Math.round(report.selectedHypothesisConfidence * 100)}%` : ""}</p>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">{report.selectedHypothesisReason || "暂无选择原因"}</p>
      </div>
      <div>
        <p className="text-xs text-muted-foreground">证据门禁</p>
        <p className="mt-1 text-sm font-medium">{report.selectedEvidenceCount} 条全文证据 / {report.providerFamilies.length} 个来源家族</p>
        <p className="mt-1 text-xs text-muted-foreground">快照 {report.evidenceSnapshotSha256.slice(0, 12)}</p>
      </div>
      <div>
        <p className="text-xs text-muted-foreground">Qwen 审计</p>
        <p className="mt-1 text-sm font-medium">{report.model || "未生成"}</p>
        <p className="mt-1 text-xs text-muted-foreground">{report.requestId || "无 requestId"}</p>
      </div>
    </div>

    <section className="border-t border-border pt-3">
      <h3 className="text-sm font-semibold">自动选择的文献</h3>
      {selectedEvidence.length === 0 ? <NoDataState title="没有合格证据" /> : <div className="mt-2 grid gap-2">
        {selectedEvidence.map((item, index) => {
          const row = item as { title?: string; provider?: string; stableId?: string; reasons?: string[] }
          return <article key={`${row.stableId || index}`} className="border border-border px-3 py-2 text-xs">
            <div className="flex flex-wrap items-center gap-2"><Tag tone="blue">{row.provider || "provider"}</Tag><span className="font-mono text-[11px] text-muted-foreground">{row.stableId}</span></div>
            <p className="mt-1 leading-5 text-foreground">{row.title}</p>
            <p className="mt-1 text-muted-foreground">{(row.reasons || []).join("；")}</p>
          </article>
        })}
      </div>}
    </section>

    <section className="border-t border-border pt-3">
      <h3 className="text-sm font-semibold">候选假设与 H0 对照</h3>
      {hypotheses.length === 0 ? <div className="mt-2 flex items-center gap-2 text-xs text-warning"><AlertTriangle className="size-4" />该报告还没有 research-v1 输出。</div> : <div className="mt-2 space-y-2">
        {hypotheses.map((hypothesis) => <article key={hypothesis.id} className={`border px-3 py-2 text-xs ${hypothesis.id === report.selectedHypothesisId ? "border-primary/50 bg-primary-soft" : "border-border"}`}>
          <div className="flex items-center justify-between gap-2">
            <h4 className="text-sm font-medium">{hypothesis.id} · {hypothesis.title}</h4>
            <span>{Math.round(hypothesis.confidence * 100)}%</span>
          </div>
          <p className="mt-1 leading-5 text-muted-foreground">{hypothesis.statement}</p>
        </article>)}
        {nullHypothesis && <article className="border border-border px-3 py-2 text-xs">
          <h4 className="text-sm font-medium">H0 · {nullHypothesis.title}</h4>
          <p className="mt-1 leading-5 text-muted-foreground">{nullHypothesis.statement}</p>
        </article>}
      </div>}
    </section>
  </Panel>
}
