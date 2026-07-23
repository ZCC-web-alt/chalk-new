import type { ResearchOutput } from "./generated/research-v1"

export type User = {
  id: number
  username: string
  createdAt: string
}

export type AuthResponse = {
  user: User
}

export type DocumentRecord = {
  id: number
  title: string
  sourceType: string
  fileName: string | null
  summary: string | null
  createdAt: string
}

export type GlossaryTermRecord = {
  id: number
  enTerm: string
  zhTerm: string
  note: string | null
  createdAt: string
}

export type LabRecord = {
  id: number
  title: string
  content: string
  relatedDocIds: string | null
  aiSuggestion: string | null
  createdAt: string
  updatedAt: string
}

export type HypothesisArtifact = {
  id: string
  kind: "interactive_report_html" | "agent_trace_json" | "workflow_package_zip" | string
  fileName: string
  mimeType: string
  sizeBytes: number
  createdAt: string
  updatedAt: string
}

export type HypothesisSummary = {
  id: number
  title: string
  researchQuestion: string | null
  confidence: number
  feasibility: string
  iterationCount: number
  sourceDocumentIds: number[]
  domain: string
  tags: string[]
  status: string
  createdAt: string
  updatedAt: string
}

export type HypothesisDetail = HypothesisSummary & {
  hypothesis: Record<string, unknown>
  sourceDocuments: Array<Record<string, unknown>>
  iterations: unknown[]
  critiqueHistory: unknown[]
  reasoningChain: unknown
  debateHistory: unknown[]
  hitl: Record<string, unknown>
  verification: Record<string, unknown>
  scientificEvidence: Record<string, unknown>
  multimodalEvidence: Record<string, unknown>
  quantitativeReport: Record<string, unknown>
  multimodalRuns: Array<Record<string, unknown>>
  workflow: Record<string, unknown>
  artifacts: HypothesisArtifact[]
}

export type HypothesisGenerationResult = {
  hypothesisId?: number
  summary?: {
    title: string
    confidence: number
    feasibility: string
    status: string
  }
  warnings?: string[]
  artifacts?: HypothesisArtifact[]
  researchOutput?: ResearchOutput
  audit?: {
    contractVersion: "research-v1"
    provider: string
    model: string
    requestId: string
    totalTokens: number
    latencyMs: number
    retryCount: number
    estimatedCostCny: number
    schemaRepaired: boolean
    policyHash: string
    evidenceSnapshotHash: string
    evidenceCount: number
    providerFamilies: string[]
  }
}

export type HypothesisFeedbackPrompt = {
  kind: "hypothesis_review"
  round: number
  stage: "initial" | "iteration"
  hypothesis: Record<string, unknown>
  critique: Record<string, unknown>
  debate: Record<string, unknown>
  evidenceSummary: Record<string, unknown>
  reviewContext: Record<string, unknown>
  history: Record<string, unknown>
}

export type HypothesisFeedbackPayload = {
  action: "approve" | "revise" | "skip"
  feedbackText?: string
  editedHypothesis?: Record<string, unknown>
  debateStance?: "neutral" | "devil" | "optimist" | "custom"
  selectedAttackIndices?: number[]
  structuredFeedback?: {
    citationAuthenticity?: "verified" | "needsVerification" | "suspectedInvalid"
    overExtension?: "none" | "minor" | "major"
    falsifiability?: "falsifiable" | "needsCriteria" | "notFalsifiable"
    experimentFeasibility?: "feasible" | "needsAdjustment" | "infeasible"
    baselineNeed?: "none" | "recommended" | "required"
    referenceReplacement?: "none" | "recommended" | "required"
  }
}

export type EvidenceResponse = {
  data: Record<string, unknown>[]
  pagination: {
    page: number
    pageSize: number
    totalItems: number
    totalPages: number
  }
  graph: {
    nodes: unknown[]
    edges: unknown[]
  }
}

export type JobStatus = "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELLED" | "WAITING_FOR_FEEDBACK"

export type JobResource = {
  analysisType?: DocumentAnalysisType
  documentId?: number
  documentIds?: number[]
  sourceDocumentIds?: number[]
  domain?: string
  hypothesisId?: number
  recordId?: number
  artifactKind?: string
  sourceIds?: string[]
  sourceType?: string
  workspaceId?: string
  requestHash?: string
  science125Id?: string
}

export type Job<T = unknown> = {
  id: string
  type: string
  status: JobStatus
  stage: string | null
  progress: number
  message: string
  result: T | null
  error: { code: string; message: string; details?: unknown } | null
  feedbackPrompt: HypothesisFeedbackPrompt | null
  resource: JobResource | null
  createdAt: string
  updatedAt: string
}

export type RagCitation = {
  index: number
  documentId: number
  chunkId: number
  sourceTitle: string
  excerpt: string
  score: number
  contextMarkers: string
}

export type RagQaResult = {
  answer: string
  chunks: Record<string, unknown>[]
  citations: RagCitation[]
}

