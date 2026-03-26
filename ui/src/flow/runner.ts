/**
 * flow/runner.ts
 * ---------------
 * Minimal "flow runner" abstraction.
 *
 * v1: single-step — one agent produces one result.
 * Structure allows chaining: each step's output can feed the next step's input.
 */

import type { AgentDefinition } from '../agents/registry';

export interface FlowStepInput {
  [key: string]: unknown;
}

export interface FlowStepOutput {
  status: 'ok' | 'need_clarification' | 'error';
  [key: string]: unknown;
}

export interface FlowStep {
  /** Agent that handles this step. */
  agent: AgentDefinition;
  /** Human-readable label. */
  label: string;
  /** Transform the previous step's output into this step's input. */
  prepareInput: (prev: FlowStepOutput | null) => FlowStepInput;
  /** Execute the step (calls agent API). */
  execute: (input: FlowStepInput) => Promise<FlowStepOutput>;
}

export interface FlowResult {
  steps: FlowStepOutput[];
  final: FlowStepOutput;
  error?: string;
}

/**
 * Run a sequence of flow steps.
 * If any step returns status !== 'ok', execution stops and the partial result
 * is returned (allows callers to handle clarifications inline).
 */
export async function runFlow(steps: FlowStep[]): Promise<FlowResult> {
  const outputs: FlowStepOutput[] = [];
  let prev: FlowStepOutput | null = null;

  for (const step of steps) {
    const input = step.prepareInput(prev);
    const output = await step.execute(input);
    outputs.push(output);
    prev = output;

    if (output.status !== 'ok') {
      return { steps: outputs, final: output };
    }
  }

  return { steps: outputs, final: outputs[outputs.length - 1] };
}

/**
 * Build a single-step flow that calls /generate.
 * This is the v1 entry point used by the UI.
 */
export function buildGenerateStep(
  agent: AgentDefinition,
  apiCall: (input: FlowStepInput) => Promise<FlowStepOutput>,
): FlowStep {
  return {
    agent,
    label: `${agent.name} — Generate`,
    prepareInput: () => ({}),
    execute: apiCall,
  };
}
