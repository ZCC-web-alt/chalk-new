import { apiFetch } from "./client"

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
  translationReviewStatus?: "reviewed" | null
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
