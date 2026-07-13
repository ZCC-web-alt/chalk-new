"use client"

import type React from "react"
import { cn } from "@/lib/utils"
import { Check, Minus, type LucideIcon } from "lucide-react"

/* ---------------------------------- Panel --------------------------------- */

export function Panel({
  title,
  extra,
  icon: Icon,
  children,
  className,
  bodyClassName,
  noPadding,
}: {
  title?: React.ReactNode
  extra?: React.ReactNode
  icon?: LucideIcon
  children?: React.ReactNode
  className?: string
  bodyClassName?: string
  noPadding?: boolean
}) {
  return (
    <section
      className={cn(
        "flex min-h-0 flex-col rounded-lg border border-border bg-card",
        className,
      )}
    >
      {title != null && (
        <header className="flex h-10 shrink-0 items-center justify-between gap-2 border-b border-border px-3">
          <div className="flex items-center gap-2 text-[13px] font-semibold text-foreground">
            {Icon && <Icon className="size-4 text-primary" />}
            {title}
          </div>
          {extra && <div className="flex items-center gap-1.5">{extra}</div>}
        </header>
      )}
      <div className={cn("min-h-0 flex-1", noPadding ? "" : "p-3", bodyClassName)}>
        {children}
      </div>
    </section>
  )
}

/* --------------------------------- Button --------------------------------- */

type BtnVariant = "default" | "primary" | "purple" | "danger" | "ghost" | "text"

export function Btn({
  children,
  variant = "default",
  size = "sm",
  icon: Icon,
  active,
  className,
  ...props
}: {
  children?: React.ReactNode
  variant?: BtnVariant
  size?: "xs" | "sm"
  icon?: LucideIcon
  active?: boolean
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const variants: Record<BtnVariant, string> = {
    default:
      "border border-input bg-card text-foreground hover:border-primary hover:text-primary",
    primary:
      "border border-primary bg-primary text-primary-foreground hover:bg-primary-hover hover:border-primary-hover",
    purple:
      "border border-purple bg-purple text-white hover:opacity-90",
    danger:
      "border border-input bg-card text-danger hover:border-danger hover:bg-danger-soft",
    ghost:
      "border border-transparent text-muted-foreground hover:bg-secondary hover:text-foreground",
    text: "border border-transparent text-primary hover:bg-primary-soft",
  }
  return (
    <button
      className={cn(
        "inline-flex shrink-0 items-center justify-center gap-1.5 rounded-md font-medium whitespace-nowrap transition-colors disabled:pointer-events-none disabled:opacity-50",
        size === "xs" ? "h-7 px-2 text-xs" : "h-8 px-3 text-[13px]",
        variants[variant],
        active && "border-primary bg-primary-soft text-primary",
        className,
      )}
      {...props}
    >
      {Icon && <Icon className={size === "xs" ? "size-3.5" : "size-4"} />}
      {children}
    </button>
  )
}

/* --------------------------------- IconBtn -------------------------------- */

export function IconBtn({
  icon: Icon,
  className,
  ...props
}: { icon: LucideIcon } & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      className={cn(
        "inline-flex size-7 items-center justify-center rounded-md border border-transparent text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground",
        className,
      )}
      {...props}
    >
      <Icon className="size-4" />
    </button>
  )
}

/* ----------------------------------- Tag ---------------------------------- */

type TagTone =
  | "default"
  | "blue"
  | "purple"
  | "green"
  | "orange"
  | "red"
  | "gray"

export function Tag({
  children,
  tone = "default",
  className,
  dot,
}: {
  children: React.ReactNode
  tone?: TagTone
  className?: string
  dot?: boolean
}) {
  const tones: Record<TagTone, string> = {
    default: "border-primary/30 bg-primary-soft text-primary",
    blue: "border-primary/30 bg-primary-soft text-primary",
    purple: "border-purple/30 bg-purple-soft text-purple",
    green: "border-success/30 bg-success-soft text-success",
    orange: "border-warning/40 bg-warning-soft text-[#d48806]",
    red: "border-danger/30 bg-danger-soft text-danger",
    gray: "border-border bg-secondary text-muted-foreground",
  }
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-[11px] font-medium leading-none",
        tones[tone],
        className,
      )}
    >
      {dot && <span className="size-1.5 rounded-full bg-current" />}
      {children}
    </span>
  )
}

