# Sub-Agent Prompt Golden Template

Use this template for specialist system prompts in files such as:
- `agent/pov_agent.py`
- `agent/jep_agent.py`
- `agent/waf_agent.py`
- `agent/graphs/terraform_graph.py`
- `agent/critic_agent.py`

## Canonical Template

```text
You are an Oracle Cloud <specialist role>.
Primary objective: <what this agent must produce>.

Operating contract:
1. <Required behavior 1>
2. <Required behavior 2>
3. <Required behavior 3>

Quality bar:
- <Quality requirement 1>
- <Quality requirement 2>

Output contract:
- Output ONLY <markdown/json/etc>.
- No meta commentary.
- No format drift.
```

## Authoring Rules
- Keep prompts short and deterministic.
- Put strict format constraints at the end.
- Include failure behavior for underspecified inputs.
- Align with paired G-Stack skill quality bar.

## Golden Example (Critic)
See: [agent/critic_agent.py](/home/opc/drawing-agent/agent/critic_agent.py:8)
