/**
 * api/client.ts
 * -------------
 * All API calls go through this module.
 * Base URL is read from VITE_API_BASE_URL env var (default: /api).
 * Never expose a UI field for changing the base URL.
 */

export const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '/api';

export interface ApiError {
  status: number;
  detail: string; // raw text if not JSON
}

/** Generic fetch wrapper that returns parsed JSON or throws ApiError. */
async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const url = `${API_BASE}${path}`;
  const resp = await fetch(url, init);

  if (!resp.ok) {
    // Try to parse JSON error; fall back to raw text
    let detail: string;
    const ct = resp.headers.get('content-type') ?? '';
    if (ct.includes('application/json')) {
      try {
        const body = await resp.json();
        detail =
          typeof body.detail === 'string'
            ? body.detail
            : JSON.stringify(body.detail ?? body);
      } catch {
        detail = await resp.text();
      }
    } else {
      detail = await resp.text();
    }
    const err: ApiError = { status: resp.status, detail };
    throw err;
  }

  return resp.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ObjectRef {
  namespace?: string;
  bucket: string;
  object: string;
  version_id?: string;
}

export interface GenerateRequest {
  resources?: Record<string, unknown>[];
  resources_from_bucket?: ObjectRef;
  context?: string;
  context_from_bucket?: ObjectRef;
  questionnaire?: string;
  questionnaire_from_bucket?: ObjectRef;
  notes?: string;
  notes_from_bucket?: ObjectRef;
  deployment_hints?: Record<string, unknown>;
  deployment_hints_from_bucket?: ObjectRef;
  diagram_name: string;
  client_id: string;
}

export interface ClarifyRequest {
  answers: string;
  client_id: string;
  diagram_name: string;
}

export interface GenerateResponse {
  status: 'ok' | 'need_clarification';
  agent_version: string;
  request_id: string;
  input_hash: string;
  client_id: string;
  diagram_name: string;
  render_manifest?: Record<string, unknown>;
  download?: { url: string; object_storage_latest: string };
  questions?: { id: string; question: string; blocking: boolean }[];
  errors: string[];
  [key: string]: unknown;
}

export interface HealthResponse {
  status: string;
  agent_version: string;
  agent: string;
  pending_clarifications: string[];
  idempotency_cache_size: number;
}

