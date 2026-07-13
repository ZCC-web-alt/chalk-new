"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { BookOpen, Bot, FileText, Quote, Send, Sparkles, Square, User2 } from "lucide-react"
import { apiFetch, type Page } from "@/lib/api/client"
import { cancelJob, createJob, getLatestActiveJob, waitForJob } from "@/lib/api/jobs"
import type { DocumentRecord, Job, RagCitation, RagQaResult } from "@/lib/api/types"
import { ErrorState, LoadingState, NoDataState } from "../api-state"
import { Btn, Panel, Select, Tag, Textarea } from "../ui"
import { cn } from "@/lib/utils"

type Message = {
  id: string
  role: "user" | "assistant"
  content: string
  citations?: RagCitation[]
}

export function QaPage({ initialDocumentId }: { initialDocumentId?: number | null }) {
  const [documents, setDocuments] = useState<DocumentRecord[]>([])
  const [documentsLoading, setDocumentsLoading] = useState(true)
  const [selectedDocumentId, setSelectedDocumentId] = useState<number | null>(initialDocumentId ?? null)
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState("")
  const [job, setJob] = useState<Job<RagQaResult> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const controllerRef = useRef<AbortController | null>(null)

  const loadDocuments = useCallback(async () => {
    setDocumentsLoading(true)
    try {
      const response = await apiFetch<Page<DocumentRecord>>("/documents?pageSize=100")
      setDocuments(response.data)
      setSelectedDocumentId((current) => (
        current && response.data.some((document) => document.id === current) ? current : null
      ))
    } catch (err) {
      setError(err instanceof Error ? err.message : "文献列表加载失败")
    } finally {
      setDocumentsLoading(false)
    }
  }, [])

  const followQaJob = useCallback(async (queued: Job<RagQaResult>, controller: AbortController) => {
    const completed = await waitForJob(queued, { signal: controller.signal, onUpdate: setJob })
    if (completed.status === "SUCCEEDED" && completed.result) {
      const result = completed.result
      setMessages((current) => [
        ...current,
        { id: crypto.randomUUID(), role: "assistant", content: result.answer, citations: result.citations },
      ])
      setJob(null)
    } else if (completed.status !== "CANCELLED") {
      setError(completed.error?.message || "智能问答失败")
    }
  }, [])

  useEffect(() => {
    void loadDocuments()
    void getLatestActiveJob<RagQaResult>("rag_qa").then((activeJob) => {
      if (!activeJob) return
      const controller = new AbortController()
      controllerRef.current = controller
      return followQaJob(activeJob, controller)
    }).catch((err) => setError(err instanceof Error ? err.message : "无法恢复问答任务"))
    return () => controllerRef.current?.abort()
  }, [followQaJob, loadDocuments])

  useEffect(() => {
    if (initialDocumentId) setSelectedDocumentId(initialDocumentId)
  }, [initialDocumentId])

  useEffect(() => {
    requestAnimationFrame(() => scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" }))
  }, [messages, job?.progress])

  async function sendQuestion() {
    const question = input.trim()
    if (!question || documents.length === 0) return
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    setError(null)
    setInput("")
    setMessages((current) => [...current, { id: crypto.randomUUID(), role: "user", content: question }])
    try {
      const queued = await createJob<RagQaResult>("rag_qa", {
        question,
        documentId: selectedDocumentId ?? undefined,
        topK: 5,
      })
      await followQaJob(queued, controller)
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return
      setError(err instanceof Error ? err.message : "智能问答失败")
    }
  }

  async function stopQuestion() {
    if (!job) return
    try {
      setJob(await cancelJob<RagQaResult>(job.id))
      controllerRef.current?.abort()
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法取消问答任务")
    }
  }

  const citations = useMemo(() => messages.flatMap((message) => message.citations ?? []), [messages])
  const running = job && ["QUEUED", "RUNNING"].includes(job.status)

  return (
    <div className="grid h-full grid-cols-1 gap-3 lg:grid-cols-[1fr_300px]">
      <Panel title="智能问答" icon={Sparkles} noPadding extra={<Tag tone="purple">RAG</Tag>} className="min-h-0">
        <div className="flex h-full min-h-0 flex-col">
          <div className="flex items-center gap-2 border-b border-border px-3 py-2">
            <BookOpen className="size-4 text-muted-foreground" />
            <Select
              aria-label="知识库范围"
              value={selectedDocumentId ?? ""}
              onChange={(event) => setSelectedDocumentId(event.target.value ? Number(event.target.value) : null)}
              disabled={documentsLoading || documents.length === 0}
              className="min-w-0 flex-1"
            >
              <option value="">全部文献</option>
              {documents.map((document) => <option key={document.id} value={document.id}>{document.title}</option>)}
            </Select>
          </div>

          <div ref={scrollRef} className="min-h-0 flex-1 space-y-4 overflow-auto p-4">
            {documentsLoading ? (
              <LoadingState label="正在加载文献..." />
            ) : messages.length === 0 && !running ? (
              <NoDataState title="暂无问答记录" />
            ) : (
              messages.map((message) => <MessageBubble key={message.id} message={message} />)
            )}
            {running && (
              <div className="flex items-start gap-2.5">
                <div className="flex size-7 shrink-0 items-center justify-center rounded-md bg-purple text-white"><Bot className="size-4" /></div>
                <div className="min-w-0 flex-1 rounded-md border border-dashed border-purple/40 bg-purple-soft px-3 py-2 text-xs text-purple">
                  <div className="mb-2 flex items-center justify-between gap-3"><span className="truncate">{job.message || "正在处理"}</span><span className="tabular-nums">{job.progress}%</span></div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-card"><div className="h-full bg-purple transition-all" style={{ width: `${job.progress}%` }} /></div>
                </div>
              </div>
            )}
          </div>

          {error && <div className="border-t border-border px-3 py-2"><ErrorState message={error} /></div>}

          <div className="shrink-0 border-t border-border p-3">
            <div className="relative">
              <Textarea
                rows={2}
                value={input}
                disabled={documents.length === 0 || Boolean(running)}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                    event.preventDefault()
                    void sendQuestion()
                  }
                }}
                placeholder={documents.length === 0 ? "请先导入文献" : "输入问题"}
                className="pr-24"
              />
              <div className="absolute bottom-2 right-2">
                {running ? (
                  <Btn variant="danger" size="xs" icon={Square} onClick={stopQuestion}>取消</Btn>
                ) : (
                  <Btn variant="primary" size="xs" icon={Send} disabled={!input.trim() || documents.length === 0} onClick={sendQuestion}>发送</Btn>
                )}
              </div>
            </div>
          </div>
        </div>
      </Panel>

      <div className="hidden min-h-0 flex-col gap-3 lg:flex">
        <Panel title="引用来源" icon={Quote} bodyClassName="space-y-2 overflow-auto">
          {citations.length === 0 ? <NoDataState title="暂无引用" className="py-6" /> : citations.map((citation) => (
            <div key={`${citation.documentId}-${citation.chunkId}-${citation.index}`} className="rounded-md border border-border bg-secondary/60 p-2">
              <div className="mb-1 flex items-start gap-2">
                <span className="flex size-4 shrink-0 items-center justify-center rounded-sm bg-primary text-[10px] font-bold text-primary-foreground">{citation.index}</span>
                <p className="line-clamp-2 text-xs font-medium text-foreground">{citation.sourceTitle}</p>
              </div>
              <p className="line-clamp-4 text-[11px] leading-relaxed text-muted-foreground">{citation.excerpt}</p>
              <p className="mt-1 text-[10px] tabular-nums text-muted-foreground">相关度 {(citation.score * 100).toFixed(1)}%</p>
            </div>
          ))}
        </Panel>
        <Panel title="知识库范围" icon={BookOpen} bodyClassName="space-y-1.5 overflow-auto">
          {documents.length === 0 ? <NoDataState title="暂无文献" className="py-6" /> : documents.map((document) => (
            <button
              key={document.id}
              onClick={() => setSelectedDocumentId(document.id)}
              className={cn("flex w-full items-start gap-2 rounded-md px-1.5 py-1.5 text-left hover:bg-secondary", selectedDocumentId === document.id && "bg-primary-soft")}
            >
              <FileText className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
              <p className="line-clamp-2 text-xs leading-snug text-foreground">{document.title}</p>
            </button>
          ))}
        </Panel>
      </div>
    </div>
  )
}

function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === "user"
  return (
    <div className={isUser ? "flex justify-end" : "flex justify-start"}>
      <div className={cn("flex max-w-[85%] gap-2.5", isUser && "flex-row-reverse")}>
        <div className={cn("flex size-7 shrink-0 items-center justify-center rounded-md", isUser ? "bg-primary text-primary-foreground" : "bg-purple text-white")}>
          {isUser ? <User2 className="size-4" /> : <Bot className="size-4" />}
        </div>
        <div className="min-w-0">
          <div className={cn("rounded-lg px-3 py-2 text-[13px] leading-relaxed whitespace-pre-wrap", isUser ? "bg-primary text-primary-foreground" : "border border-border bg-card text-foreground")}>
            {message.content}
          </div>
          {!isUser && message.citations && message.citations.length > 0 && (
            <div className="mt-2 space-y-1.5 lg:hidden">
              {message.citations.map((citation) => (
                <div key={`${citation.documentId}-${citation.chunkId}-${citation.index}`} className="rounded-md border border-border bg-secondary/60 p-2 text-[11px]">
                  <p className="mb-1 font-medium text-foreground">[{citation.index}] {citation.sourceTitle}</p>
                  <p className="line-clamp-3 text-muted-foreground">{citation.excerpt}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
