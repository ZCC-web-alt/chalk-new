import { apiFetch, toQuery, type Page } from "./client"
import type { HypothesisFeedbackPayload, Job, JobResource } from "./types"


const TERMINAL_STATUSES = new Set(["SUCCEEDED", "FAILED", "CANCELLED"])

export async function createJob<T = unknown>(type: string, payload: Record<string, unknown>) {
  return apiFetch<Job<T>>("/jobs", {
    method: "POST",
    body: JSON.stringify({ type, payload }),
  })
}

export async function getJob<T = unknown>(jobId: string) {
  return apiFetch<Job<T>>(`/jobs/${jobId}`)
}

export async function cancelJob<T = unknown>(jobId: string) {
  return apiFetch<Job<T>>(`/jobs/${jobId}/cancel`, { method: "POST" })
}

export async function submitHypothesisFeedback<T = unknown>(jobId: string, payload: HypothesisFeedbackPayload) {
  return apiFetch<Job<T>>(`/jobs/${jobId}/feedback`, {
    method: "POST",
    body: JSON.stringify(payload),
  })
}

export async function listJobs<T = unknown>(params: { type?: string; status?: string; pageSize?: number; science125Id?: string; science125Scope?: "all" | "unbound" } = {}) {
  return apiFetch<Page<Job<T>>>(`/jobs${toQuery({ pageSize: params.pageSize ?? 20, type: params.type, status: params.status, science125Id: params.science125Id, science125Scope: params.science125Scope })}`)
}

export async function getLatestActiveJob<T = unknown>(type: string, options: { science125Id?: string } = {}) {
  const response = await listJobs<T>({
    type,
    pageSize: 100,
    science125Id: options.science125Id,
    science125Scope: options.science125Id ? undefined : "unbound",
  })
  return response.data.find((job) => (
    job.status === "QUEUED" || job.status === "RUNNING" || job.status === "WAITING_FOR_FEEDBACK"
  )) ?? null
}

export async function getLatestCompletedJob<T = unknown>(type: string, options: { science125Id?: string } = {}) {
  const response = await listJobs<T>({
    type,
    status: "SUCCEEDED",
    pageSize: 100,
    science125Id: options.science125Id,
    science125Scope: options.science125Id ? undefined : "unbound",
  })
  return response.data[0] ?? null
}

export async function getActiveJobForResource<T = unknown>(type: string, resource: JobResource) {
  const response = await listJobs<T>({ type, pageSize: 100 })
  return response.data.find((job) => (
    (job.status === "QUEUED" || job.status === "RUNNING" || job.status === "WAITING_FOR_FEEDBACK")
    && resourcesMatch(job.resource, resource)
  )) ?? null
}

function resourcesMatch(left: JobResource | null, right: JobResource) {
  if (!left) return false
  if (left.analysisType !== right.analysisType) return false
  if (left.documentId !== right.documentId) return false
  const leftIds = [...(left.documentIds ?? [])].sort((a, b) => a - b)
  const rightIds = [...(right.documentIds ?? [])].sort((a, b) => a - b)
  if (leftIds.length !== rightIds.length || !leftIds.every((value, index) => value === rightIds[index])) return false
  const leftSourceIds = [...(left.sourceDocumentIds ?? [])].sort((a, b) => a - b)
  const rightSourceIds = [...(right.sourceDocumentIds ?? [])].sort((a, b) => a - b)
  return (
    leftSourceIds.length === rightSourceIds.length
    && leftSourceIds.every((value, index) => value === rightSourceIds[index])
    && left.hypothesisId === right.hypothesisId
    && left.artifactKind === right.artifactKind
    && left.domain === right.domain
    && left.science125Id === right.science125Id
  )
}

export async function waitForJob<T>(
  initialJob: Job<T>,
  options: {
    signal?: AbortSignal
    intervalMs?: number
    onUpdate?: (job: Job<T>) => void
  } = {},
): Promise<Job<T>> {
  const { signal, intervalMs = 1000, onUpdate } = options
  let job = initialJob
  onUpdate?.(job)
  while (!TERMINAL_STATUSES.has(job.status)) {
    await abortableDelay(intervalMs, signal)
    job = await getJob<T>(job.id)
    onUpdate?.(job)
  }
  return job
}

function abortableDelay(milliseconds: number, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Polling aborted", "AbortError"))
      return
    }
    const onAbort = () => {
      window.clearTimeout(timeout)
      reject(new DOMException("Polling aborted", "AbortError"))
    }
    const timeout = window.setTimeout(() => {
      signal?.removeEventListener("abort", onAbort)
      resolve()
    }, milliseconds)
    signal?.addEventListener("abort", onAbort, { once: true })
  })
}
