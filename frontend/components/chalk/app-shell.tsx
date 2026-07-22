"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import {
  Beaker,
  ChevronLeft,
  Database,
  FileText,
  FlaskConical,
  Images,
  Library,
  Lightbulb,
  LogOut,
  MessageSquareText,
  Search,
  Settings,
  Cpu,
  BookMarked,
  type LucideIcon,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { apiFetch } from "@/lib/api/client"
import type { User } from "@/lib/api/types"
import { LiteraturePage } from "./pages/literature-management"
import { SearchPage } from "./pages/literature-search"
import { QaPage } from "./pages/qa"
import { NotesPage } from "./pages/lab-notes"
import { GlossaryPage } from "./pages/glossary"
import { HypothesisPage } from "./pages/hypothesis-generation"
import { HypothesisLibraryPage } from "./pages/hypothesis-library"
import { EvidencePage } from "./pages/evidence-database"
import { MultimodalPage } from "./pages/multimodal"
import { ModelingPage } from "./pages/computational-modeling"
import { ApiSettingsPage } from "./pages/api-settings"

type NavItem = { key: string; label: string; icon: LucideIcon }

const NAV: NavItem[] = [
  { key: "literature", label: "文献管理", icon: FileText },
  { key: "search", label: "文献检索", icon: Search },
  { key: "qa", label: "智能问答", icon: MessageSquareText },
  { key: "notes", label: "实验记录", icon: Beaker },
  { key: "glossary", label: "术语库", icon: BookMarked },
  { key: "hypothesis", label: "假设生成", icon: Lightbulb },
  { key: "hyplib", label: "假设库", icon: Library },
  { key: "evidence", label: "证据数据库", icon: Database },
  { key: "multimodal", label: "多模态分析", icon: Images },
  { key: "modeling", label: "计算建模", icon: Cpu },
  { key: "api", label: "API 设置", icon: Settings },
]

const DISCOVERY_NAV: Array<NavItem & { href: string }> = [
  { key: "research-general", label: "跨学科假设工作台", icon: Lightbulb, href: "/research/general/new" },
]

const HYPOTHESIS_KEYS = new Set(["hypothesis", "hyplib"])

