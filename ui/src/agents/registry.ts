/**
 * agents/registry.ts
 * ------------------
 * Archie agent registry.
 * Structure supports chaining multiple agents in future flow steps.
 */

export interface AgentDefinition {
  id: string;
  name: string;
  version: string;
  description: string;
  /** API endpoint paths this agent exposes (relative, no /api prefix) */
  endpoints: {
    generate?: string;
    uploadBom?: string;
    clarify?: string;
    health?: string;
    download?: string;
    inputsResolve?: string;
  };
}

const ARCHIE_AGENT: AgentDefinition = {
  id: 'archie',
  name: 'Archie',
  version: '1.9.1',
  description:
    'Coordinates OCI architecture diagrams, BOMs, documents, Terraform, and WAF review.',
  endpoints: {
    generate: '/generate',
    uploadBom: '/upload-bom',
    clarify: '/clarify',
    health: '/health',
    download: '/download',
    inputsResolve: '/inputs/resolve',
  },
};

/** All registered agents, ordered by fleet number. */
export const AGENT_REGISTRY: AgentDefinition[] = [
  ARCHIE_AGENT,
  // Future agents (2–7) will be added here as they are implemented.
  // { id: 'sizing-agent', name: 'BOM Sizing + Pricing Agent', ... },
];

export function getAgent(id: string): AgentDefinition | undefined {
  return AGENT_REGISTRY.find((a) => a.id === id);
}

export const CURRENT_AGENT = ARCHIE_AGENT;
