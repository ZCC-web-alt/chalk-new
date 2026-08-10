"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import { ArrowLeft, ArrowRight, BookOpenCheck, CircleCheck, FileCheck2, FlaskConical, Lightbulb, Library, Search } from "lucide-react"
import { useRouter, useSearchParams } from "next/navigation"
import {
  getScience125QuestionContext,
  getScience125QuestionProfile,
  listScience125Questions,
  type Science125Question,
  type Science125QuestionContext,
  type Science125QuestionProfile,
} from "@/lib/api/science125"
import { ErrorState, LoadingState, NoDataState } from "../api-state"
import { Btn, Input, Panel, Select, Tag } from "../ui"
import { useAuth } from "../auth-provider"
import { buildScience125SearchQuery } from "@/lib/science125-search"
import { HypothesisPage } from "./hypothesis-generation"
import { SearchPage, type LiteratureReviewState } from "./literature-search"

type WorkspaceStage = "select" | "presearch" | "hypothesis"

const STAGES = ["选择题目", "文献预搜索", "人工审核与假设准备"]
const DOMAIN_ORDER = [
  "Mathematical Sciences",
  "Chemistry",
  "Medicine & Health",
  "Biology",
  "Astronomy",
  "Physics",
  "Engineering & Materials Science",
  "Information Science",
  "Neuroscience",
  "Ecology",
  "Energy Science",
  "Artificial Intelligence",
] as const
const DOMAIN_LABELS_ZH: Record<(typeof DOMAIN_ORDER)[number], string> = {
  "Mathematical Sciences": "数学科学",
  Chemistry: "化学",
  "Medicine & Health": "医学与健康",
  Biology: "生物学",
  Astronomy: "天文学",
  Physics: "物理学",
  "Engineering & Materials Science": "工程与材料科学",
  "Information Science": "信息科学",
  Neuroscience: "神经科学",
  Ecology: "生态学",
  "Energy Science": "能源科学",
  "Artificial Intelligence": "人工智能",
}
const EMPTY_REVIEW_STATE: LiteratureReviewState = {
  jobId: null,
  selectedResultIds: [],
  literatureContext: "",
  pageExcerpts: [],
  confirmed: false,
  restorationComplete: false,
}

function questionSeedId(question: Science125Question) {
  return Number.parseInt(question.id.slice(-3), 10)
}

function domainLabelZh(domain: string) {
  return DOMAIN_LABELS_ZH[domain as keyof typeof DOMAIN_LABELS_ZH] || domain
}

function bilingualDomainLabel(domain: string) {
  return `${domainLabelZh(domain)} / ${domain}`
}

