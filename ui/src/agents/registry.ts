/**
 * agents/registry.ts
 * ------------------
 * Agent fleet registry. Start with Drawing Agent (Agent 3).
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

const DRAWING_AGENT: AgentDefinition = {
  id: 'drawing-agent',
  name: 'OCI Drawing Agent',
  version: '1.5.0',
  description:
    'Generates OCI architecture draw.io diagrams from a Bill of Materials (BOM) Excel file.',
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
  DRAWING_AGENT,
  // Future agents (2–7) will be added here as they are implemented.
  // { id: 'sizing-agent', name: 'BOM Sizing + Pricing Agent', ... },
];

export function getAgent(id: string): AgentDefinition | undefined {
  return AGENT_REGISTRY.find((a) => a.id === id);
}

export const CURRENT_AGENT = DRAWING_AGENT;
