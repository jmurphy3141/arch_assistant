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
  answers:                string;
  client_id:              string;
  diagram_name:           string;
  // Stateless path: echo these back from result._clarify_context
  items_json?:            string;
  prompt?:                string;
  deployment_hints_json?: string;
  // Auto WAF: echo back from upload-bom if auto_waf=True
  auto_waf?:              boolean;
  customer_id?:           string;
  customer_name?:         string;
}

export interface JobPending {
  status: 'pending';
  job_id: string;
}

export type DiagramResult = GenerateResponse | OrchestrationResult;

export interface OrchestrationResult {
  status:        'orchestration_complete';
  agent_version: string;
  client_id:     string;
  customer_id:   string;
  diagram_name:  string;
  request_id:    string;
  draw_result:   GenerateResponse;
  waf_result: {
    version:        number;
    key:            string;
    content:        string;
    overall_rating: string;
  };
  loop_summary: {
    iterations: number;
    history: {
      iteration:         number;
      waf_rating:        string;
      applied:           number;
      draw_instructions: string[];
    }[];
  };
  errors: string[];
}

export interface RefineRequest {
  feedback:     string;
  client_id:    string;
  diagram_name: string;
  // Echo back from result._refine_context
  items_json?:             string;
  prompt?:                 string;
  prev_spec?:              string;  // JSON-encoded previous LayoutIntent
  deployment_hints_json?:  string;  // preserves multi_region_mode across refine calls
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

export type BomChatType = 'normal' | 'question' | 'final';

export interface BomConversationTurn {
  role: 'user' | 'assistant';
  content: string;
}

export interface BomChatRequest {
  message: string;
  conversation?: BomConversationTurn[];
  model_id?: string;
}

export interface BomPayloadLineItem {
  sku: string;
  description: string;
  category: string;
  quantity: number;
  unit_price: number;
  extended_price: number;
  notes?: string;
}

export interface BomPayload {
  currency: string;
  line_items: BomPayloadLineItem[];
  assumptions?: string[];
  totals?: {
    estimated_monthly_cost: number;
  };
}

export interface BomChatResponse {
  type: BomChatType;
  reply: string;
  trace_id: string;
  json_bom?: string;
  bom_payload?: BomPayload;
  score?: number;
  trace?: Record<string, unknown>;
}

export interface BomConfigResponse {
  status: string;
  default_model_id: string;
  cache: {
    ready: boolean;
    source: string;
    refreshed_at: number | null;
    pricing_sku_count: number;
  };
  allowed_types: BomChatType[];
}

export interface BomHealthResponse {
  ready: boolean;
  source: string;
  refreshed_at: number | null;
  pricing_sku_count: number;
  trace_id?: string;
}

export interface BomRefreshResponse {
  ready: boolean;
  source: string;
  pricing_sku_count: number;
  latency_ms: number;
  refreshed_at: number | null;
  trace_id?: string;
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

export async function apiHealth(): Promise<HealthResponse> {
  // Health is at /health (no /api prefix)
  const resp = await fetch('/health');
  if (!resp.ok) throw { status: resp.status, detail: await resp.text() } as ApiError;
  return resp.json() as Promise<HealthResponse>;
}

export async function apiGenerate(req: GenerateRequest): Promise<GenerateResponse> {
  return apiFetch<GenerateResponse>('/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
}

export async function apiClarify(
  req: ClarifyRequest,
): Promise<JobPending> {
  return apiFetch<JobPending>('/clarify', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
}

export async function apiRefineDiagram(req: RefineRequest): Promise<GenerateResponse> {
  return apiFetch<GenerateResponse>('/refine', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
}

export async function apiUploadBom(
  formData: FormData,
): Promise<JobPending> {
  return apiFetch<JobPending>('/upload-bom', {
    method: 'POST',
    body: formData,
  });
}

/** Poll /api/job/{job_id} every intervalMs until status is no longer "pending". */
export async function apiWaitForJob(
  jobId: string,
  onTick?: (elapsedSec: number) => void,
  intervalMs = 3000,
): Promise<DiagramResult> {
  const start = Date.now();
  while (true) {
    await new Promise<void>(r => setTimeout(r, intervalMs));
    if (onTick) onTick(Math.round((Date.now() - start) / 1000));
    const r = await apiFetch<JobPending | DiagramResult>(`/job/${jobId}`);
    if ((r as JobPending).status !== 'pending') return r as DiagramResult;
  }
}

export async function apiInputsResolve(body: object): Promise<ResolveResponse> {
  return apiFetch<ResolveResponse>('/inputs/resolve', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function apiBomConfig(): Promise<BomConfigResponse> {
  return apiFetch<BomConfigResponse>('/bom/config');
}

export async function apiBomHealth(): Promise<BomHealthResponse> {
  return apiFetch<BomHealthResponse>('/bom/health');
}

export async function apiBomChat(req: BomChatRequest): Promise<BomChatResponse> {
  return apiFetch<BomChatResponse>('/bom/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
}

export async function apiBomRefreshData(): Promise<BomRefreshResponse> {
  return apiFetch<BomRefreshResponse>('/bom/refresh-data', {
    method: 'POST',
  });
}

export async function apiBomGenerateXlsx(payload: BomPayload): Promise<Blob> {
  const resp = await fetch(`${API_BASE}/bom/generate-xlsx`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ bom_payload: payload }),
  });
  if (!resp.ok) {
    let detail = await resp.text();
    try {
      const body = JSON.parse(detail);
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body);
    } catch {
      // keep raw
    }
    throw { status: resp.status, detail } as ApiError;
  }
  return resp.blob();
}

export interface UploadToBucketResponse {
  object_key: string;
  filename:   string;
  size:       number;
  bom_type:   string;
}

/** Upload a file to OCI Object Storage. bomType controls the prefix (main | poc). */
export async function apiUploadToBucket(
  customerId: string,
  file: File,
  bomType: 'main' | 'poc' = 'main',
): Promise<UploadToBucketResponse> {
  const fd = new FormData();
  fd.append('customer_id', customerId);
  fd.append('file', file);
  fd.append('bom_type', bomType);
  return apiFetch<UploadToBucketResponse>('/upload-to-bucket', { method: 'POST', body: fd });
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

interface A2AResponse {
  task_id:       string;
  agent_id:      string;
  status:        'ok' | 'need_clarification' | 'error';
  outputs:       Record<string, unknown>;
  error_message?: string | null;
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
  const raw = await apiFetch<A2AResponse>('/a2a/task', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(task),
  });

  // A2A wraps errors as status="error" with HTTP 200 — surface them as ApiError
  if (raw.status === 'error') {
    const err: ApiError = {
      status: 500,
      detail: raw.error_message ?? 'Unknown error from drawing agent',
    };
    throw err;
  }

  // Success: outputs contains the full GenerateResponse
  return raw.outputs as unknown as GenerateResponse;
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
  trace_id?: string;
  customer_id: string;
  customer_name?: string;
  summary?: string;
  version?: number;
  key?: string;
  latest_key?: string;
  files?: string[];
  stages?: Record<string, unknown>[];
  blocking_questions?: string[];
}

export interface TerraformLatestResponse {
  status: string;
  trace_id?: string;
  customer_id: string;
  latest: {
    version: number;
    files: Record<string, string>;
    metadata?: Record<string, unknown>;
    timestamp?: string;
  };
}

export interface TerraformVersionEntry {
  version: number;
  key: string;
  timestamp: string;
  metadata: Record<string, unknown>;
}

export interface TerraformVersionsResponse {
  status: string;
  trace_id?: string;
  customer_id: string;
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
  feedback?: string,
): Promise<DocResponse> {
  return apiFetch<DocResponse>('/pov/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      customer_id: customerId,
      customer_name: customerName,
      ...(feedback?.trim() ? { feedback } : {}),
    }),
  });
}

export async function apiApprovePov(
  customerId: string,
  customerName: string,
  content: string,
): Promise<{ status: string; key: string }> {
  return apiFetch('/pov/approve', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ customer_id: customerId, customer_name: customerName, content }),
  });
}