export function Science125Workbench() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const { user } = useAuth()
  const [questions, setQuestions] = useState<Science125Question[]>([])
  const [optimisticQuestionId, setOptimisticQuestionId] = useState("")
  const [query, setQuery] = useState("")
  const [domain, setDomain] = useState("")
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [questionContext, setQuestionContext] = useState<Science125QuestionContext | null>(null)
  const [questionProfile, setQuestionProfile] = useState<Science125QuestionProfile | null>(null)
  const [contextLoading, setContextLoading] = useState(false)
  const [contextError, setContextError] = useState<string | null>(null)
  const [profileLoading, setProfileLoading] = useState(false)
  const [profileError, setProfileError] = useState<string | null>(null)
  const [evidenceContext, setEvidenceContext] = useState("")
  const [reviewState, setReviewState] = useState<LiteratureReviewState>(EMPTY_REVIEW_STATE)
  const selectedId = searchParams.get("question") || ""
  const activeQuestionId = selectedId || optimisticQuestionId
  const requestedStage = searchParams.get("stage")
  const requestedWorkspaceStage: WorkspaceStage = requestedStage === "presearch" || requestedStage === "hypothesis" ? requestedStage : "select"

  useEffect(() => {
    setOptimisticQuestionId((current) => current === selectedId ? current : selectedId)
  }, [selectedId])

  useEffect(() => {
    let active = true
    listScience125Questions()
      .then((catalog) => { if (active) setQuestions(catalog.data) })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "无法读取 Science 125 题目目录。") })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [])

  const domains = useMemo(() => [...DOMAIN_ORDER], [])
  const domainCounts = useMemo(() => DOMAIN_ORDER.reduce<Record<string, number>>((counts, item) => {
    counts[item] = questions.filter((question) => question.benchmarkDomain === item).length
    return counts
  }, {}), [questions])
  const selected = useMemo(() => questions.find((item) => item.id === activeQuestionId) || null, [activeQuestionId, questions])
  const hasAuthoritativeContext = Boolean(questionContext?.sourceContext.trim())
  const reviewedEvidenceCount = reviewState.selectedResultIds.length + reviewState.pageExcerpts.length
  const hasConfirmedReview = Boolean(
    reviewState.confirmed
    && reviewedEvidenceCount >= 3
    && hasAuthoritativeContext
  )
  const stage: WorkspaceStage = requestedWorkspaceStage === "hypothesis" && !hasConfirmedReview
    ? "presearch"
    : requestedWorkspaceStage
  const visibleQuestions = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase()
    return questions.filter((item) => (
      (!domain || item.benchmarkDomain === domain)
      && (!needle || [
        item.id,
        item.question,
        item.questionZh || "",
        item.benchmarkDomain,
        domainLabelZh(item.benchmarkDomain),
        item.primarySubdomain,
        ...item.crossDomainTags,
      ].join(" ").toLocaleLowerCase().includes(needle))
    ))
  }, [domain, query, questions])

  useEffect(() => {
    let active = true
    setQuestionContext(null)
    setContextError(null)
    if (!selected) {
      setContextLoading(false)
      return () => { active = false }
    }
    setContextLoading(true)
    getScience125QuestionContext(selected.id)
      .then((context) => {
        if (active) setQuestionContext(context)
      })
      .catch((reason) => {
        if (active) setContextError(reason instanceof Error ? reason.message : "无法读取题册完整上下文。")
      })
      .finally(() => {
        if (active) setContextLoading(false)
      })
    return () => { active = false }
  }, [selected])

  useEffect(() => {
    let active = true
    setQuestionProfile(null)
    setProfileError(null)
    if (!selected) {
      setProfileLoading(false)
      return () => { active = false }
    }
    setProfileLoading(true)
    getScience125QuestionProfile(selected.id)
      .then((profile) => { if (active) setQuestionProfile(profile) })
      .catch((reason) => {
        if (active) setProfileError(reason instanceof Error ? reason.message : "无法读取题目领域配置。")
      })
      .finally(() => { if (active) setProfileLoading(false) })
    return () => { active = false }
  }, [selected])

  const setWorkspaceRoute = useCallback((questionId?: string, nextStage: WorkspaceStage = "select") => {
    setOptimisticQuestionId(questionId || "")
    const params = new URLSearchParams()
    if (questionId) params.set("question", questionId)
    if (questionId && nextStage !== "select") params.set("stage", nextStage)
    const suffix = params.toString()
    router.push(`/research/general/new${suffix ? `?${suffix}` : ""}`)
  }, [router])

  useEffect(() => {
    if (!loading && selectedId && !selected) router.replace("/research/general/new")
  }, [loading, router, selected, selectedId])

  useEffect(() => {
    setEvidenceContext("")
    setReviewState(EMPTY_REVIEW_STATE)
  }, [activeQuestionId])

  useEffect(() => {
    if (!selected || requestedWorkspaceStage !== "hypothesis" || !reviewState.restorationComplete || (!questionContext && !contextError) || hasConfirmedReview) return
    router.replace(`/research/general/new?question=${encodeURIComponent(selected.id)}&stage=presearch`)
  }, [contextError, hasConfirmedReview, questionContext, requestedWorkspaceStage, reviewState.restorationComplete, router, selected])

  function chooseQuestion(question: Science125Question) {
    setWorkspaceRoute(question.id === activeQuestionId ? undefined : question.id)
  }

  function returnToSelection() {
    setWorkspaceRoute()
  }

  const selectionStorageKey = selected && user ? `chalk:science125:review-selection:${user.id}:${selected.id}` : undefined
  const updateEvidenceContext = useCallback((context: string) => setEvidenceContext(context), [])
  const enterHypothesis = useCallback((context: string, review: LiteratureReviewState) => {
    setEvidenceContext(context)
    setReviewState(review)
    if (selected) setWorkspaceRoute(selected.id, "hypothesis")
  }, [selected, setWorkspaceRoute])

  return <main className="min-h-screen bg-background text-foreground">
    <header className="border-b border-border bg-topbar text-topbar-foreground">
      <div className="mx-auto flex min-h-14 max-w-7xl items-center gap-3 px-4">
        <Btn aria-label="返回主页面" size="xs" variant="ghost" icon={ArrowLeft} onClick={() => router.push("/")}>返回主页面</Btn>
        <Btn size="xs" variant="ghost" icon={Library} onClick={() => router.push("/research/general/reports")}>报告库</Btn>
        <FlaskConical className="size-5 text-primary" />
        <span className="font-semibold">Chalk</span>
        <span className="hidden text-sm text-white/65 sm:inline">Science 125 跨学科假设</span>
      </div>
    </header>
    <div className="mx-auto max-w-7xl px-4 py-5">
      {loading ? <LoadingState label="正在加载 Science 125 题目..." /> : error ? <ErrorState message={error} onRetry={() => window.location.reload()} /> : <>
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">跨学科假设工作台</h1>
          <p className="mt-1 text-sm text-muted-foreground">仅处理 Science 125 权威题目，并沿用 Chalk 的文献检索、人工审核与假设生成流程。</p>
        </div>
        {selected && <Tag tone="blue">{selected.id}</Tag>}
      </div>
      <ol className="mb-5 grid grid-cols-1 gap-2 sm:grid-cols-3" aria-label="跨学科假设流程">
        {STAGES.map((label, index) => {
          const complete = (stage === "presearch" && index === 0) || (stage === "hypothesis" && index < 2)
          const current = (stage === "select" && index === 0) || (stage === "presearch" && index === 1) || (stage === "hypothesis" && index === 2)
          return <li className={`flex min-h-11 items-center gap-2 border px-3 text-sm ${current ? "border-primary/50 bg-primary-soft text-primary" : complete ? "border-success/40 bg-success-soft text-success" : "border-border text-muted-foreground"}`} key={label}>
            {complete ? <CircleCheck className="size-4" /> : <span className="flex size-5 items-center justify-center border border-current text-xs">{index + 1}</span>}
            {label}
          </li>
        })}
      </ol>
      {stage === "select" && <QuestionSelection
        domains={domains}
        domainCounts={domainCounts}
        domain={domain}
        query={query}
        questions={visibleQuestions}
        selected={selected}
        questionContext={questionContext}
        contextLoading={contextLoading}
        contextError={contextError}
        questionProfile={questionProfile}
        profileLoading={profileLoading}
        profileError={profileError}
        onDomain={setDomain}
        onQuery={setQuery}
        onSelect={chooseQuestion}
        onContinue={() => selected && setWorkspaceRoute(selected.id, "presearch")}
      />}
      {selected && stage !== "select" && <section className={stage === "presearch" ? "space-y-4" : "hidden"} aria-hidden={stage !== "presearch"}>
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-3">
          <div>
            <p className="text-xs text-muted-foreground">{selected.id} · {bilingualDomainLabel(selected.benchmarkDomain)} · {selected.primarySubdomain}</p>
            <h2 data-testid="science125-question-zh" className="text-base font-semibold">{selected.questionZh || selected.question}</h2>
            {selected.questionZh && <p data-testid="science125-question-en" className="mt-1 text-xs text-muted-foreground">权威英文原题：{selected.question}</p>}
          </div>
          <Btn size="xs" variant="ghost" icon={ArrowLeft} onClick={returnToSelection}>重新选择题目</Btn>
        </div>
        <AuthoritativeQuestionContext context={questionContext} loading={contextLoading} error={contextError} />
        {questionContext && <SearchPage
          key={selected.id}
          initialQuery={questionProfile?.recommendedQuery || buildScience125SearchQuery(selected.question, questionContext?.sourceContext || "")}
          initialQueryZh={questionProfile?.searchIntentZh || undefined}
          science125Id={selected.id}
          science125Profile={questionProfile}
          selectionStorageKey={selectionStorageKey}
          onSelectionChange={updateEvidenceContext}
          onReviewStateChange={setReviewState}
          onUseAsHypothesis={enterHypothesis}
        />}
      </section>}
      {stage === "hypothesis" && selected && <section className="flex min-h-[calc(100vh-15rem)] flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-3">
          <div><p className="text-xs text-muted-foreground">{selected.id} · {bilingualDomainLabel(selected.benchmarkDomain)} · {selected.primarySubdomain}</p><h2 className="text-base font-semibold">已完成文献人工筛选，准备假设生成输入</h2></div>
          <Btn size="xs" variant="ghost" icon={ArrowLeft} onClick={() => setWorkspaceRoute(selected.id, "presearch")}>返回文献预搜索</Btn>
        </div>
        <ReviewedInputSnapshot
          question={selected}
          context={questionContext}
          evidenceContext={evidenceContext}
          review={reviewState}
          profile={questionProfile}
        />
        <div className="min-h-0 flex-1">
          <HypothesisPage seed={{
            id: questionSeedId(selected),
            question: selected.questionZh || selected.question,
            questionLocked: true,
            domainLabel: bilingualDomainLabel(selected.benchmarkDomain),
            science125Id: selected.id,
            literatureSearchJobId: reviewState.jobId || undefined,
            reviewedEvidenceIds: reviewState.selectedResultIds,
            reviewedEvidenceCount,
            generationDisabled: !questionProfile?.pilotEnabled,
            generationBlockedReason: questionProfile
              ? "当前仅开放 S125-006、S125-043 和 S125-054 三道审计试点；其余题目完成代表题验证后再开放。"
              : "正在读取 Science 125 试点状态。",
            sourcePageSelections: reviewState.pageExcerpts.map((excerpt) => ({
              documentId: excerpt.documentId,
              pages: excerpt.pages,
              pdfSha256: excerpt.provenance.pdfSha256,
              textSha256: excerpt.hash,
              maxChars: excerpt.provenance.maxChars,
            })),
          }} />
        </div>
      </section>}
      </>}
    </div>
  </main>
}

