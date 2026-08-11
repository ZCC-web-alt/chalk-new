import { apiFetch, apiUrl } from "./client"
import type { ResearchOutput } from "./generated/research-v1"

export type Science125Question = {
  id: string
  question: string
  questionZh?: string | null
  sourceDomain: string
  benchmarkDomain: string
  pdfPage: number
  bookletPage: number
  primarySubdomain: string
  crossDomainTags: string[]
  methodProfile: Science125MethodProfile
  promptProfile: string
  retrievalProfile: string
  classificationReviewStatus: "reviewed"
}

export type Science125MethodProfile = {
  primary: string
  secondary: string[]
}

export type Science125Provider = {
  providerId: string
  displayName: string
  baseUrl: string
  policySourceUrl: string
  authMode: "none" | "optional" | "required"
  isRequired: boolean
}

export type Science125ProviderReadiness = {
  providerId: string
  ready: boolean
  status: "ready" | "degraded" | "blocked"
  missingConfigurationCodes: string[]
}

export type Science125QuestionProfile = {
  questionId: string
  routingVersion: "science125-routing-v1"
  localizationVersion?: "science125-zh-CN-v1" | null
  questionZh?: string | null
  searchIntentZh?: string | null
  recommendedQuery?: string | null
  translationReviewStatus?: "reviewed" | "translated_pending_review" | null
  promptVersion: "science125-prompts-v1" | "science125-prompts-v2"
  promptModuleHash: string
  promptReviewStatus: "draft_pending_review" | "team_reviewed"
  benchmarkDomain: string
  primarySubdomain: string
  crossDomainTags: string[]
  methodProfile: Science125MethodProfile
  promptProfile: string
  retrievalProfile: string
  classificationReviewStatus: "reviewed"
  ready: boolean
  pilotEnabled: boolean
  missingConfigurationCodes: string[]
  providers: Science125Provider[]
  providerReadiness: Science125ProviderReadiness[]
}

export type Science125QuestionContext = {
  id: string
  headline: string
  headlineZh?: string | null
  sourceContext: string
  contextSha256: string
  pdfPage: number
  bookletPage: number
  extractionVersion: string
  availability: "available"
}

export type Science125BatchStatus = "DRAFT" | "RUNNING" | "PAUSED" | "SUCCEEDED" | "FAILED" | "CANCELLED"
export type Science125ReportStatus = "PENDING" | "RETRIEVING" | "EVIDENCE_READY" | "BLOCKED_EVIDENCE" | "GENERATING" | "SUCCEEDED" | "FAILED" | "RETRYING"
export type Science125ExportFormat = "docx" | "json"

export type Science125Export = {
  exportId: string
  batchId: string
  reportId?: string | null
  format: Science125ExportFormat
  fileName: string
  mimeType: string
  sizeBytes: number
  createdAt: string
}

export type Science125ReportSummary = {
  reportId: string
  batchId: string
  questionId: string
  question: string
  questionZh?: string | null
  benchmarkDomain: string
  primarySubdomain: string
  status: Science125ReportStatus
  attemptNumber: number
  selectedHypothesisId?: string | null
  selectedHypothesisConfidence?: number | null
  selectedHypothesisReason?: string | null
  evidenceStatus: "sufficient" | "partial" | "insufficient"
  selectedEvidenceCount: number
  providerFamilies: string[]
  model?: string | null
  requestId?: string | null
  totalTokens: number
  latencyMs: number
  estimatedCostCny: number
  createdAt: string
  updatedAt: string
  sourceType: "interactive_job" | "batch"
  sourceJobId?: string | null
}

export type Science125Report = Science125ReportSummary & {
  retrievalQuery: string
  retrievalQueryZh?: string | null
  refinementQueries: string[]
  retrievalSnapshot: Record<string, unknown>
  evidenceSnapshot: Record<string, unknown>
  evidenceSnapshotSha256: string
  researchOutput?: ResearchOutput | null
  provenance?: Record<string, unknown> | null
  exportArtifacts: Science125Export[]
}

