"use client"

/* eslint-disable @next/next/no-img-element -- Analysis assets require authenticated API requests. */

import { useState } from "react"
import {
  AlertTriangle,
  Beaker,
  BookOpenText,
  Copy,
  Download,
  FileImage,
  FileText,
  FlaskConical,
  Image as ImageIcon,
  Languages,
  LoaderCircle,
  Play,
  RefreshCw,
  Save,
  Search,
  Sparkles,
  X,
} from "lucide-react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { apiUrl } from "@/lib/api/client"
import type {
  ChemicalInfo,
  ComparisonAnalysisResult,
  DocumentAnalysisRecord,
  DocumentAnalysisType,
  DocumentRecord,
  ImageAnalysisResult,
  Job,
  ReactionAnalysisResult,
  ReactionItem,
  SafetyAnalysisResult,
  SopAnalysisResult,
  StructureAnalysisResult,
  SummaryAnalysisResult,
  TranslationAnalysisResult,
} from "@/lib/api/types"
import { cn } from "@/lib/utils"
import { NoDataState } from "../api-state"
import { Btn, Input, Panel, Select, Tabs, Tag, Toggle } from "../ui"

export type AnalysisMap = Partial<Record<DocumentAnalysisType, DocumentAnalysisRecord>>

const TAB_LABELS = ["摘要", "图片", "结构式", "安全", "SOP", "反应", "翻译"] as const
type TabLabel = (typeof TAB_LABELS)[number]

const TAB_TO_TYPE: Record<TabLabel, DocumentAnalysisType> = {
  摘要: "summary",
  图片: "images",
  结构式: "structures",
  安全: "safety",
  SOP: "sop",
  反应: "reactions",
  翻译: "translation",
}

function resultOf<T>(analyses: AnalysisMap, type: DocumentAnalysisType) {
  return (analyses[type]?.result as T | undefined) ?? null
}

export function LiteratureAnalysisView({
  document,
  analyses,
  loading,
  error,
  job,
  actionMessage,
  onRun,
  onCancel,
  onCreateLabRecord,
  onSaveGlossary,
}: {
  document: DocumentRecord
  analyses: AnalysisMap
  loading: boolean
  error: string | null
  job: Job<DocumentAnalysisRecord> | null
  actionMessage: string | null
  onRun: (type: DocumentAnalysisType, options?: { chemicalName?: string; useLlmPageHints?: boolean }) => void
  onCancel: () => void
  onCreateLabRecord: (reaction: ReactionItem) => void
  onSaveGlossary: (terms: TranslationAnalysisResult["glossary"]) => void
}) {
  const [activeTab, setActiveTab] = useState<TabLabel>("摘要")
  const currentType = TAB_TO_TYPE[activeTab]
  const running = Boolean(job && (job.status === "QUEUED" || job.status === "RUNNING"))

  return (
    <Panel title={document.title} icon={FileText} noPadding className="min-h-0" bodyClassName="flex min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2 text-xs text-muted-foreground">
        <span className="font-mono">DOC-{document.id}</span>
        <Tag tone="blue">{document.sourceType}</Tag>
        <span>{document.fileName || "未记录文件名"}</span>
        <span className="ml-auto">{new Date(document.createdAt).toLocaleString()}</span>
      </div>
      <Tabs tabs={[...TAB_LABELS]} active={activeTab} onChange={(tab) => setActiveTab(tab as TabLabel)} />
      {job && (
        <div className="flex items-center gap-3 border-b border-border bg-secondary/50 px-3 py-2 text-xs">
          <LoaderCircle className={cn("size-4 text-primary", running && "animate-spin")} />
          <div className="h-1.5 min-w-24 flex-1 overflow-hidden rounded-full bg-secondary">
            <div className="h-full bg-primary transition-all" style={{ width: `${job.progress}%` }} />
          </div>
          <span className="tabular-nums text-foreground">{job.progress}%</span>
          <span className="max-w-72 truncate text-muted-foreground">{job.message || "等待处理"}</span>
          {running && <Btn size="xs" variant="ghost" icon={X} onClick={onCancel}>取消</Btn>}
        </div>
      )}
      {error && <div className="border-b border-danger/30 bg-danger-soft px-3 py-2 text-xs text-danger">{error}</div>}
      {actionMessage && <div className="border-b border-success/30 bg-success-soft px-3 py-2 text-xs text-success">{actionMessage}</div>}
      <div className="min-h-0 flex-1 overflow-auto p-3">
        {loading ? (
          <div className="flex h-40 items-center justify-center gap-2 text-sm text-muted-foreground">
            <LoaderCircle className="size-4 animate-spin" />正在加载分析结果
          </div>
        ) : (
          <>
            {currentType === "summary" && <SummaryTab document={document} result={resultOf<SummaryAnalysisResult>(analyses, "summary")} running={running} onRun={() => onRun("summary")} />}
            {currentType === "images" && <ImagesTab documentId={document.id} result={resultOf<ImageAnalysisResult>(analyses, "images")} running={running} onRun={(useLlmPageHints) => onRun("images", { useLlmPageHints })} />}
            {currentType === "structures" && <StructuresTab documentId={document.id} result={resultOf<StructureAnalysisResult>(analyses, "structures")} running={running} onRun={(chemicalName) => onRun("structures", chemicalName ? { chemicalName } : undefined)} />}
            {currentType === "safety" && <SafetyTab result={resultOf<SafetyAnalysisResult>(analyses, "safety")} running={running} onRun={() => onRun("safety")} />}
            {currentType === "sop" && <SopTab result={resultOf<SopAnalysisResult>(analyses, "sop")} running={running} onRun={() => onRun("sop")} />}
            {currentType === "reactions" && <ReactionsTab result={resultOf<ReactionAnalysisResult>(analyses, "reactions")} running={running} onRun={() => onRun("reactions")} onCreateLabRecord={onCreateLabRecord} />}
            {currentType === "translation" && <TranslationTab result={resultOf<TranslationAnalysisResult>(analyses, "translation")} running={running} onRun={() => onRun("translation")} onSaveGlossary={onSaveGlossary} />}
          </>
        )}
      </div>
    </Panel>
  )
}

