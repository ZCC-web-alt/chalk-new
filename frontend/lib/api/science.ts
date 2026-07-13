import { apiFetch, apiRequest, toQuery, type Page } from "./client"
import type {
  DocumentMultimodalSource,
  ModelingWorkspaceChanges,
  ModelingOptions,
  ModelingStructure,
  ModelingWorkspace,
  MultimodalAsset,
  MultimodalCorrectionSet,
  MultimodalRun,
  MultimodalTablePreview,
} from "./types"


export async function uploadMultimodalAssets(files: File[]) {
  const body = new FormData()
  files.forEach((file) => body.append("files", file))
  return apiFetch<{ data: MultimodalAsset[] }>("/multimodal/assets", { method: "POST", body })
}

export async function listMultimodalAssets(pageSize = 100) {
  return apiFetch<Page<MultimodalAsset>>(`/multimodal/assets${toQuery({ pageSize })}`)
}

export async function deleteMultimodalAsset(assetId: string) {
  return apiFetch<void>(`/multimodal/assets/${assetId}`, { method: "DELETE" })
}

export async function getMultimodalTablePreview(
  assetId: string,
  sheetName: string,
  page = 1,
  pageSize = 50,
) {
  return apiFetch<MultimodalTablePreview>(
    `/multimodal/assets/${assetId}/content${toQuery({ sheetName, page, pageSize })}`,
  )
}

export async function listDocumentMultimodalSources(documentId: number) {
  return apiFetch<{ data: DocumentMultimodalSource[]; extractionStatus: string }>(
    `/documents/${documentId}/multimodal-sources`,
  )
}

export async function listMultimodalRuns(pageSize = 100) {
  return apiFetch<Page<MultimodalRun>>(`/multimodal/runs${toQuery({ pageSize })}`)
}

export async function getMultimodalRun(runId: string) {
  return apiFetch<MultimodalRun>(`/multimodal/runs/${runId}`)
}

export async function patchMultimodalRun(
  runId: string,
  expectedRevision: number,
  changes: MultimodalCorrectionSet,
) {
  return apiFetch<MultimodalRun>(`/multimodal/runs/${runId}`, {
    method: "PATCH",
    body: JSON.stringify({ expectedRevision, changes }),
  })
}

export async function getModelingOptions() {
  return apiFetch<ModelingOptions>("/modeling/options")
}

export async function listModelingWorkspaces(pageSize = 100) {
  return apiFetch<Page<ModelingWorkspace>>(`/modeling/workspaces${toQuery({ pageSize })}`)
}

export async function getModelingWorkspace(workspaceId: string) {
  return apiFetch<ModelingWorkspace>(`/modeling/workspaces/${workspaceId}`)
}

export async function patchModelingWorkspace(
  workspaceId: string,
  expectedRevision: number,
  changes: ModelingWorkspaceChanges,
  activeStructureId?: string | null,
) {
  return apiFetch<ModelingWorkspace>(`/modeling/workspaces/${workspaceId}`, {
    method: "PATCH",
    body: JSON.stringify({ expectedRevision, changes, activeStructureId }),
  })
}

export async function deleteModelingWorkspace(workspaceId: string) {
  return apiFetch<void>(`/modeling/workspaces/${workspaceId}`, { method: "DELETE" })
}

export async function uploadModelingStructure(workspaceId: string, file: File) {
  const body = new FormData()
  body.append("file", file)
  return apiFetch<ModelingStructure>(`/modeling/workspaces/${workspaceId}/structures`, {
    method: "POST",
    body,
  })
}

export async function getModelingStructure(workspaceId: string, structureId: string) {
  return apiFetch<ModelingStructure>(`/modeling/workspaces/${workspaceId}/structures/${structureId}`)
}

export async function downloadModelingArtifact(workspaceId: string, artifactId: string, fileName: string) {
  const response = await apiRequest(`/modeling/workspaces/${workspaceId}/artifacts/${artifactId}`)
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = fileName
  anchor.click()
  URL.revokeObjectURL(url)
}