export async function apiGetApprovedPov(customerId: string): Promise<DocLatestResponse> {
  return apiFetch<DocLatestResponse>(`/pov/${encodeURIComponent(customerId)}/approved`);
}

export async function apiGetLatestPov(customerId: string): Promise<DocLatestResponse> {
  return apiFetch<DocLatestResponse>(`/pov/${encodeURIComponent(customerId)}/latest`);
}

export async function apiListPovVersions(customerId: string): Promise<DocVersionsResponse> {
  return apiFetch<DocVersionsResponse>(`/pov/${encodeURIComponent(customerId)}/versions`);
}

export interface KickoffQuestion {
  id: string;
  question: string;
  hint: string;
  known_value: string | null;
}

export interface JepKickoffResponse {
  status: string;
  customer_id: string;
  questions: KickoffQuestion[];
  extracted: Record<string, string | null>;
  questions_key: string;
}

export interface JepQuestionsResponse {
  status: string;
  customer_id: string;
  questions?: KickoffQuestion[];
  answers?: Record<string, string>;
  timestamp?: string;
}

export async function apiJepKickoff(
  customerId: string,
  customerName: string,
): Promise<JepKickoffResponse> {
  return apiFetch<JepKickoffResponse>('/jep/kickoff', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ customer_id: customerId, customer_name: customerName }),
  });
}

