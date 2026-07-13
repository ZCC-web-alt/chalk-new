"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import { Check, Code2, LoaderCircle, MessageSquareText, ShieldCheck, SkipForward, X } from "lucide-react"
import type { HypothesisFeedbackPayload, HypothesisFeedbackPrompt } from "@/lib/api/types"
import { Checkbox, Btn, FieldLabel, Select, Tabs, Tag, Textarea, Toggle } from "../ui"
import { StructuredDataView } from "./hypothesis-detail"

const REVIEW_TABS = ["评审", "辩论", "证据", "历史"] as const
type ReviewTab = (typeof REVIEW_TABS)[number]

type StructuredState = {
  citationAuthenticity: string
  overExtension: string
  falsifiability: string
  experimentFeasibility: string
  baselineNeed: string
  referenceReplacement: string
}

const EMPTY_STRUCTURED: StructuredState = {
  citationAuthenticity: "",
  overExtension: "",
  falsifiability: "",
  experimentFeasibility: "",
  baselineNeed: "",
  referenceReplacement: "",
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value)
}

export function HypothesisReviewDialog({
  prompt,
  submitting,
  error,
  onSubmit,
  onCancel,
}: {
  prompt: HypothesisFeedbackPrompt
  submitting: boolean
  error: string | null
  onSubmit: (payload: HypothesisFeedbackPayload) => Promise<void>
  onCancel: () => void
}) {
  const [tab, setTab] = useState<ReviewTab>("评审")
  const [feedbackText, setFeedbackText] = useState("")
  const [editEnabled, setEditEnabled] = useState(false)
  const [editedJson, setEditedJson] = useState("")
  const [jsonError, setJsonError] = useState<string | null>(null)
  const [stance, setStance] = useState<"neutral" | "devil" | "optimist" | "custom">("neutral")
  const [selectedAttacks, setSelectedAttacks] = useState<Set<number>>(new Set())
  const [structured, setStructured] = useState<StructuredState>(EMPTY_STRUCTURED)
  const cancelRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    setTab("评审")
    setFeedbackText("")
    setEditEnabled(false)
    setEditedJson(JSON.stringify(prompt.hypothesis, null, 2))
    setJsonError(null)
    setStance("neutral")
    setSelectedAttacks(new Set())
    setStructured(EMPTY_STRUCTURED)
    cancelRef.current?.focus()
  }, [prompt])

  const attacks = useMemo(() => {
    const value = prompt.debate.devilAttackPoints
    return Array.isArray(value) ? value : []
  }, [prompt.debate])

  const reviewValue = tab === "评审"
    ? prompt.critique
    : tab === "辩论"
      ? prompt.debate
      : tab === "证据"
        ? { evidenceSummary: prompt.evidenceSummary, reviewContext: prompt.reviewContext }
        : prompt.history

  function toggleAttack(index: number) {
    setSelectedAttacks((current) => {
      const next = new Set(current)
      if (next.has(index)) next.delete(index)
      else next.add(index)
      return next
    })
  }

  async function submit(action: "approve" | "revise" | "skip") {
    setJsonError(null)
    let editedHypothesis: Record<string, unknown> | undefined
    if (editEnabled && action !== "skip") {
      try {
        const parsed = JSON.parse(editedJson)
        if (!isRecord(parsed)) throw new Error("JSON 顶层必须是对象")
        editedHypothesis = parsed
      } catch (parseError) {
        setJsonError(parseError instanceof Error ? parseError.message : "JSON 格式无效")
        return
      }
    }
    const structuredFeedback = Object.fromEntries(
      Object.entries(structured).filter(([, value]) => value),
    ) as HypothesisFeedbackPayload["structuredFeedback"]
    await onSubmit({
      action,
      ...(feedbackText.trim() ? { feedbackText: feedbackText.trim() } : {}),
      ...(editedHypothesis ? { editedHypothesis } : {}),
      debateStance: stance,
      selectedAttackIndices: [...selectedAttacks].sort((a, b) => a - b),
      structuredFeedback,
    })
  }

  return (
    <div data-testid="hypothesis-review-dialog" className="fixed inset-0 z-50 flex flex-col bg-background" role="dialog" aria-modal="true" aria-label="假设人工审核">
      <header className="flex min-h-12 shrink-0 flex-wrap items-center gap-2 border-b border-border bg-card px-3 py-2">
        <ShieldCheck className="size-5 text-primary" />
        <h2 className="text-sm font-semibold">假设人工审核</h2>
        <Tag tone="purple">{prompt.stage === "initial" ? "初始假设" : `第 ${prompt.round} 轮`}</Tag>
        <span className="text-xs text-muted-foreground">审核结果会写入完整 HITL 轨迹</span>
        <button
          ref={cancelRef}
          type="button"
          className="ml-auto inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-xs font-medium text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground disabled:pointer-events-none disabled:opacity-50"
          onClick={onCancel}
          disabled={submitting}
        >
          <X className="size-3.5" />取消任务
        </button>
      </header>

      <main className="grid min-h-0 flex-1 grid-cols-1 overflow-auto lg:grid-cols-[minmax(0,1.35fr)_minmax(360px,0.65fr)] lg:overflow-hidden">
        <section className="min-h-0 border-b border-border lg:border-b-0 lg:border-r">
          <div className="flex items-center gap-3 border-b border-border px-3 py-2">
            <h3 className="text-[13px] font-semibold">当前假设</h3>
            <div className="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
              <Code2 className="size-4" />JSON 编辑<Toggle checked={editEnabled} onChange={setEditEnabled} />
            </div>
          </div>
          <div className="max-h-[48vh] overflow-auto p-4 lg:max-h-none lg:h-[calc(100vh-13.5rem)]">
            {editEnabled ? (
              <div>
                <Textarea
                  aria-label="编辑假设 JSON"
                  className="min-h-[420px] font-mono text-xs"
                  value={editedJson}
                  onChange={(event) => setEditedJson(event.target.value)}
                />
                {jsonError && <p className="mt-2 text-xs text-danger">{jsonError}</p>}
              </div>
            ) : (
              <StructuredDataView value={prompt.hypothesis} />
            )}
          </div>
        </section>

        <section className="flex min-h-0 flex-col">
          <Tabs tabs={[...REVIEW_TABS]} active={tab} onChange={(value) => setTab(value as ReviewTab)} />
          <div className="min-h-48 flex-1 overflow-auto p-3">
            <StructuredDataView value={reviewValue} />
          </div>
          {attacks.length > 0 && (
            <div className="border-t border-border p-3">
              <FieldLabel>重点回应的攻击点</FieldLabel>
              <div className="max-h-28 space-y-2 overflow-auto">
                {attacks.map((attack, index) => (
                  <Checkbox
                    key={index}
                    checked={selectedAttacks.has(index)}
                    onChange={() => toggleAttack(index)}
                    label={<span className="line-clamp-2 text-xs">{isRecord(attack) ? String(attack.target || attack.evidence || `攻击点 ${index + 1}`) : String(attack)}</span>}
                  />
                ))}
              </div>
            </div>
          )}
        </section>
      </main>

      <footer className="shrink-0 border-t border-border bg-card p-3">
        <div className="grid gap-3 xl:grid-cols-[1fr_520px]">
          <div>
            <FieldLabel>审核意见</FieldLabel>
            <Textarea
              rows={3}
              placeholder="填写需要保留、修订或补强的科学依据"
              value={feedbackText}
              onChange={(event) => setFeedbackText(event.target.value)}
            />
          </div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            <ReviewSelect label="引用真实性" value={structured.citationAuthenticity} onChange={(value) => setStructured((current) => ({ ...current, citationAuthenticity: value }))} options={[["verified", "已核验"], ["needsVerification", "需核验"], ["suspectedInvalid", "疑似无效"]]} />
            <ReviewSelect label="过度外推" value={structured.overExtension} onChange={(value) => setStructured((current) => ({ ...current, overExtension: value }))} options={[["none", "无"], ["minor", "轻微"], ["major", "明显"]]} />
            <ReviewSelect label="可证伪性" value={structured.falsifiability} onChange={(value) => setStructured((current) => ({ ...current, falsifiability: value }))} options={[["falsifiable", "可证伪"], ["needsCriteria", "需判据"], ["notFalsifiable", "不可证伪"]]} />
            <ReviewSelect label="实验可行性" value={structured.experimentFeasibility} onChange={(value) => setStructured((current) => ({ ...current, experimentFeasibility: value }))} options={[["feasible", "可行"], ["needsAdjustment", "需调整"], ["infeasible", "不可行"]]} />
            <ReviewSelect label="基线需求" value={structured.baselineNeed} onChange={(value) => setStructured((current) => ({ ...current, baselineNeed: value }))} options={[["none", "无需"], ["recommended", "建议"], ["required", "必须"]]} />
            <ReviewSelect label="文献替换" value={structured.referenceReplacement} onChange={(value) => setStructured((current) => ({ ...current, referenceReplacement: value }))} options={[["none", "无需"], ["recommended", "建议"], ["required", "必须"]]} />
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <MessageSquareText className="size-4 text-muted-foreground" />
          <Select value={stance} onChange={(event) => setStance(event.target.value as typeof stance)} aria-label="辩论立场">
            <option value="neutral">中立立场</option>
            <option value="devil">反方立场</option>
            <option value="optimist">正方立场</option>
            <option value="custom">自定义立场</option>
          </Select>
          {error && <span className="text-xs text-danger">{error}</span>}
          {submitting && <LoaderCircle className="ml-auto size-4 animate-spin text-primary" />}
          <Btn className={submitting ? "" : "ml-auto"} icon={SkipForward} onClick={() => void submit("skip")} disabled={submitting}>跳过</Btn>
          <Btn variant="purple" icon={MessageSquareText} onClick={() => void submit("revise")} disabled={submitting}>提交修订</Btn>
          <Btn data-testid="approve-hypothesis" variant="primary" icon={Check} onClick={() => void submit("approve")} disabled={submitting}>批准并继续</Btn>
        </div>
      </footer>
    </div>
  )
}

function ReviewSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  options: Array<[string, string]>
}) {
  return (
    <label className="min-w-0">
      <span className="mb-1 block text-[11px] text-muted-foreground">{label}</span>
      <Select className="w-full" value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">未选择</option>
        {options.map(([option, text]) => <option key={option} value={option}>{text}</option>)}
      </Select>
    </label>
  )
}