function QuestionSelection({
  domains, domainCounts, domain, query, questions, selected, questionContext, contextLoading, contextError,
  questionProfile, profileLoading, profileError, onDomain, onQuery, onSelect, onContinue,
}: {
  domains: string[]
  domainCounts: Record<string, number>
  domain: string
  query: string
  questions: Science125Question[]
  selected: Science125Question | null
  questionContext: Science125QuestionContext | null
  contextLoading: boolean
  contextError: string | null
  questionProfile: Science125QuestionProfile | null
  profileLoading: boolean
  profileError: string | null
  onDomain: (value: string) => void
  onQuery: (value: string) => void
  onSelect: (question: Science125Question) => void
  onContinue: () => void
}) {
  return <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
    <Panel title="Science 125 题目" icon={BookOpenCheck} bodyClassName="space-y-3">
      <div className="grid gap-4 md:grid-cols-[210px_minmax(0,1fr)]">
        <nav aria-label="Science 125 一级领域" className="hidden space-y-1 md:block">
          <button type="button" aria-pressed={!domain} onClick={() => onDomain("")} className={`flex w-full items-center justify-between border px-3 py-2 text-left text-sm ${!domain ? "border-primary/50 bg-primary-soft text-primary" : "border-transparent text-muted-foreground hover:bg-secondary"}`}>
            <span>全部领域</span><span className="tabular-nums">{questions.length}</span>
          </button>
          {domains.map((item) => <button type="button" key={item} aria-pressed={domain === item} onClick={() => onDomain(item)} className={`flex min-h-12 w-full items-center justify-between gap-2 border px-3 py-2 text-left text-xs ${domain === item ? "border-primary/50 bg-primary-soft text-primary" : "border-transparent text-muted-foreground hover:bg-secondary"}`}>
            <span className="min-w-0"><span className="block text-sm text-foreground">{domainLabelZh(item)}</span><span className="block text-[10px] leading-4 text-muted-foreground">{item}</span></span><span className="tabular-nums">{domainCounts[item] || 0}</span>
          </button>)}
        </nav>
        <div className="min-w-0 space-y-3">
          <Select aria-label="Science 125 领域" className="md:hidden" value={domain} onChange={(event) => onDomain(event.target.value)}>
            <option value="">全部领域</option>
            {domains.map((item) => <option key={item} value={item}>{bilingualDomainLabel(item)} ({domainCounts[item] || 0})</option>)}
          </Select>
          <Input value={query} icon={Search} placeholder="按编号、题目、子领域或标签筛选" onChange={(event) => onQuery(event.target.value)} />
        </div>
      </div>
      <p className="text-xs text-muted-foreground">显示 {questions.length} / 125 个题目</p>
      {questions.length === 0 ? <NoDataState title="没有匹配题目" /> : <div className="max-h-[58vh] divide-y divide-border overflow-y-auto border-y border-border">
        {questions.map((item) => {
          const active = item.id === selected?.id
          return <button type="button" data-testid={`science125-question-${item.id}`} aria-pressed={active} onClick={() => onSelect(item)} className={`flex w-full items-start gap-3 px-3 py-3 text-left transition-colors hover:bg-secondary ${active ? "bg-primary-soft" : ""}`} key={item.id}>
            <span className={`mt-0.5 flex size-5 shrink-0 items-center justify-center border ${active ? "border-primary bg-primary text-white" : "border-border text-muted-foreground"}`}>{active && <CircleCheck className="size-3.5" />}</span>
            <span className="min-w-0"><span className="flex flex-wrap items-center gap-2"><span className="font-mono text-xs text-primary">{item.id}</span><span className="text-xs text-muted-foreground">{bilingualDomainLabel(item.benchmarkDomain)}</span><span className="text-xs text-muted-foreground">{item.primarySubdomain}</span></span><span className="mt-1 block text-sm leading-6 text-foreground">{item.questionZh || item.question}</span>{item.questionZh && <span className="mt-1 block text-xs leading-5 text-muted-foreground">{item.question}</span>}<span className="mt-1 block text-[11px] text-muted-foreground">{item.crossDomainTags.length ? `跨域：${item.crossDomainTags.join(" · ")}` : "单一领域"}</span></span>
          </button>
        })}
      </div>}
    </Panel>
    <aside>
      <Panel title="已选题目" icon={Lightbulb} className="sticky top-4">
        {!selected ? <div data-testid="science125-empty-selection"><NoDataState title="请选择一个题目" hint="题目来自固定的 science125-v1 目录。" className="py-12" /></div> : <div className="space-y-4"><div><Tag tone="blue">{selected.id}</Tag><p data-testid="science125-question-zh" className="mt-3 text-sm font-medium leading-6">{selected.questionZh || selected.question}</p>{selected.questionZh && <p data-testid="science125-question-en" className="mt-1 text-xs leading-5 text-muted-foreground">权威英文原题：{selected.question}</p>}<p className="mt-2 text-xs text-muted-foreground">{bilingualDomainLabel(selected.benchmarkDomain)} · {selected.primarySubdomain} · 题册第 {selected.bookletPage} 页</p></div><RoutingSummary profile={questionProfile} loading={profileLoading} error={profileError} /><AuthoritativeQuestionContext context={questionContext} loading={contextLoading} error={contextError} compact /><Btn data-testid="begin-science125-presearch" variant="primary" className="w-full" icon={ArrowRight} disabled={!questionContext?.sourceContext.trim()} onClick={onContinue}>开始文献预搜索</Btn></div>}
      </Panel>
    </aside>
  </div>
}