export interface ResolveResponse {
  status: 'ok' | 'partial';
  resolved: Record<string, unknown>;
  errors: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

export async function apiHealth(): Promise<HealthResponse> {
  return apiFetch<HealthResponse>('/health');
}

export async function apiGenerate(req: GenerateRequest): Promise<GenerateResponse> {
  return apiFetch<GenerateResponse>('/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
}

export async function apiClarify(req: ClarifyRequest): Promise<GenerateResponse> {
  return apiFetch<GenerateResponse>('/clarify', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
}

export async function apiUploadBom(formData: FormData): Promise<GenerateResponse> {
  return apiFetch<GenerateResponse>('/upload-bom', {
    method: 'POST',
    body: formData,
  });
}

export async function apiInputsResolve(body: object): Promise<ResolveResponse> {
  return apiFetch<ResolveResponse>('/inputs/resolve', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

// ---------------------------------------------------------------------------
// A2A task endpoint
// ---------------------------------------------------------------------------

export interface A2ATask {
  task_id: string;
  skill: string;
  client_id: string;
  inputs: Record<string, unknown>;
}

/** Call the A2A upload_bom skill — BOM and context fetched server-side from OCI bucket. */
export async function apiA2AUploadBom(
  customerId: string,
  bomObject: string,
  diagramName: string,
  context?: string,
  namespace = 'oraclejamescalise',
  bucket = 'agent_assistante',
): Promise<GenerateResponse> {
  const task: A2ATask = {
    task_id: crypto.randomUUID(),
    skill: 'upload_bom',
    client_id: customerId,
    inputs: {
      bom_from_bucket: { namespace, bucket, object: bomObject },
      diagram_name: diagramName,
      ...(context?.trim() ? { context } : {}),
    },
  };
  return apiFetch<GenerateResponse>('/a2a/task', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(task),
  });
}

/** Build a download URL for a named artifact. */
export function downloadUrl(
  filename: string,
  clientId: string,
  diagramName: string,
): string {
  return `${API_BASE}/download/${filename}?client_id=${encodeURIComponent(
    clientId,
  )}&diagram_name=${encodeURIComponent(diagramName)}`;
}

// ---------------------------------------------------------------------------
// Writing agents — Notes, POV, JEP
// ---------------------------------------------------------------------------

export interface NoteEntry {
  key: string;
  name: string;
  timestamp: string;
}

export interface DocVersionEntry {
  version: number;
  key: string;
  timestamp: string;
  metadata: Record<string, unknown>;
}

export interface DocResponse {
  status: string;
  agent_version: string;
  customer_id: string;
  doc_type: 'pov' | 'jep' | 'terraform' | 'waf';
  version: number;
  key: string;
  latest_key: string;
  content: string;
  bom?: Record<string, unknown>;
  diagram_key?: string;
  overall_rating?: string;
  errors: string[];
}

export interface TerraformResponse {
  status: string;
  agent_version: string;
  customer_id: string;
  doc_type: 'terraform';
  version: number;
  prefix_key: string;
  file_count: number;
  files: Record<string, string>;
  latest_key: string;
  errors: string[];
}

export interface TerraformLatestResponse {
  status: string;
  customer_id: string;
  doc_type: 'terraform';
  version: number;
  prefix_key: string;
  files: Record<string, string>;
}

export interface TerraformVersionEntry {
  version: number;
  prefix: string;
  files: Record<string, string>;
  timestamp: string;
  metadata: Record<string, unknown>;
}

export interface TerraformVersionsResponse {
  status: string;
  customer_id: string;
  doc_type: 'terraform';
  versions: TerraformVersionEntry[];
}

export interface NoteUploadResponse {
  status: string;
  key: string;
  customer_id: string;
  note_name: string;
}

export interface NoteListResponse {
  status: string;
  customer_id: string;
  notes: NoteEntry[];
}

export interface DocLatestResponse {
  status: string;
  customer_id: string;
  doc_type: string;
  content: string;
}

export interface DocVersionsResponse {
  status: string;
  customer_id: string;
  doc_type: string;
  versions: DocVersionEntry[];
}

export async function apiUploadNote(
  customerId: string,
  noteName: string,
  file: File,
): Promise<NoteUploadResponse> {
  const fd = new FormData();
  fd.append('customer_id', customerId);
  fd.append('note_name', noteName || file.name);
  fd.append('file', file);
  return apiFetch<NoteUploadResponse>('/notes/upload', { method: 'POST', body: fd });
}

export async function apiListNotes(customerId: string): Promise<NoteListResponse> {
  return apiFetch<NoteListResponse>(`/notes/${encodeURIComponent(customerId)}`);
}

export async function apiGeneratePov(
  customerId: string,
  customerName: string,
): Promise<DocResponse> {
  return apiFetch<DocResponse>('/pov/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ customer_id: customerId, customer_name: customerName }),
  });
}

export async function apiGetLatestPov(customerId: string): Promise<DocLatestResponse> {
  return apiFetch<DocLatestResponse>(`/pov/${encodeURIComponent(customerId)}/latest`);
}

export async function apiListPovVersions(customerId: string): Promise<DocVersionsResponse> {
  return apiFetch<DocVersionsResponse>(`/pov/${encodeURIComponent(customerId)}/versions`);
}

export async function apiGenerateJep(
  customerId: string,
  customerName: string,
  diagramKey?: string,
): Promise<DocResponse> {
  return apiFetch<DocResponse>('/jep/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      customer_id: customerId,
      customer_name: customerName,
      diagram_key: diagramKey || null,
    }),
  });
}

export async function apiGetLatestJep(customerId: string): Promise<DocLatestResponse> {
  return apiFetch<DocLatestResponse>(`/jep/${encodeURIComponent(customerId)}/latest`);
}

export async function apiListJepVersions(customerId: string): Promise<DocVersionsResponse> {
  return apiFetch<DocVersionsResponse>(`/jep/${encodeURIComponent(customerId)}/versions`);
}

// ---------------------------------------------------------------------------
// Terraform agent
// ---------------------------------------------------------------------------

export async function apiGenerateTerraform(
  customerId: string,
  customerName: string,
): Promise<TerraformResponse> {
  return apiFetch<TerraformResponse>('/terraform/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ customer_id: customerId, customer_name: customerName }),
  });
}

export async function apiGetLatestTerraform(
  customerId: string,
): Promise<TerraformLatestResponse> {
  return apiFetch<TerraformLatestResponse>(
    `/terraform/${encodeURIComponent(customerId)}/latest`,
  );
}

export async function apiListTerraformVersions(
  customerId: string,
): Promise<TerraformVersionsResponse> {
  return apiFetch<TerraformVersionsResponse>(
    `/terraform/${encodeURIComponent(customerId)}/versions`,
  );
}

// ---------------------------------------------------------------------------
// WAF review agent
// ---------------------------------------------------------------------------

export async function apiGenerateWaf(
  customerId: string,
  customerName: string,
): Promise<DocResponse> {
  return apiFetch<DocResponse>('/waf/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ customer_id: customerId, customer_name: customerName }),
  });
}

export async function apiGetLatestWaf(customerId: string): Promise<DocLatestResponse> {
  return apiFetch<DocLatestResponse>(`/waf/${encodeURIComponent(customerId)}/latest`);
}

export async function apiListWafVersions(customerId: string): Promise<DocVersionsResponse> {
  return apiFetch<DocVersionsResponse>(`/waf/${encodeURIComponent(customerId)}/versions`);
}
