"use client"

import { usePathname, useRouter } from "next/navigation"
import { useEffect } from "react"
import { LoadingState } from "./api-state"
import { useAuth } from "./auth-provider"

export function ResearchAccess({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()
  const pathname = usePathname()
  const router = useRouter()

  useEffect(() => {
    if (!loading && !user) {
      const current = window.location.search.slice(1)
      router.replace(`/?next=${encodeURIComponent(`${pathname}${current ? `?${current}` : ""}`)}`)
    }
  }, [loading, pathname, router, user])

  if (loading) return <LoadingState label="正在恢复 Chalk 会话..." />
  if (!user) return <LoadingState label="正在转到 Chalk 登录..." />
  return <>{children}</>
}
