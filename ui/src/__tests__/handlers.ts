/**
 * MSW request handlers — default happy-path responses.
 * Individual tests override with server.use(...) as needed.
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';

const BASE = '/api';

export const HEALTH_RESPONSE = {
  status: 'ok',
  agent_version: '1.3.2',
  agent: 'oci-drawing-agent',
  pending_clarifications: [],
  idempotency_cache_size: 0,
};

export const GENERATE_OK_RESPONSE = {
  status: 'ok',
  agent_version: '1.3.2',
  request_id: 'aabbccdd-0000-0000-0000-112233445566',
  input_hash: 'a'.repeat(64),
  client_id: 'test-client',
  diagram_name: 'test_diag',
  render_manifest: { page: { width: 1654, height: 1169 }, node_count: 3 },
  download: {
    url: '/api/download/diagram.drawio?client_id=test-client&diagram_name=test_diag',
    object_storage_latest: 'diagrams/test-client/test_diag/LATEST.json',
  },
  errors: [],
};

export const CLARIFY_RESPONSE = {
  status: 'need_clarification',
  agent_version: '1.3.2',
  request_id: 'clarify-req-id',
  input_hash: 'b'.repeat(64),
  client_id: 'test-client',
  diagram_name: 'test_diag',
  questions: [
    { id: 'ha.ads', question: 'How many ADs per region?', blocking: true },
  ],
  errors: [],
};

export const handlers = [
  http.get(`${BASE}/health`, () => HttpResponse.json(HEALTH_RESPONSE)),

  http.post(`${BASE}/generate`, () => HttpResponse.json(GENERATE_OK_RESPONSE)),

  http.post(`${BASE}/clarify`, () =>
    HttpResponse.json({ ...GENERATE_OK_RESPONSE, status: 'ok' }),
  ),

  http.post(`${BASE}/upload-bom`, () => HttpResponse.json(GENERATE_OK_RESPONSE)),

  http.post(`${BASE}/inputs/resolve`, () =>
    HttpResponse.json({ status: 'ok', resolved: {}, errors: {} }),
  ),
];

export const server = setupServer(...handlers);
