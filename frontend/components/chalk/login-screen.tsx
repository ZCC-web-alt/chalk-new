"use client"

import { useState } from "react"
import { Eye, EyeOff, FlaskConical, Lock, User } from "lucide-react"
import { ApiError } from "@/lib/api/client"
import { Btn, Checkbox, Input } from "./ui"
import { useAuth } from "./auth-provider"

export function LoginScreen() {
  const { login, register } = useAuth()
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [show, setShow] = useState(false)
  const [keepSignedIn, setKeepSignedIn] = useState(false)
  const [mode, setMode] = useState<"login" | "register">("login")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit() {
    setSubmitting(true)
    setError(null)
    try {
      if (mode === "login") {
        await login(username, password)
      } else {
        await register(username, password)
      }
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message)
      } else {
        setError("无法连接 Chalk Web 后端，请确认 FastAPI 服务已启动。")
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-[#f0f2f5] px-4">
      <div className="w-full max-w-[380px]">
        <div className="mb-6 flex flex-col items-center gap-2">
          <div className="flex size-14 items-center justify-center rounded-xl bg-primary text-primary-foreground shadow-sm">
            <FlaskConical className="size-7" />
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">Chalk</h1>
          <p className="text-[13px] text-muted-foreground">化工实验助手</p>
        </div>

        <div className="rounded-xl border border-border bg-card p-6 shadow-[0_2px_12px_rgba(0,0,0,0.06)]">
          <h2 className="mb-5 text-center text-base font-semibold text-foreground">
            {mode === "login" ? "登录到工作台" : "创建 Chalk 账号"}
          </h2>

          <form
            className="flex flex-col gap-4"
            onSubmit={async (event) => {
              event.preventDefault()
              await handleSubmit()
            }}
          >
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">
                用户名
              </label>
              <Input
                icon={User}
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                placeholder="请输入用户名"
                autoComplete="username"
              />
            </div>

            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">
                密码
              </label>
              <div className="relative flex items-center">
                <Lock className="pointer-events-none absolute left-2.5 size-4 text-muted-foreground" />
                <input
                  type={show ? "text" : "password"}
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  placeholder="请输入密码"
                  autoComplete={mode === "login" ? "current-password" : "new-password"}
                  className="h-8 w-full rounded-md border border-input bg-card pl-8 pr-9 text-[13px] outline-none transition-colors placeholder:text-muted-foreground focus:border-primary focus:ring-2 focus:ring-primary/15"
                />
                <button
                  type="button"
                  onClick={() => setShow((value) => !value)}
                  className="absolute right-2 text-muted-foreground hover:text-foreground"
                  aria-label={show ? "隐藏密码" : "显示密码"}
                >
                  {show ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                </button>
              </div>
            </div>

            <div className="flex items-center justify-between">
              <Checkbox
                checked={keepSignedIn}
                onChange={setKeepSignedIn}
                label="保持登录"
              />
              <button
                type="button"
                onClick={() => {
                  setMode((value) => (value === "login" ? "register" : "login"))
                  setError(null)
                }}
                className="text-[13px] text-primary hover:underline"
              >
                {mode === "login" ? "注册新账号" : "返回登录"}
              </button>
            </div>

            {error && (
              <p className="rounded-md border border-danger/25 bg-danger-soft px-2.5 py-2 text-[13px] text-danger">
                {error}
              </p>
            )}

            <Btn type="submit" variant="primary" className="mt-1 h-9 w-full" disabled={submitting}>
              {submitting ? "处理中..." : mode === "login" ? "登录" : "注册并登录"}
            </Btn>
          </form>
        </div>

        <p className="mt-6 text-center text-xs text-muted-foreground">
          Chalk · 化工文献智能分析平台 Web
        </p>
      </div>
    </main>
  )
}