function RoutingSummary({
  profile,
  loading,
  error,
}: {
  profile: Science125QuestionProfile | null
  loading: boolean
  error: string | null
}) {
  if (loading) return <div data-testid="science125-routing-summary" className="text-xs text-muted-foreground">正在读取领域检索配置...</div>
  if (error) return <div data-testid="science125-routing-summary" className="text-xs text-warning">领域配置暂不可用：{error}</div>
  if (!profile) return null
  const blocked = profile.providerReadiness.filter((item) => !item.ready)
  return <section data-testid="science125-routing-summary" className="space-y-2 border-y border-border py-3">
    <div className="flex flex-wrap items-center gap-2"><Tag tone="blue">{profile.primarySubdomain}</Tag><span className="text-xs text-muted-foreground">方法：{profile.methodProfile.primary}{profile.methodProfile.secondary.length ? ` + ${profile.methodProfile.secondary.join(" + ")}` : ""}</span></div>
    <p className="text-[11px] text-muted-foreground">检索配置 {profile.retrievalProfile} · 提示词 {profile.promptProfile}</p>
    <div className="flex flex-wrap gap-1" aria-label="领域文献源">
      {profile.providers.map((provider) => <span key={provider.providerId} className={`border px-2 py-1 text-[10px] ${provider.isRequired ? "border-primary/40 text-primary" : "border-border text-muted-foreground"}`}>{provider.displayName}{provider.isRequired ? " · 必需" : ""}</span>)}
    </div>
    {blocked.length > 0 && <p className="text-[11px] text-warning">尚有 {blocked.length} 个检索源未就绪，生成前需要完成配置。</p>}
  </section>
}