export async function apiGetJepQuestions(customerId: string): Promise<JepQuestionsResponse> {
  return apiFetch<JepQuestionsResponse>(`/jep/${encodeURIComponent(customerId)}/questions`);
}

export async function apiSaveJepAnswers(
  customerId: string,
  answers: Record<string, string>,
): Promise<{ status: string; answers_saved: number }> {
  return apiFetch('/jep/answers', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ customer_id: customerId, answers }),
  });
}

export async function apiGenerateJep(
  customerId: string,
  customerName: string,
  diagramKey?: string,
  feedback?: string,
): Promise<DocResponse> {
  return apiFetch<DocResponse>('/jep/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      customer_id: customerId,
      customer_name: customerName,
      diagram_key: diagramKey || null,
      ...(feedback?.trim() ? { feedback } : {}),
    }),
  });
}

export async function apiApproveJep(
  customerId: string,
  customerName: string,
  content: string,
): Promise<{ status: string; key: string }> {
  return apiFetch('/jep/approve', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ customer_id: customerId, customer_name: customerName, content }),
  });
}

export async function apiGetApprovedJep(customerId: string): Promise<DocLatestResponse> {
  return apiFetch<DocLatestResponse>(`/jep/${encodeURIComponent(customerId)}/approved`);
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
  prompt?: string,
): Promise<TerraformResponse> {
  return apiFetch<TerraformResponse>('/terraform/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      customer_id: customerId,
      customer_name: customerName,
      ...(prompt?.trim() ? { prompt } : {}),
    }),
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

export async function apiDownloadTerraformFile(
  customerId: string,
  filename: string,
): Promise<string> {
  const url = `${API_BASE}/terraform/${encodeURIComponent(customerId)}/download/${encodeURIComponent(filename)}`;
  const resp = await fetch(url);
  if (!resp.ok) {
    throw {
      status: resp.status,
      detail: await resp.text(),
    } as ApiError;
  }
  return resp.text();
}

// ---------------------------------------------------------------------------
// Conversational orchestrator — chat
// ---------------------------------------------------------------------------

export interface ChatToolCall {
  tool:           string;
  args:           Record<string, unknown>;
  result_summary: string;
  result_data?:   Record<string, unknown>;
}

export interface ChatArtifactDownload {
  type: string;
  tool: string;
  key?: string;
  filename?: string;
  download_url: string;
}

export interface ChatArtifactManifest {
  downloads: ChatArtifactDownload[];
}

export interface ChatMessage {
  role:      'user' | 'assistant' | 'tool';
  content?:  string;
  tool?:     string;
  result_summary?: string;
  timestamp: string;
  tool_call?: { tool: string; args: Record<string, unknown> };
}

export interface ChatResponse {
  status:         string;
  trace_id?:      string;
  reply:          string;
  tool_calls:     ChatToolCall[];
  artifacts:      Record<string, string>;
  artifact_manifest?: ChatArtifactManifest;
  history_length: number;
}