export function AppShell({ user, onLogout }: { user: User; onLogout: () => void | Promise<void> }) {
  const router = useRouter()
  const [active, setActive] = useState("literature")
  const [collapsed, setCollapsed] = useState(false)
  const [apiHealthy, setApiHealthy] = useState<boolean | null>(null)
  const [qaDocumentId, setQaDocumentId] = useState<number | null>(null)
  const [hypothesisSeed, setHypothesisSeed] = useState<{ id: number; text: string } | null>(null)
  const [modelingHypothesisId, setModelingHypothesisId] = useState<number | null>(null)

  useEffect(() => {
    apiFetch<{ status: string }>("/health")
      .then((response) => setApiHealthy(response.status === "ok"))
      .catch(() => setApiHealthy(false))
  }, [])

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-background text-foreground">
      {/* Top bar */}
      <header className="flex h-12 shrink-0 items-center justify-between bg-topbar px-3 text-topbar-foreground">
        <div className="flex items-center gap-2.5">
          <div className="flex size-7 items-center justify-center rounded-md bg-primary">
            <FlaskConical className="size-4" />
          </div>
          <div className="flex items-baseline gap-2">
            <span className="text-[15px] font-bold tracking-tight">Chalk</span>
            <span className="text-xs text-white/55">化工文献智能分析</span>
          </div>
        </div>

        <div className="flex items-center gap-4">
          <div className="hidden items-center gap-3 md:flex">
            <StatusDotDark
              tone={apiHealthy === false ? "red" : apiHealthy === true ? "green" : "gray"}
              label={apiHealthy === false ? "API 未连接" : apiHealthy === true ? "API 已连接" : "正在检测 API"}
            />
          </div>
          <div className="flex items-center gap-2 border-l border-white/15 pl-4">
            <div className="flex size-7 items-center justify-center rounded-full bg-primary text-[11px] font-semibold">
              {user.username.slice(0, 1).toUpperCase()}
            </div>
            <div className="hidden leading-tight sm:block">
              <div className="text-xs font-medium">{user.username}</div>
              <div className="text-[10px] text-white/50">Chalk Web</div>
            </div>
            <button
              onClick={onLogout}
              className="ml-1 text-white/60 hover:text-white"
              aria-label="退出登录"
            >
              <LogOut className="size-4" />
            </button>
          </div>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* Sidebar */}
        <nav
          className={cn(
            "flex shrink-0 flex-col border-r border-border bg-sidebar transition-all",
            collapsed ? "w-14" : "w-14 md:w-52",
          )}
        >
          <div className="flex-1 overflow-y-auto py-2">
            {!collapsed && <div className="px-4 pb-1 pt-1 text-[10px] font-semibold text-muted-foreground md:block">科学发现</div>}
            {DISCOVERY_NAV.map((item) => (
              <button
                key={item.key}
                data-nav-key={item.key}
                onClick={() => router.push(item.href)}
                className={cn(
                  "relative flex w-full items-center gap-3 px-4 py-2.5 text-[13px] font-medium text-sidebar-foreground transition-colors hover:bg-sidebar-hover hover:text-foreground",
                  collapsed ? "justify-center px-0" : "justify-center px-0 md:justify-start md:px-4",
                )}
                title={item.label}
              >
                <item.icon className="size-4 shrink-0 text-primary" />
                {!collapsed && <span className="hidden md:inline">{item.label}</span>}
              </button>
            ))}
            {!collapsed && <div className="px-4 pb-1 pt-3 text-[10px] font-semibold text-muted-foreground md:block">科研工具</div>}
            {NAV.map((item) => {
              const isActive = active === item.key
              const isHyp = HYPOTHESIS_KEYS.has(item.key)
              return (
                <button
                  key={item.key}
                  data-nav-key={item.key}
                  onClick={() => setActive(item.key)}
                  className={cn(
                    "relative flex w-full items-center gap-3 px-4 py-2.5 text-[13px] font-medium transition-colors",
                    collapsed ? "justify-center px-0" : "justify-center px-0 md:justify-start md:px-4",
                    isActive
                      ? "bg-sidebar-active text-sidebar-active-foreground"
                      : "text-sidebar-foreground hover:bg-sidebar-hover hover:text-foreground",
                  )}
                  title={item.label}
                >
                  {isActive && (
                    <span className="absolute inset-y-0 left-0 w-0.5 bg-primary" />
                  )}
                  <item.icon
                    className={cn(
                      "size-4 shrink-0",
                      isHyp && !isActive && "text-purple",
                    )}
                  />
                  {!collapsed && <span className="hidden md:inline">{item.label}</span>}
                </button>
              )
            })}
          </div>
          <button
            onClick={() => setCollapsed((c) => !c)}
            className="flex h-9 items-center justify-center gap-2 border-t border-border text-xs text-muted-foreground hover:bg-sidebar-hover"
          >
            <ChevronLeft
              className={cn("size-4 transition-transform", collapsed && "rotate-180")}
            />
            {!collapsed && <span className="hidden md:inline">收起侧栏</span>}
          </button>
        </nav>

        {/* Content */}
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          <Breadcrumb active={active} />
          <div className="min-h-0 flex-1 overflow-auto p-3">
            {active === "literature" && <LiteraturePage onAskDocument={(documentId) => { setQaDocumentId(documentId); setActive("qa") }} />}
            {active === "search" && <SearchPage onUseAsHypothesis={(text) => { setHypothesisSeed({ id: Date.now(), text }); setActive("hypothesis") }} />}
            {active === "qa" && <QaPage initialDocumentId={qaDocumentId} />}
            {active === "notes" && <NotesPage />}
            {active === "glossary" && <GlossaryPage />}
            {active === "hypothesis" && <HypothesisPage seed={hypothesisSeed} />}
            {active === "hyplib" && <HypothesisLibraryPage onOpenLab={() => setActive("notes")} onOpenModeling={(hypothesisId) => { setModelingHypothesisId(hypothesisId); setActive("modeling") }} />}
            {active === "evidence" && <EvidencePage />}
            {active === "multimodal" && <MultimodalPage />}
            {active === "modeling" && <ModelingPage initialHypothesisId={modelingHypothesisId} />}
            {active === "api" && <ApiSettingsPage />}
          </div>
        </div>
      </div>
    </div>
  )
}

function Breadcrumb({ active }: { active: string }) {
  const item = NAV.find((n) => n.key === active)
  return (
    <div className="flex h-10 shrink-0 items-center gap-2 border-b border-border bg-card px-4">
      {item && <item.icon className="size-4 text-primary" />}
      <span className="text-[13px] font-semibold text-foreground">{item?.label}</span>
      <span className="ml-2 text-xs text-muted-foreground">工作台 / {item?.label}</span>
    </div>
  )
}

function StatusDotDark({ tone, label }: { tone: "green" | "red" | "gray"; label: string }) {
  const color = tone === "green" ? "bg-success" : tone === "red" ? "bg-danger" : "bg-white/40"
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-white/70">
      <span className={cn("size-2 rounded-full", color)} />
      {label}
    </span>
  )
}