function AuthoritativeQuestionContext({
  context,
  loading,
  error,
  compact = false,
}: {
  context: Science125QuestionContext | null
  loading: boolean
  error: string | null
  compact?: boolean
}) {
  if (loading) return <div data-testid="science125-booklet-context"><LoadingState label="正在读取题册完整上下文..." /></div>
  if (error || !context) return <div data-testid="science125-booklet-context"><ErrorState message={error || "题册完整上下文不可用。"} /></div>
  return <section data-testid="science125-booklet-context" className={compact ? "space-y-2 border-t border-border pt-3" : "border border-border bg-card px-4 py-3"}>
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="flex items-center gap-2"><BookOpenCheck className="size-4 text-primary" /><h3 className="text-sm font-semibold">权威英文题册上下文</h3></div>
      <span className="text-[11px] text-muted-foreground">PDF 第 {context.pdfPage} 页 · 校验 {context.contextSha256.slice(0, 12)}</span>
    </div>
    <p className={`${compact ? "max-h-40" : "max-h-52"} overflow-y-auto whitespace-pre-wrap text-xs leading-5 text-muted-foreground`}>{context.sourceContext}</p>
  </section>
}

function ReviewedInputSnapshot({
  question,
  context,
  evidenceContext,
  review,
  profile,
}: {
  question: Science125Question
  context: Science125QuestionContext | null
  evidenceContext: string
  review: LiteratureReviewState
  profile: Science125QuestionProfile | null
}) {
  return <Panel title="已审核输入快照" icon={FileCheck2} bodyClassName="space-y-4">
    <div className="grid gap-4 lg:grid-cols-2">
      <section>
        <p className="text-xs font-semibold text-foreground">Science 125 题目</p>
        <p className="mt-1 text-sm leading-6 text-foreground">{question.questionZh || question.question}</p>
        {question.questionZh && <p className="mt-1 text-xs leading-5 text-muted-foreground">权威英文原题：{question.question}</p>}
        <p className="mt-2 text-[11px] text-muted-foreground">{bilingualDomainLabel(question.benchmarkDomain)} · {question.primarySubdomain} · 方法 {profile?.methodProfile.primary || "读取中"}</p>
      </section>
      <section data-testid="science125-input-source" data-source-type="booklet-context">
        <p className="text-xs font-semibold text-foreground">题册原文上下文</p>
        <p className="mt-1 max-h-32 overflow-y-auto whitespace-pre-wrap text-xs leading-5 text-muted-foreground">{context?.sourceContext || "上下文不可用"}</p>
      </section>
    </div>
    <section data-testid="science125-reviewed-evidence" className="border-t border-border pt-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-semibold text-foreground">人工审核证据</p>
        <span className="text-[11px] text-muted-foreground">{review.selectedResultIds.length} 条检索结果 · {review.pageExcerpts.length} 组 PDF 页摘录</span>
      </div>
      <p className="mt-2 max-h-40 overflow-y-auto whitespace-pre-wrap text-xs leading-5 text-muted-foreground">{evidenceContext || "尚无审核证据"}</p>
    </section>
  </Panel>
}