function SectionHeader({ title, hasResult, running, onRun, runLabel }: {
  title: string
  hasResult: boolean
  running: boolean
  onRun: () => void
  runLabel: string
}) {
  return (
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
      <h3 className="text-[13px] font-semibold text-foreground">{title}</h3>
      <Btn size="xs" icon={hasResult ? RefreshCw : Play} disabled={running} onClick={onRun}>
        {hasResult ? `重新${runLabel}` : runLabel}
      </Btn>
    </div>
  )
}

function SummaryTab({ document, result, running, onRun }: { document: DocumentRecord; result: SummaryAnalysisResult | null; running: boolean; onRun: () => void }) {
  const summary = result?.summary || document.summary
  return (
    <div>
      <SectionHeader title="文献摘要" hasResult={Boolean(summary)} running={running} onRun={onRun} runLabel="生成摘要" />
      {!summary ? <NoDataState title="暂无摘要" /> : (
        <>
          <p className="whitespace-pre-wrap text-[13px] leading-7 text-foreground">{summary}</p>
          {result && (
            <div className="mt-4 flex flex-wrap gap-2 border-t border-border pt-3 text-xs text-muted-foreground">
              <Tag tone="gray">{result.segmentCount} 个分段</Tag>
              <Tag tone="gray">{result.sourceCharCount.toLocaleString()} 字符</Tag>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function ImagesTab({ documentId, result, running, onRun }: { documentId: number; result: ImageAnalysisResult | null; running: boolean; onRun: (useHints: boolean) => void }) {
  const [useHints, setUseHints] = useState(false)
  const [selectedAsset, setSelectedAsset] = useState<string | null>(null)
  const selected = result?.images.find((image) => image.assetId === selectedAsset) ?? result?.images[0]

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <h3 className="text-[13px] font-semibold text-foreground">PDF 图片</h3>
        <label className="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
          智能筛选含图页
          <Toggle checked={useHints} onChange={setUseHints} />
        </label>
        <Btn size="xs" icon={result ? RefreshCw : FileImage} disabled={running} onClick={() => onRun(useHints)}>
          {result ? "重新提取" : "提取图片"}
        </Btn>
      </div>
      {!result || result.images.length === 0 ? <NoDataState title={result ? "未检测到图片" : "尚未提取图片"} /> : (
        <div className="grid min-h-0 gap-3 lg:grid-cols-[160px_1fr]">
          <div className="flex gap-2 overflow-auto lg:flex-col">
            {result.images.map((image, index) => (
              <button
                key={image.assetId}
                aria-label={`查看图片 ${index + 1}`}
                onClick={() => setSelectedAsset(image.assetId)}
                className={cn("h-20 w-28 shrink-0 overflow-hidden rounded-md border bg-secondary", selected?.assetId === image.assetId ? "border-primary" : "border-border")}
              >
                <img src={apiUrl(`/documents/${documentId}/assets/${image.assetId}`)} alt={image.fileName} className="size-full object-contain" />
              </button>
            ))}
          </div>
          {selected && (
            <div className="flex min-h-80 items-center justify-center overflow-auto border-l border-border pl-3 lg:min-h-0">
              <img src={apiUrl(`/documents/${documentId}/assets/${selected.assetId}`)} alt={selected.fileName} className="max-h-[60vh] max-w-full object-contain" />
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function StructuresTab({ documentId, result, running, onRun }: { documentId: number; result: StructureAnalysisResult | null; running: boolean; onRun: (chemicalName?: string) => void }) {
  const [query, setQuery] = useState("")
  const [selectedName, setSelectedName] = useState("")
  const compounds = result?.compounds ?? []
  const selected = compounds.find((compound) => compound.name === selectedName) ?? compounds[0]

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-end gap-2">
        <div className="mr-auto">
          <h3 className="text-[13px] font-semibold text-foreground">化学结构</h3>
          <p className="mt-1 text-xs text-muted-foreground">自动识别文献化学品，或按名称、英文名、CAS 号查询 PubChem。</p>
        </div>
        <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="输入化学品名称" className="w-48" />
        <Btn size="xs" icon={Search} disabled={running || !query.trim()} onClick={() => onRun(query.trim())}>查询</Btn>
        <Btn size="xs" icon={Sparkles} disabled={running} onClick={() => onRun()}>识别化学品</Btn>
      </div>
      {result?.warnings.map((warning) => <p key={warning} className="mb-2 text-xs text-warning">{warning}</p>)}
      {!result || compounds.length === 0 ? <NoDataState title="尚无结构查询结果" /> : (
        <div className="grid gap-4 xl:grid-cols-[320px_1fr]">
          <div>
            {compounds.length > 1 && (
              <Select className="mb-3 w-full" value={selected?.name} onChange={(event) => setSelectedName(event.target.value)}>
                {compounds.map((compound) => <option key={compound.name} value={compound.name}>{compound.name}</option>)}
              </Select>
            )}
            <div className="flex min-h-64 items-center justify-center bg-secondary/50">
              {selected?.assetId ? (
                <img src={apiUrl(`/documents/${documentId}/assets/${selected.assetId}`)} alt={`${selected.name} 结构式`} className="max-h-72 max-w-full object-contain" />
              ) : <ImageIcon className="size-10 text-muted-foreground" />}
            </div>
          </div>
          {selected && <ChemicalProperties compound={selected} />}
        </div>
      )}
    </div>
  )
}

function ChemicalProperties({ compound }: { compound: ChemicalInfo }) {
  const rows = [
    ["名称", compound.name], ["PubChem CID", compound.cid], ["IUPAC 名称", compound.iupacName],
    ["分子式", compound.molecularFormula], ["分子量", compound.molecularWeight], ["CAS", compound.cas],
    ["SMILES", compound.canonicalSmiles], ["InChIKey", compound.inchiKey], ["熔点", compound.meltingPoint],
    ["沸点", compound.boilingPoint], ["密度", compound.density], ["溶解性", compound.solubility], ["外观", compound.appearance],
  ].filter(([, value]) => value !== undefined && value !== null && value !== "")
  return (
    <dl className="divide-y divide-border border-y border-border text-xs">
      {rows.map(([label, value]) => (
        <div key={String(label)} className="grid grid-cols-[120px_1fr] gap-3 py-2">
          <dt className="text-muted-foreground">{label}</dt><dd className="break-all text-foreground">{String(value)}</dd>
        </div>
      ))}
    </dl>
  )
}

function SafetyTab({ result, running, onRun }: { result: SafetyAnalysisResult | null; running: boolean; onRun: () => void }) {
  return (
    <div>
      <SectionHeader title="化学品安全信息" hasResult={Boolean(result)} running={running} onRun={onRun} runLabel="检测安全信息" />
      {!result ? <NoDataState title="尚未检测安全信息" /> : result.chemicals.length === 0 ? <NoDataState title="未识别到化学品" /> : (
        <div className="divide-y divide-border border-y border-border">
          {result.chemicals.map((chemical) => (
            <section key={chemical.name} className="py-3">
              <div className="mb-2 flex flex-wrap items-center gap-2">
                {chemical.highRisk && <AlertTriangle className="size-4 text-danger" />}
                <h4 className="text-[13px] font-semibold">{chemical.name}</h4>
                <Tag tone={chemical.highRisk ? "red" : "green"}>{chemical.signalWord || chemical.status}</Tag>
                <Tag tone="gray">{chemical.source === "local" ? "本地库" : "PubChem"}</Tag>
              </div>
              {chemical.ghsCodes.length > 0 && <p className="mb-1 text-xs text-muted-foreground">GHS: {chemical.ghsCodes.join(", ")}</p>}
              {chemical.hazards.map((hazard) => <p key={hazard} className="text-xs leading-6 text-foreground">{hazard}</p>)}
              {chemical.warning && <p className="mt-1 text-xs text-warning">{chemical.warning}</p>}
            </section>
          ))}
        </div>
      )}
    </div>
  )
}

function SopTab({ result, running, onRun }: { result: SopAnalysisResult | null; running: boolean; onRun: () => void }) {
  return (
    <div>
      <SectionHeader title="标准操作程序" hasResult={Boolean(result)} running={running} onRun={onRun} runLabel="提取 SOP" />
      {!result ? <NoDataState title="尚未提取 SOP" /> : (
        <div className="space-y-5">
          <h4 className="text-sm font-semibold text-foreground">{result.title || "未命名实验"}</h4>
          {result.chemicals.length > 0 && (
            <div className="overflow-x-auto"><table className="w-full border-collapse text-xs"><thead><tr className="border-b border-border bg-secondary"><th className="p-2 text-left">化学品</th><th className="p-2 text-left">用量</th><th className="p-2 text-left">作用</th><th className="p-2 text-left">安全</th></tr></thead><tbody>{result.chemicals.map((chemical, index) => <tr key={`${chemical.name}-${index}`} className="border-b border-border"><td className="p-2">{chemical.name}</td><td className="p-2">{chemical.amount}</td><td className="p-2">{chemical.role}</td><td className="p-2">{chemical.safety}</td></tr>)}</tbody></table></div>
          )}
          <ol className="divide-y divide-border border-y border-border">
            {result.steps.map((step) => <li key={step.step} className="grid grid-cols-[32px_1fr] gap-2 py-3"><span className="flex size-6 items-center justify-center rounded-sm bg-primary-soft text-xs font-semibold text-primary">{step.step}</span><div><p className="text-[13px] text-foreground">{step.action}</p>{step.params && <p className="mt-1 text-xs text-muted-foreground">参数：{step.params}</p>}{step.safetyNote && <p className="mt-1 text-xs text-warning">注意：{step.safetyNote}</p>}</div></li>)}
          </ol>
          {result.postProcessing && <TextSection title="后处理" value={result.postProcessing} />}
          {result.characterization && <TextSection title="表征与分析" value={result.characterization} />}
        </div>
      )}
    </div>
  )
}

function ReactionsTab({ result, running, onRun, onCreateLabRecord }: { result: ReactionAnalysisResult | null; running: boolean; onRun: () => void; onCreateLabRecord: (reaction: ReactionItem) => void }) {
  return (
    <div>
      <SectionHeader title="反应条件" hasResult={Boolean(result)} running={running} onRun={onRun} runLabel="提取反应" />
      {!result ? <NoDataState title="尚未提取反应信息" /> : result.reactions.length === 0 ? <NoDataState title="文献中未识别到反应" /> : (
        <div className="divide-y divide-border border-y border-border">
          {result.summary && <p className="py-3 text-[13px] leading-6 text-foreground">{result.summary}</p>}
          {result.reactions.map((reaction, index) => (
            <section key={`${reaction.name}-${index}`} className="py-4">
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <FlaskConical className="size-4 text-primary" />
                <h4 className="text-[13px] font-semibold">{reaction.name || `反应 ${index + 1}`}</h4>
                <Btn className="ml-auto" size="xs" icon={Beaker} onClick={() => onCreateLabRecord(reaction)}>生成实验记录</Btn>
              </div>
              <p className="mb-3 font-mono text-xs text-foreground">{reaction.reactants.join(" + ") || "?"} → {reaction.products.join(" + ") || "?"}</p>
              <dl className="grid gap-x-6 gap-y-2 text-xs sm:grid-cols-2 xl:grid-cols-3">
                {[['催化剂/试剂', reaction.catalyst], ['溶剂', reaction.solvent], ['温度', reaction.temperature], ['时间', reaction.time], ['压力', reaction.pressure], ['pH', reaction.ph], ['产率', reaction.yield], ['后处理', reaction.workup], ['备注', reaction.notes]].filter(([, value]) => value).map(([label, value]) => <div key={label} className="grid grid-cols-[76px_1fr] gap-2"><dt className="text-muted-foreground">{label}</dt><dd className="text-foreground">{value}</dd></div>)}
              </dl>
            </section>
          ))}
        </div>
      )}
    </div>
  )
}

function TranslationTab({ result, running, onRun, onSaveGlossary }: { result: TranslationAnalysisResult | null; running: boolean; onRun: () => void; onSaveGlossary: (terms: TranslationAnalysisResult["glossary"]) => void }) {
  const text = result ? result.segments.map((segment) => `【原文 ${segment.index}】\n${segment.sourceText}\n\n【译文 ${segment.index}】\n${segment.translation}`).join("\n\n") : ""
  const copyTranslation = () => void navigator.clipboard.writeText(text)
  const exportTranslation = () => {
    if (!result) return
    const glossary = result.glossary.map((term) => `${term.en} = ${term.zh}${term.note ? ` (${term.note})` : ""}`).join("\n")
    const url = URL.createObjectURL(new Blob([`${text}\n\n【术语对照表】\n${glossary}`], { type: "text/plain;charset=utf-8" }))
    const anchor = document.createElement("a")
    anchor.href = url
    anchor.download = "chalk-translation.txt"
    anchor.click()
    URL.revokeObjectURL(url)
  }
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h3 className="text-[13px] font-semibold">文献对照翻译</h3>
        {result && <Tag tone="gray">{result.sourceCharCount.toLocaleString()} 字符</Tag>}
        {result && <Btn size="xs" icon={Copy} onClick={copyTranslation}>复制译文</Btn>}
        {result && <Btn size="xs" icon={Download} onClick={exportTranslation}>导出文本</Btn>}
        {result && result.glossary.length > 0 && <Btn className="ml-auto" size="xs" icon={Save} onClick={() => onSaveGlossary(result.glossary)}>保存到术语库</Btn>}
        <Btn size="xs" icon={result ? RefreshCw : Languages} disabled={running} onClick={onRun}>{result ? "重新翻译" : "翻译文献"}</Btn>
      </div>
      {!result ? <NoDataState title="尚未翻译文献" /> : (
        <div className="space-y-5">
          <div className="divide-y divide-border border-y border-border">
            {result.segments.map((segment) => (
              <section key={segment.index} className="grid gap-3 py-4 lg:grid-cols-2">
                <div><h4 className="mb-2 text-xs font-semibold text-muted-foreground">原文 {segment.index}</h4><p className="whitespace-pre-wrap text-[13px] leading-7 text-foreground">{segment.sourceText}</p></div>
                <div className="border-t border-border pt-3 lg:border-l lg:border-t-0 lg:pl-3 lg:pt-0"><h4 className="mb-2 text-xs font-semibold text-primary">译文 {segment.index}</h4><p className="whitespace-pre-wrap text-[13px] leading-7 text-foreground">{segment.translation}</p></div>
              </section>
            ))}
          </div>
          {result.glossary.length > 0 && (
            <div><h4 className="mb-2 text-[13px] font-semibold">术语</h4><div className="overflow-x-auto"><table className="w-full text-xs"><thead><tr className="border-b border-border bg-secondary"><th className="p-2 text-left">英文</th><th className="p-2 text-left">中文</th><th className="p-2 text-left">说明</th></tr></thead><tbody>{result.glossary.map((term) => <tr key={term.en.toLowerCase()} className="border-b border-border"><td className="p-2">{term.en}</td><td className="p-2">{term.zh}</td><td className="p-2 text-muted-foreground">{term.note}</td></tr>)}</tbody></table></div></div>
          )}
        </div>
      )}
    </div>
  )
}

function TextSection({ title, value }: { title: string; value: string }) {
  return <section><h4 className="mb-1 text-xs font-semibold text-muted-foreground">{title}</h4><p className="whitespace-pre-wrap text-[13px] leading-6 text-foreground">{value}</p></section>
}

export function ComparisonDialog({ open, documents, record, job, error, onClose, onRun, onCancel }: {
  open: boolean
  documents: DocumentRecord[]
  record: DocumentAnalysisRecord<ComparisonAnalysisResult> | null
  job: Job<DocumentAnalysisRecord<ComparisonAnalysisResult>> | null
  error: string | null
  onClose: () => void
  onRun: () => void
  onCancel: () => void
}) {
  if (!open) return null
  const running = Boolean(job && (job.status === "QUEUED" || job.status === "RUNNING"))
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 p-3" role="dialog" aria-modal="true" aria-label="多文献对比分析">
      <div className="flex max-h-[90vh] w-full max-w-5xl flex-col overflow-hidden rounded-lg border border-border bg-card shadow-xl">
        <header className="flex min-h-11 items-center gap-2 border-b border-border px-3">
          <BookOpenText className="size-4 text-primary" />
          <h2 className="text-sm font-semibold">多文献对比分析</h2>
          <span className="text-xs text-muted-foreground">{documents.map((document) => document.title).join(" · ")}</span>
          <button className="ml-auto text-muted-foreground hover:text-foreground" onClick={onClose} aria-label="关闭对比分析"><X className="size-4" /></button>
        </header>
        {job && (
          <div className="flex items-center gap-3 border-b border-border px-3 py-2 text-xs">
            <LoaderCircle className={cn("size-4 text-primary", running && "animate-spin")} />
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-secondary"><div className="h-full bg-primary" style={{ width: `${job.progress}%` }} /></div>
            <span>{job.progress}%</span>
            {running && <Btn size="xs" variant="ghost" icon={X} onClick={onCancel}>取消</Btn>}
          </div>
        )}
        {error && <div className="border-b border-danger/30 bg-danger-soft px-3 py-2 text-xs text-danger">{error}</div>}
        <div className="min-h-0 flex-1 overflow-auto p-4">
          {!record ? <NoDataState title={running ? "正在生成对比结果" : "尚无对比结果"} /> : (
            <div className="analysis-markdown text-[13px] leading-7 text-foreground">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  table: ({ children }) => <div className="my-3 overflow-x-auto"><table className="w-full border-collapse text-xs">{children}</table></div>,
                  th: ({ children }) => <th className="border border-border bg-secondary p-2 text-left font-semibold">{children}</th>,
                  td: ({ children }) => <td className="border border-border p-2 align-top">{children}</td>,
                  h1: ({ children }) => <h3 className="mb-2 text-base font-semibold">{children}</h3>,
                  h2: ({ children }) => <h3 className="mb-2 text-sm font-semibold">{children}</h3>,
                  p: ({ children }) => <p className="my-2">{children}</p>,
                  ul: ({ children }) => <ul className="my-2 list-disc pl-5">{children}</ul>,
                }}
              >{record.result.markdown}</ReactMarkdown>
            </div>
          )}
        </div>
        <footer className="flex justify-end gap-2 border-t border-border px-3 py-2">
          <Btn onClick={onClose}>关闭</Btn>
          <Btn variant="primary" icon={record ? RefreshCw : Play} disabled={running} onClick={onRun}>{record ? "重新对比" : "开始对比"}</Btn>
        </footer>
      </div>
    </div>
  )
}
