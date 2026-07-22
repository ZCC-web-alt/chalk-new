"use client"

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react"
import { apiFetch } from "@/lib/api/client"
import type { AuthResponse, User } from "@/lib/api/types"

type AuthContextValue = {
  user: User | null
  loading: boolean
  error: string | null
  login: (username: string, password: string) => Promise<void>
  register: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  refresh: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      setLoading(true)
      setError(null)
      const response = await apiFetch<AuthResponse>("/auth/me")
      setUser(response.user)
    } catch {
      setUser(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const login = useCallback(async (username: string, password: string) => {
    setError(null)
    const response = await apiFetch<AuthResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    })
    setUser(response.user)
  }, [])

  const register = useCallback(async (username: string, password: string) => {
    setError(null)
    const response = await apiFetch<AuthResponse>("/auth/register", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    })
    setUser(response.user)
  }, [])

  const logout = useCallback(async () => {
    await apiFetch<{ ok: boolean }>("/auth/logout", { method: "POST" })
    setUser(null)
  }, [])

  const value = useMemo(
    () => ({ user, loading, error, login, register, logout, refresh }),
    [user, loading, error, login, register, logout, refresh],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) throw new Error("useAuth must be used inside AuthProvider")
  return context
}
