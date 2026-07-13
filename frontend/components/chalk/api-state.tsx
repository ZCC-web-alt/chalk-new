"use client"

import { AlertTriangle, Loader2, SearchX } from "lucide-react"
import { Btn, EmptyState } from "./ui"

export function LoadingState({ label = "正在加载数据..." }: { label?: string }) {
  return (
    <div className="flex h-full min-h-40 items-center justify-center gap-2 text-sm text-muted-foreground">
      <Loader2 className="size-4 animate-spin" />
      {label}
    </div>
  )
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex h-full min-h-40 flex-col items-center justify-center gap-3 text-center">
      <div className="flex size-10 items-center justify-center rounded-full bg-danger-soft text-danger">
        <AlertTriangle className="size-5" />
      </div>
      <p className="max-w-sm text-[13px] text-muted-foreground">{message}</p>
      {onRetry && (
        <Btn size="xs" onClick={onRetry}>
          重试
        </Btn>
      )}
    </div>
  )
}

export function NoDataState({ title = "暂无数据", hint, className }: { title?: string; hint?: string; className?: string }) {
  return <EmptyState icon={SearchX} title={title} hint={hint} className={className} />
}