/* -------------------------------- StatusDot ------------------------------- */

export function StatusDot({
  tone = "green",
  label,
}: {
  tone?: "green" | "orange" | "red" | "gray" | "blue"
  label: string
}) {
  const colors: Record<string, string> = {
    green: "bg-success",
    orange: "bg-warning",
    red: "bg-danger",
    gray: "bg-muted-foreground",
    blue: "bg-primary",
  }
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
      <span className={cn("size-2 rounded-full", colors[tone])} />
      {label}
    </span>
  )
}

/* ---------------------------------- Input --------------------------------- */

export function Input({
  className,
  icon: Icon,
  ...props
}: { icon?: LucideIcon } & React.InputHTMLAttributes<HTMLInputElement>) {
  if (Icon) {
    return (
      <div className={cn("relative flex items-center", className)}>
        <Icon className="pointer-events-none absolute left-2.5 size-4 text-muted-foreground" />
        <input
          className="h-8 w-full rounded-md border border-input bg-card pl-8 pr-2.5 text-[13px] outline-none transition-colors placeholder:text-muted-foreground focus:border-primary focus:ring-2 focus:ring-primary/15"
          {...props}
        />
      </div>
    )
  }
  return (
    <input
      className={cn(
        "h-8 w-full rounded-md border border-input bg-card px-2.5 text-[13px] outline-none transition-colors placeholder:text-muted-foreground focus:border-primary focus:ring-2 focus:ring-primary/15",
        className,
      )}
      {...props}
    />
  )
}

export function Textarea({
  className,
  ...props
}: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={cn(
        "w-full resize-none rounded-md border border-input bg-card px-2.5 py-2 text-[13px] leading-relaxed outline-none transition-colors placeholder:text-muted-foreground focus:border-primary focus:ring-2 focus:ring-primary/15",
        className,
      )}
      {...props}
    />
  )
}

export function Select({
  className,
  children,
  ...props
}: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn(
        "h-8 rounded-md border border-input bg-card px-2 text-[13px] outline-none transition-colors focus:border-primary focus:ring-2 focus:ring-primary/15",
        className,
      )}
      {...props}
    >
      {children}
    </select>
  )
}

/* -------------------------------- Checkbox -------------------------------- */

export function Checkbox({
  checked,
  indeterminate,
  onChange,
  label,
  className,
  disabled,
  ariaLabel,
}: {
  checked?: boolean
  indeterminate?: boolean
  onChange?: (v: boolean) => void
  label?: React.ReactNode
  className?: string
  disabled?: boolean
  ariaLabel?: string
}) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-label={ariaLabel}
      aria-checked={indeterminate ? "mixed" : Boolean(checked)}
      disabled={disabled}
      onClick={() => onChange?.(!checked)}
      className={cn(
        "inline-flex cursor-pointer select-none items-center gap-1.5 text-[13px] text-foreground",
        disabled && "cursor-not-allowed opacity-45",
        className,
      )}
    >
      <span
        className={cn(
          "flex size-4 items-center justify-center rounded-[3px] border transition-colors",
          checked || indeterminate
            ? "border-primary bg-primary text-white"
            : "border-input bg-card enabled:hover:border-primary",
        )}
      >
        {indeterminate ? (
          <Minus className="size-3" strokeWidth={3} />
        ) : checked ? (
          <Check className="size-3" strokeWidth={3} />
        ) : null}
      </span>
      {label}
    </button>
  )
}

/* --------------------------------- Toggle --------------------------------- */

export function Toggle({
  checked,
  onChange,
}: {
  checked: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors",
        checked ? "bg-primary" : "bg-[#bfbfbf]",
      )}
    >
      <span
        className={cn(
          "inline-block size-4 rounded-full bg-white shadow-sm transition-transform",
          checked ? "translate-x-4" : "translate-x-0.5",
        )}
      />
    </button>
  )
}