export type LiteratureSearchResult = {
  id: string
  title: string
  authors: string
  journal: string
  year: string
  doi: string
  abstract: string
  sourcePlatform: string
  providerFamily?: string
  url: string
  isOpenAccess: boolean
  relevanceScore: number
  relevanceLabel?: "high" | "medium" | "low" | "very_low"
  relevanceBreakdown?: {
    scoringVersion: "science125-relevance-v1"
    titleCoverage: number
    abstractCoverage: number
    conceptCoverage: number
    phraseMatch: number
    evidenceCompleteness: number
    matchedConcepts: string[]
  }
  accessStatus: string
  needsFulltext: boolean
  warning: string
}

export type LiteratureSearchJobResult = {
  results: LiteratureSearchResult[]
  relevanceScoringVersion?: "science125-relevance-v1"
  platformStatus: Record<string, Record<string, unknown>>
  warnings: string[]
  evidenceStatus?: "ready_for_review" | "evidence_insufficient"
  providerDiagnostics?: Array<Record<string, unknown>>
  policyHashes?: Record<string, string>
  query: Record<string, unknown>
}

export type DocumentAnalysisType = "summary" | "images" | "structures" | "safety" | "sop" | "reactions" | "translation"

export type DocumentAnalysisRecord<T = Record<string, unknown>> = {
  id: string
  type: DocumentAnalysisType | "comparison"
  documentIds: number[]
  result: T
  jobId: string | null
  createdAt: string
  updatedAt: string
}

export type SummaryAnalysisResult = {
  summary: string
  segmentCount: number
  sourceCharCount: number
}

export type AnalysisImage = {
  assetId: string
  fileName: string
  mimeType: string
}

export type ImageAnalysisResult = {
  images: AnalysisImage[]
  count: number
  usedLlmPageHints: boolean
}

export type ChemicalInfo = {
  name: string
  cid?: number | null
  iupacName?: string
  molecularFormula?: string
  molecularWeight?: string
  cas?: string
  canonicalSmiles?: string
  inchi?: string
  inchiKey?: string
  meltingPoint?: string
  boilingPoint?: string
  density?: string
  solubility?: string
  appearance?: string
  pubchemUrl?: string
  assetId?: string
}

export type StructureAnalysisResult = {
  chemicals: string[]
  compounds: ChemicalInfo[]
  warnings: string[]
}

export type SafetyChemical = {
  name: string
  source: "local" | "pubchem"
  status: string
  signalWord: string
  ghsCodes: string[]
  pictograms: string[]
  hazards: string[]
  highRisk: boolean
  warning: string
}

export type SafetyAnalysisResult = {
  chemicals: SafetyChemical[]
  highRiskNames: string[]
  warnings: string[]
}

export type SopAnalysisResult = {
  title: string
  chemicals: Array<{ name: string; amount: string; role: string; safety: string }>
  steps: Array<{ step: number; action: string; params: string; safetyNote: string }>
  postProcessing: string
  characterization: string
  segmentCount?: number
  sourceCharCount?: number
}

export type ReactionItem = {
  name: string
  reactants: string[]
  products: string[]
  catalyst: string
  solvent: string
  temperature: string
  time: string
  pressure: string
  ph: string
  yield: string
  workup: string
  notes: string
}

export type ReactionAnalysisResult = {
  summary: string
  reactions: ReactionItem[]
  segmentCount?: number
  sourceCharCount?: number
}

export type TranslationAnalysisResult = {
  segments: Array<{ index: number; sourceText: string; translation: string }>
  glossary: Array<{ en: string; zh: string; note: string }>
  segmentCount: number
  sourceCharCount: number
}

export type ComparisonAnalysisResult = {
  markdown: string
  documents: Array<{ id: number; title: string }>
  sourceCharCounts: Record<string, number>
}

export type MultimodalAsset = {
  id: string
  fileName: string
  mimeType: string
  assetType: "image" | "workbook"
  metadata: {
    width?: number
    height?: number
    format?: string
    frameCount?: number
    sheetNames?: string[]
    sheetCount?: number
    sheetRows?: Record<string, number>
    sheetColumns?: Record<string, number>
    rows?: number
    dataRows?: number
    columns?: number
    sizeBytes?: number
    warnings?: string[]
  }
  sha256: string
  createdAt: string
}

export type DocumentMultimodalSource = {
  id: string
  documentId: number
  fileName: string
  mimeType: string
  analysisType: string
  contentUrl: string
  createdAt: string
}

export type MultimodalDataPoint = {
  parameter: string
  parameterCn?: string
  value: number | string
  unit: string
  originalValue?: string
  originalUnit?: string
  source?: string
  isSuspicious?: boolean
  suspiciousReason?: string
  precision?: number
  note?: string
}

export type MultimodalAnnotation = {
  id: string
  x: number
  y: number
  width: number
  height: number
  note: string
  dataPointIndex?: number
}

