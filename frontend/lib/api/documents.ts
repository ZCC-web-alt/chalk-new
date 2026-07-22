import { apiFetch, type Page } from "./client"
import type { DocumentRecord } from "./types"

export type DocumentPageExcerpt = {
  documentId: number
  title: string
  pages: number[]
  text: string
  hash: string
  provenance: {
    sourceType: "pdf"
    pdfSha256: string
    extractor: "PyMuPDF"
    pageCount: number
    maxChars: number
    originalCharCount: number
    returnedCharCount: number
    truncated: boolean
  }
}

export type DocumentPageSelection = {
  documentId: number
  pages: number[]
  pdfSha256: string
  textSha256: string
  maxChars: number
}

export async function listDocumentsForEvidence() {
  return apiFetch<Page<DocumentRecord>>("/documents?pageSize=100")
}

export async function extractDocumentPages(documentId: number, pages: number[], maxChars = 12000) {
  return apiFetch<DocumentPageExcerpt>(`/documents/${documentId}/page-excerpts`, {
    method: "POST",
    body: JSON.stringify({ pages, maxChars }),
  })
}
