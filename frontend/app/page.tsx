"use client"

import { LoginScreen } from "@/components/chalk/login-screen"
import { AppShell } from "@/components/chalk/app-shell"
import { useAuth } from "@/components/chalk/auth-provider"
import { LoadingState } from "@/components/chalk/api-state"
import { useRouter, useSearchParams } from "next/navigation"
import { Suspense, useEffect } from "react"

const RETURN_PATH_BASE = "https://chalk.local"

function getResearchReturnPath(value: string | null) {
  if (!value?.startsWith("/research/general/new")) return null

  try {
    const url = new URL(value, RETURN_PATH_BASE)
    if (url.origin !== RETURN_PATH_BASE || url.pathname !== "/research/general/new") return null
    return value
  } catch {
    return null
  }
}

export default function Page() {
  return (
    <Suspense fallback={<LoadingState label="正在加载 Chalk..." />}><ChalkApp /></Suspense>
  )
}

function ChalkApp() {
  const { user, loading, logout } = useAuth()
  const router = useRouter()
  const searchParams = useSearchParams()
  const returnPath = getResearchReturnPath(searchParams.get("next"))

  useEffect(() => {
    if (!loading && user && returnPath) router.replace(returnPath)
  }, [loading, returnPath, router, user])

  if (loading) return <LoadingState label="正在恢复 Chalk 会话..." />
  if (!user) return <LoginScreen />
  if (returnPath) return <LoadingState label="正在返回跨学科工作台..." />
  return <AppShell user={user} onLogout={logout} />
}