export type Science125BatchSummary = {
  batchId: string
  manifestVersion: "science125-v1"
  manifestSha256: string
  routingVersion: "science125-routing-v1"
  routingSha256: string
  promptVersion: string
  promptRegistrySha256: string
  model: string
  status: Science125BatchStatus
  totalCount: number
  succeededCount: number
  failedCount: number
  blockedEvidenceCount: number
  totalTokens: number
  estimatedCostCny: number
  startedAt?: string | null
  completedAt?: string | null
  createdAt: string
  updatedAt: string
}

export type Science125Batch = Science125BatchSummary & {
  questionIds: string[]
  reports: Science125ReportSummary[]
}

type Science125Catalog = {
  manifestVersion: "science125-v1"
  routingVersion: "science125-routing-v1"
  data: Science125Question[]
}

export async function listScience125Questions() {
  return apiFetch<Science125Catalog>("/science-125/questions")
}

export async function getScience125QuestionContext(questionId: string) {
  return apiFetch<Science125QuestionContext>(`/science-125/questions/${encodeURIComponent(questionId)}`)
}

export async function getScience125QuestionProfile(questionId: string) {
  return apiFetch<Science125QuestionProfile>(`/science-125/questions/${encodeURIComponent(questionId)}/profile`)
}

export async function createScience125Batch(questionIds?: string[]) {
  return apiFetch<Science125Batch>("/science-125/batches", {
    method: "POST",
    body: JSON.stringify({ questionIds }),
  })
}

export async function listScience125Batches() {
  return apiFetch<Science125BatchSummary[]>("/science-125/batches")
}

export async function getScience125Batch(batchId: string) {
  return apiFetch<Science125Batch>(`/science-125/batches/${encodeURIComponent(batchId)}`)
}

export async function deleteScience125Batch(batchId: string) {
  return apiFetch<void>(`/science-125/batches/${encodeURIComponent(batchId)}`, { method: "DELETE" })
}

export async function resumeScience125Batch(batchId: string) {
  return apiFetch<Science125Batch>(`/science-125/batches/${encodeURIComponent(batchId)}/resume`, { method: "POST" })
}

export async function pauseScience125Batch(batchId: string) {
  return apiFetch<Science125Batch>(`/science-125/batches/${encodeURIComponent(batchId)}/pause`, { method: "POST" })
}

export async function retryScience125Batch(batchId: string, questionIds?: string[]) {
  return apiFetch<Science125Batch>(`/science-125/batches/${encodeURIComponent(batchId)}/retries`, {
    method: "POST",
    body: JSON.stringify({ questionIds }),
  })
}

export async function listScience125Reports(params: {
  batchId?: string
  questionId?: string
  status?: string
  benchmarkDomain?: string
  sortBy?: "updatedAt" | "questionId" | "selectedHypothesisConfidence"
  sortOrder?: "asc" | "desc"
} = {}) {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value) query.set(key, value)
  }
  return apiFetch<Science125ReportSummary[]>(`/science-125/reports${query.toString() ? `?${query}` : ""}`)
}

export async function getScience125Report(reportId: string) {
  return apiFetch<Science125Report>(`/science-125/reports/${encodeURIComponent(reportId)}`)
}

export async function createScience125ReportExport(reportId: string, format: Science125ExportFormat) {
  return apiFetch<Science125Export>(`/science-125/reports/${encodeURIComponent(reportId)}/exports`, {
    method: "POST",
    body: JSON.stringify({ format }),
  })
}

export async function createScience125BatchExport(batchId: string, format: Science125ExportFormat) {
  return apiFetch<Science125Export>(`/science-125/batches/${encodeURIComponent(batchId)}/exports`, {
    method: "POST",
    body: JSON.stringify({ format }),
  })
}

export function science125ExportUrl(exportId: string) {
  return apiUrl(`/science-125/exports/${encodeURIComponent(exportId)}`)
}
