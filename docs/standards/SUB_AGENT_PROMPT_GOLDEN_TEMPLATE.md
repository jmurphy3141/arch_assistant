# Sub-Agent Prompt Golden Template

Use this template for specialist system prompts in files such as:
- `sub_agents/pov/system_prompt.md`
- `sub_agents/jep/system_prompt.md`
- `sub_agents/waf/system_prompt.md`
- `sub_agents/terraform/system_prompt.md`
- `agent/hats/critic.md`

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
See: [agent/hats/critic.md](/home/opc/drawing-agent/agent/hats/critic.md:1)
