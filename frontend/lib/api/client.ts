export type ApiErrorBody = {
  error: {
    code: string
    message: string
    details?: unknown
  }
}

export class ApiError extends Error {
  code: string
  status: number
  details?: unknown

  constructor(status: number, body: ApiErrorBody) {
    super(body.error.message)
    this.name = "ApiError"
    this.status = status
    this.code = body.error.code
    this.details = body.error.details
  }
}

export type Pagination = {
  page: number
  pageSize: number
  totalItems: number
  totalPages: number
}

export type Page<T> = {
  data: T[]
  pagination: Pagination
}

const API_BASE = process.env.NEXT_PUBLIC_CHALK_API_BASE ?? "http://127.0.0.1:8000/api"

export function apiUrl(path: string) {
  return `${API_BASE}${path}`
}

export async function apiRequest(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json")
  }
  const response = await fetch(apiUrl(path), {
    ...init,
    headers,
    credentials: "include",
  })
  if (!response.ok) {
    let body: ApiErrorBody = {
      error: {
        code: "HTTP_ERROR",
        message: `Request failed with status ${response.status}`,
      },
    }
    try {
      body = (await response.json()) as ApiErrorBody
    } catch {
      // Keep the fallback error body.
    }
    throw new ApiError(response.status, body)
  }
  return response
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await apiRequest(path, init)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export function toQuery(params: Record<string, string | number | boolean | null | undefined>) {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") query.set(key, String(value))
  }
  const text = query.toString()
  return text ? `?${text}` : ""
}
