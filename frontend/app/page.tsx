"use client"

import { LoginScreen } from "@/components/chalk/login-screen"
import { AppShell } from "@/components/chalk/app-shell"
import { AuthProvider, useAuth } from "@/components/chalk/auth-provider"
import { LoadingState } from "@/components/chalk/api-state"

export default function Page() {
  return (
    <AuthProvider>
      <ChalkApp />
    </AuthProvider>
  )
}

function ChalkApp() {
  const { user, loading, logout } = useAuth()

  if (loading) return <LoadingState label="正在恢复 Chalk 会话..." />
  if (!user) return <LoginScreen />
  return <AppShell user={user} onLogout={logout} />
}