/* --------------------------------- Stepper -------------------------------- */

export function Stepper({
  value,
  onChange,
  min = 0,
  max = 999,
}: {
  value: number
  onChange: (v: number) => void
  min?: number
  max?: number
}) {
  return (
    <div className="inline-flex h-8 items-stretch overflow-hidden rounded-md border border-input">
      <button
        onClick={() => onChange(Math.max(min, value - 1))}
        className="w-7 bg-secondary text-muted-foreground transition-colors hover:text-primary"
      >
        −
      </button>
      <input
        value={value}
        onChange={(e) => {
          const n = Number.parseInt(e.target.value, 10)
          if (!Number.isNaN(n)) onChange(Math.min(max, Math.max(min, n)))
        }}
        className="w-10 border-x border-input bg-card text-center text-[13px] outline-none"
      />
      <button
        onClick={() => onChange(Math.min(max, value + 1))}
        className="w-7 bg-secondary text-muted-foreground transition-colors hover:text-primary"
      >
        +
      </button>
    </div>
  )
}

/* ---------------------------------- Tabs ---------------------------------- */

export function Tabs({
  tabs,
  active,
  onChange,
  className,
}: {
  tabs: string[]
  active: string
  onChange: (t: string) => void
  className?: string
}) {
  return (
    <div
      className={cn(
        "flex items-center gap-0.5 overflow-x-auto border-b border-border",
        className,
      )}
    >
      {tabs.map((t) => (
        <button
          key={t}
          onClick={() => onChange(t)}
          className={cn(
            "relative shrink-0 px-3 py-2 text-[13px] font-medium transition-colors",
            active === t
              ? "text-primary"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {t}
          {active === t && (
            <span className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-primary" />
          )}
        </button>
      ))}
    </div>
  )
}

/* ------------------------------- ProgressBar ------------------------------ */

export function ScoreBar({
  label,
  value,
  tone = "blue",
}: {
  label: string
  value: number
  tone?: "blue" | "purple" | "green" | "orange"
}) {
  const colors: Record<string, string> = {
    blue: "bg-primary",
    purple: "bg-purple",
    green: "bg-success",
    orange: "bg-warning",
  }
  return (
    <div className="flex items-center gap-2">
      <span className="w-20 shrink-0 text-xs text-muted-foreground">{label}</span>
      <div className="h-2 flex-1 overflow-hidden rounded-full bg-secondary">
        <div
          className={cn("h-full rounded-full transition-all", colors[tone])}
          style={{ width: `${value}%` }}
        />
      </div>
      <span className="w-9 shrink-0 text-right text-xs font-semibold tabular-nums text-foreground">
        {value}
      </span>
    </div>
  )
}

/* ------------------------------- EmptyState ------------------------------- */

export function EmptyState({
  icon: Icon,
  title,
  hint,
  className,
}: {
  icon: LucideIcon
  title: string
  hint?: string
  className?: string
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-2 py-10 text-center",
        className,
      )}
    >
      <div className="flex size-12 items-center justify-center rounded-full bg-secondary">
        <Icon className="size-6 text-muted-foreground" />
      </div>
      <p className="text-[13px] font-medium text-foreground">{title}</p>
      {hint && <p className="max-w-xs text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}

/* --------------------------------- Toolbar -------------------------------- */

export function Toolbar({
  children,
  className,
}: {
  children: React.ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-2 rounded-lg border border-border bg-card px-3 py-2",
        className,
      )}
    >
      {children}
    </div>
  )
}

export function Divider() {
  return <span className="mx-0.5 h-4 w-px bg-border" />
}

/* --------------------------------- Section -------------------------------- */

export function FieldLabel({ children }: { children: React.ReactNode }) {
  return (
    <label className="mb-1 block text-xs font-medium text-muted-foreground">
      {children}
    </label>
  )
}
