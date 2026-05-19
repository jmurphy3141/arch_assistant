# skills/

Skill files are markdown documents injected into the LLM prompt to guide behavior.

## Global skill files

Registered with `forge.register_skill_file(path)` — injected into the system prompt
on every turn, before tool-specific guidance.

```python
forge.register_skill_file("skills/intent_routing.md")
```

Or via YAML config:
```yaml
global_skills:
  - skills/intent_routing.md
```

## Per-tool skill files

Registered with the `skill_guidance` parameter — injected only when that tool is about
to be called.

```python
forge.register_tool("generate_bom", handler, skill_guidance="skills/bom_guidance.md")
```

Or via YAML config:
```yaml
tools:
  - name: generate_bom
    handler: agent.tools.bom:BomHandler
    skill_guidance: skills/bom_guidance.md
```

## Active skill files

| File | Scope | Purpose |
|------|-------|---------|
| `intent_routing.md` | global | When to respond conversationally vs. call a tool |
| `SKILL_TEMPLATE.md` | reference | Full format reference for hat and skill files |

## Skill file format

Each file is plain markdown. No required structure — write whatever guidance helps
the LLM make better decisions for that tool or domain.

For hat files (which also serve as expert skill files), see `SKILL_TEMPLATE.md` for
the full section format including Quality Bar, Pre-Action Checklist, and Post-Action Review.
