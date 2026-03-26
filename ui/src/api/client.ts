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
