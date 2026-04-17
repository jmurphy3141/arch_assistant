/**
 * MSW request handlers — default happy-path responses.
 * Individual tests override with server.use(...) as needed.
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';

const BASE = '/api';

// ---------------------------------------------------------------------------
// Diagram agent fixtures
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Notes fixtures
// ---------------------------------------------------------------------------

export const NOTE_UPLOAD_RESPONSE = {
  status: 'ok',
  key: 'notes/test_customer/2024-01-01T00:00:00Z_meeting_notes.txt',
  customer_id: 'test_customer',
  note_name: 'meeting_notes',
};

export const NOTE_LIST_RESPONSE = {
  status: 'ok',
  customer_id: 'test_customer',
  notes: [
    { key: 'notes/test_customer/2024-01-01_note.txt', name: 'note.txt', timestamp: '2024-01-01T00:00:00Z' },
  ],
};

// ---------------------------------------------------------------------------
// POV / JEP fixtures
// ---------------------------------------------------------------------------

const DOC_CONTENT = '# Test Document\n\n## Section One\n\nSome **bold** content here.\n';

export const POV_GENERATE_RESPONSE = {
  status: 'ok',
  agent_version: '1.3.2',
  customer_id: 'test_customer',
  doc_type: 'pov',
  version: 1,
  key: 'pov/test_customer/v1/pov.md',
  latest_key: 'pov/test_customer/LATEST.json',
  content: DOC_CONTENT,
  errors: [],
};

export const POV_LATEST_RESPONSE = {
  status: 'ok',
  customer_id: 'test_customer',
  doc_type: 'pov',
  content: DOC_CONTENT,
};

export const POV_VERSIONS_RESPONSE = {
  status: 'ok',
  customer_id: 'test_customer',
  doc_type: 'pov',
  versions: [
    { version: 1, key: 'pov/test_customer/v1/pov.md', timestamp: '2024-01-01T00:00:00Z', metadata: {} },
    { version: 2, key: 'pov/test_customer/v2/pov.md', timestamp: '2024-02-01T00:00:00Z', metadata: {} },
  ],
};

export const JEP_GENERATE_RESPONSE = { ...POV_GENERATE_RESPONSE, doc_type: 'jep', key: 'jep/test_customer/v1/jep.md' };
export const JEP_LATEST_RESPONSE   = { ...POV_LATEST_RESPONSE,   doc_type: 'jep' };
export const JEP_VERSIONS_RESPONSE = { ...POV_VERSIONS_RESPONSE, doc_type: 'jep' };

// ---------------------------------------------------------------------------
// Terraform fixtures
// ---------------------------------------------------------------------------

export const TERRAFORM_FILES = {
  'main.tf': 'terraform {\n  required_providers {\n    oci = { source = "oracle/oci", version = "~> 5.0" }\n  }\n}\n\nresource "oci_core_vcn" "main" {\n  compartment_id = var.compartment_id\n  cidr_block     = "10.0.0.0/16"\n}',
  'variables.tf': 'variable "tenancy_ocid"   { description = "Tenancy OCID" }\nvariable "compartment_id" { description = "Compartment OCID" }\nvariable "region"         { default = "us-phoenix-1" }',
  'outputs.tf': 'output "vcn_id" { value = oci_core_vcn.main.id }',
  'terraform.tfvars.example': '# tenancy_ocid   = "[TBD]"\n# compartment_id = "[TBD]"\nregion = "us-phoenix-1"',
};

export const TERRAFORM_GENERATE_RESPONSE = {
  status: 'ok',
  agent_version: '1.3.2',
  customer_id: 'test_customer',
  doc_type: 'terraform',
  version: 1,
  prefix_key: 'terraform/test_customer/v1',
  file_count: 4,
  files: TERRAFORM_FILES,
  latest_key: 'terraform/test_customer/LATEST.json',
  errors: [],
};

export const TERRAFORM_LATEST_RESPONSE = {
  status: 'ok',
  customer_id: 'test_customer',
  doc_type: 'terraform',
  version: 1,
  prefix_key: 'terraform/test_customer/v1',
  files: TERRAFORM_FILES,
};

export const TERRAFORM_VERSIONS_RESPONSE = {
  status: 'ok',
  customer_id: 'test_customer',
  doc_type: 'terraform',
  versions: [
    { version: 1, prefix: 'terraform/test_customer/v1', files: {}, timestamp: '2024-01-01T00:00:00Z', metadata: {} },
  ],
};

// ---------------------------------------------------------------------------
// WAF fixtures
// ---------------------------------------------------------------------------

export const WAF_CONTENT = [
  '# WAF Review — Test Customer',
  '',
  '## Operational Excellence',
  'Strong processes in place.',
  '',
  '## Security',
  'Good security posture.',
  '',
  '## Reliability',
  'HA design implemented.',
  '',
  '## Performance Efficiency',
  'Right-sized compute.',
  '',
  '## Cost Optimization',
  'Reserved instances recommended.',
  '',
  '## Sustainability',
  'Efficient resource usage.',
  '',
  '**Overall Rating: ✅**',
].join('\n');

export const WAF_GENERATE_RESPONSE = {
  status: 'ok',
  agent_version: '1.3.2',
  customer_id: 'test_customer',
  doc_type: 'waf',
  version: 1,
  key: 'waf/test_customer/v1/review.md',
  latest_key: 'waf/test_customer/LATEST.json',
  content: WAF_CONTENT,
  overall_rating: '✅',
  errors: [],
};

export const WAF_LATEST_RESPONSE = {
  status: 'ok',
  customer_id: 'test_customer',
  doc_type: 'waf',
  content: WAF_CONTENT,
};

export const WAF_VERSIONS_RESPONSE = {
  status: 'ok',
  customer_id: 'test_customer',
  doc_type: 'waf',
  versions: [
    { version: 1, key: 'waf/test_customer/v1/review.md', timestamp: '2024-01-01T00:00:00Z', metadata: {} },
  ],
};

// ---------------------------------------------------------------------------
// Handler registry
// ---------------------------------------------------------------------------

export const handlers = [
  // Diagram agent
  http.get(`${BASE}/health`, () => HttpResponse.json(HEALTH_RESPONSE)),
  http.post(`${BASE}/generate`, () => HttpResponse.json(GENERATE_OK_RESPONSE)),
  http.post(`${BASE}/clarify`, () => HttpResponse.json({ ...GENERATE_OK_RESPONSE, status: 'ok' })),
  http.post(`${BASE}/upload-bom`, () => HttpResponse.json(GENERATE_OK_RESPONSE)),
  http.post(`${BASE}/inputs/resolve`, () => HttpResponse.json({ status: 'ok', resolved: {}, errors: {} })),

  // Notes
  http.post(`${BASE}/notes/upload`, () => HttpResponse.json(NOTE_UPLOAD_RESPONSE)),
  http.get(`${BASE}/notes/:customerId`, () => HttpResponse.json(NOTE_LIST_RESPONSE)),

  // POV
  http.post(`${BASE}/pov/generate`, () => HttpResponse.json(POV_GENERATE_RESPONSE)),
  http.get(`${BASE}/pov/:customerId/latest`, () => HttpResponse.json(POV_LATEST_RESPONSE)),
  http.get(`${BASE}/pov/:customerId/versions`, () => HttpResponse.json(POV_VERSIONS_RESPONSE)),

  // JEP
  http.post(`${BASE}/jep/generate`, () => HttpResponse.json(JEP_GENERATE_RESPONSE)),
  http.get(`${BASE}/jep/:customerId/latest`, () => HttpResponse.json(JEP_LATEST_RESPONSE)),
  http.get(`${BASE}/jep/:customerId/versions`, () => HttpResponse.json(JEP_VERSIONS_RESPONSE)),

  // Terraform
  http.post(`${BASE}/terraform/generate`, () => HttpResponse.json(TERRAFORM_GENERATE_RESPONSE)),
  http.get(`${BASE}/terraform/:customerId/latest`, () => HttpResponse.json(TERRAFORM_LATEST_RESPONSE)),
  http.get(`${BASE}/terraform/:customerId/versions`, () => HttpResponse.json(TERRAFORM_VERSIONS_RESPONSE)),

  // WAF
  http.post(`${BASE}/waf/generate`, () => HttpResponse.json(WAF_GENERATE_RESPONSE)),
  http.get(`${BASE}/waf/:customerId/latest`, () => HttpResponse.json(WAF_LATEST_RESPONSE)),
  http.get(`${BASE}/waf/:customerId/versions`, () => HttpResponse.json(WAF_VERSIONS_RESPONSE)),
];

export const server = setupServer(...handlers);