export interface ChatStreamEvent {
  trace_id?: string;
  customer_id?: string;
  event_type: 'status' | 'tool' | 'token' | 'terraform_stage' | 'completion' | 'error';
  status?: string;
  delta?: string;
  tool_call?: ChatToolCall;
  stage?: Record<string, unknown>;
  error?: string;
  reply?: string;
  tool_calls?: ChatToolCall[];
  artifacts?: Record<string, string>;
  artifact_manifest?: ChatArtifactManifest;
  history_length?: number;
}

export interface ChatHistoryResponse {
  status:      string;
  customer_id: string;
  history:     ChatMessage[];
}

export interface ChatHistoryIndexItem {
  customer_id: string;
  customer_name: string;
  last_message_preview: string;
  last_activity_timestamp: string;
  status: string;
}

export interface ChatHistoryIndexResponse {
  status: string;
  trace_id?: string;
  items: ChatHistoryIndexItem[];
  pagination: {
    page: number;
    page_size: number;
    total: number;
    has_next: boolean;
  };
}

export async function apiChat(
  customerId: string,
  customerName: string,
  message: string,
): Promise<ChatResponse> {
  return apiFetch<ChatResponse>('/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      customer_id:   customerId,
      customer_name: customerName,
      message,
    }),
  });
}

export interface ChatStreamHandlers {
  onEvent?: (event: ChatStreamEvent) => void;
  onToken?: (delta: string) => void;
  onTool?: (toolCall: ChatToolCall) => void;
}

export async function apiChatStream(
  customerId: string,
  customerName: string,
  message: string,
  handlers: ChatStreamHandlers = {},
): Promise<ChatResponse> {
  const url = `${API_BASE}/chat/stream?mode=chunked`;
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      customer_id: customerId,
      customer_name: customerName,
      message,
    }),
  });

  if (!resp.ok) {
    let detail = await resp.text();
    try {
      const body = JSON.parse(detail);
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body);
    } catch {
      // keep raw text
    }
    throw { status: resp.status, detail } as ApiError;
  }

  if (!resp.body) {
    throw { status: 500, detail: 'Streaming response body is empty.' } as ApiError;
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let completion: ChatResponse | null = null;

  const handleEvent = (event: ChatStreamEvent) => {
    handlers.onEvent?.(event);

    if (event.event_type === 'token' && typeof event.delta === 'string') {
      handlers.onToken?.(event.delta);
    }
    if (event.event_type === 'tool' && event.tool_call) {
      handlers.onTool?.(event.tool_call);
    }
    if (event.event_type === 'completion') {
      completion = {
        status: 'ok',
        trace_id: event.trace_id,
        reply: event.reply ?? '',
        tool_calls: event.tool_calls ?? [],
        artifacts: event.artifacts ?? {},
        artifact_manifest: event.artifact_manifest,
        history_length: event.history_length ?? 0,
      };
    }
    if (event.event_type === 'error') {
      throw { status: 500, detail: event.error ?? 'Streaming error.' } as ApiError;
    }
  };

  const parseLine = (line: string) => {
    const trimmed = line.trim();
    if (!trimmed) return;
    try {
      handleEvent(JSON.parse(trimmed) as ChatStreamEvent);
    } catch (err) {
      if ((err as { status?: number }).status) throw err;
    }
  };

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      buffer += decoder.decode();
      break;
    }

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';

    for (const line of lines) {
      parseLine(line);
    }
  }

  parseLine(buffer);

  if (completion) return completion;
  throw { status: 500, detail: 'Streaming completed without completion event.' } as ApiError;
}

export async function apiGetChatHistory(
  customerId: string,
  maxTurns = 30,
): Promise<ChatHistoryResponse> {
  return apiFetch<ChatHistoryResponse>(
    `/chat/${encodeURIComponent(customerId)}/history?max_turns=${maxTurns}`,
  );
}

export async function apiGetChatHistoryIndex(
  page = 1,
  pageSize = 20,
  search = '',
): Promise<ChatHistoryIndexResponse> {
  return apiFetch<ChatHistoryIndexResponse>(
    `/chat/history?page=${page}&page_size=${pageSize}&search=${encodeURIComponent(search)}`,
  );
}

export async function apiClearChatHistory(customerId: string): Promise<void> {
  await apiFetch(`/chat/${encodeURIComponent(customerId)}/history`, {
    method: 'DELETE',
  });
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