export type MultimodalRunItem = {
  status: "succeeded" | "failed"
  source: {
    sourceType: "upload" | "documentAsset"
    assetId: string
    documentId?: number
    fileName?: string
    mimeType?: string
    documentTitle?: string
  }
  imageType?: string
  analysisMarkdown?: string
  summary?: string
  dataPoints: MultimodalDataPoint[]
  coverage?: Record<string, unknown> | Array<Record<string, unknown>>
  warnings: string[]
  annotations: MultimodalAnnotation[]
  error?: { code: string; message: string }
}

export type MultimodalRunResult = {
  summary: {
    total: number
    succeeded: number
    failed: number
    dataPoints: number
    associations: number
  }
  items: MultimodalRunItem[]
  associations: Array<Record<string, unknown>>
  quantitative: {
    summary?: Record<string, unknown>
    scalingRelations?: Array<Record<string, unknown>>
    correlations?: Array<Record<string, unknown>>
  }
  context: string
  evidence: Record<string, unknown>
  warnings: string[]
}

export type MultimodalRun = {
  id: string
  question: string
  options: Record<string, unknown>
  sources: Array<Record<string, unknown>>
  result: MultimodalRunResult
  originalResult?: MultimodalRunResult
  revision: number
  jobId: string | null
  createdAt: string
  updatedAt: string
}

export type MultimodalJobResult = MultimodalRunResult & { runId: string }

export type MultimodalCorrectionSet = {
  itemCorrections: Array<{
    itemIndex: number
    dataPoints?: MultimodalDataPoint[]
    annotations?: MultimodalAnnotation[]
  }>
}

export type MultimodalTablePreview = {
  assetId: string
  sheetName: string
  columns: string[]
  rows: Array<Array<string | number | boolean | null>>
  pagination: {
    page: number
    pageSize: number
    totalItems: number
    totalPages: number
  }
  coverage: {
    rowsRead: number
    columnsRead: number
    columnsTruncated: boolean
    strategy: string
  }
}

export type ModelingOptions = {
  calcTypes: Array<{ id: string; name: string }>
  vaspkitTasks: Array<{ id: string; name: string }>
  limits: { manualTextChars: number; structureBytes: number }
}

export type ModelingField = {
  value: string | number | boolean
  source: "default" | "manual" | "literature" | "hypothesis" | "user" | string
  warnings?: string[]
}

export type ModelingPotcarElement = {
  element: string
  potential: string
  source: string
  warnings?: string[]
}

export type ModelingWorkspaceChanges = {
  incar?: Record<string, ModelingField>
  kpoints?: { mode: string; mesh: number[]; source?: string }
  potcarElements?: Array<{ element: string; potential: string; source?: string; warnings?: string[] }>
  notes?: string
}

export type StructureGeometry = {
  formula: string
  density: number
  cell: number[][]
  lattice: Record<string, number>
  atoms: Array<{
    index: number
    element: string
    fractional: [number, number, number]
    cartesian: [number, number, number]
  }>
  bonds: Array<{ from: number; to: number; distance: number }>
  bondsOmitted?: boolean
  bondsTruncated?: boolean
}

export type ModelingStructure = {
  id: string
  workspaceId: string
  fileName: string
  format: string
  warnings: string[]
  geometry?: StructureGeometry
  createdAt: string
}

export type ModelingArtifact = {
  id: string
  workspaceId: string
  revision: number
  kind: string
  fileName: string
  mimeType: string
  createdAt: string
}

export type ModelingRevision = {
  id: string
  revision: number
  changes: Record<string, unknown>
  diff: Array<{ path: string; before: unknown; after: unknown }>
  createdAt: string
}

export type ModelingResult = {
  mode: "vasp" | "ms" | "both"
  calcType?: string
  systemName?: string
  functional?: string
  isMetal?: boolean
  incar?: Record<string, ModelingField>
  kpoints?: { mode: string; mesh: number[]; source: string; warnings?: string[] }
  potcarElements?: ModelingPotcarElement[]
  structureSuggestion?: Record<string, unknown>
  missingParams?: string[]
  notes?: string
  files?: { incar?: string; kpoints?: string; potcarGuide?: string }
  vaspkit?: { task: string; name: string; steps: string[] }
  msGuide?: Record<string, unknown>
  workflow?: Record<string, unknown>
  atomate2DryRun?: Record<string, unknown>
  coverage?: Record<string, unknown>
  riskNotices?: string[]
  warnings?: string[]
  structureImport?: { candidates: number; imported: number; warnings: string[] }
}

export type ModelingWorkspace = {
  id: string
  source: Record<string, unknown>
  mode: "vasp" | "ms" | "both"
  result: ModelingResult
  originalResult: ModelingResult
  revision: number
  activeStructureId: string | null
  jobId: string | null
  structures: ModelingStructure[]
  artifacts: ModelingArtifact[]
  revisions: ModelingRevision[]
  createdAt: string
  updatedAt: string
}

export type ModelingGenerateResult = {
  workspaceId: string
  revision: number
  coverage: Record<string, unknown>
  warnings: string[]
}

export type ModelingExportResult = {
  workspaceId: string
  artifact: ModelingArtifact
  warnings: string[]
}
