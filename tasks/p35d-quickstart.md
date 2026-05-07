# Task p35d: AWS Quickstart Example — Multi-Agent SkillForge in 30 Minutes

## Goal

Create `examples/aws_quickstart/` — a fully runnable, self-contained example
that demonstrates SkillForge with a multi-agent setup (one local tool + one
A2ADelegate sub-agent), SimpleMemory, and declarative YAML registration.

This is the proof that SkillForge is genuinely reusable outside OCI. It must
work with any OpenAI-compatible or Anthropic endpoint and zero OCI credentials.

---

## Prerequisite Check

```bash
# All three primitives must be available
python3.11 -c "
from skillforge import Forge, SimpleMemory, A2ADelegate
from skillforge.types import ParallelToolCall
print('ok')
"
```

If this fails, p35g, p35h, and p35c are incomplete — stop.

---

## Scope

**Only create** (all files under `examples/aws_quickstart/`):
- `examples/aws_quickstart/README.md`
- `examples/aws_quickstart/forge_tools.yaml`
- `examples/aws_quickstart/aws_handlers.py`
- `examples/aws_quickstart/skills/size_ec2_guidance.md`
- `examples/aws_quickstart/run.py`

**Do NOT touch `agent/`, `skillforge/`, or any existing file.**

---

## Files to create

### `examples/aws_quickstart/README.md`

```markdown
# SkillForge AWS Quickstart

A minimal multi-agent example. No OCI credentials required.

## What it shows

- Registering tools declaratively via `forge_tools.yaml`
- A local tool handler (`size_ec2`)
- A remote sub-agent via `A2ADelegate` (`generate_cfn`)
- `SimpleMemory` — no persistence setup needed
- Skill guidance loaded from a `.md` file

## Requirements

    pip install skillforge httpx pyyaml anthropic  # or openai

## Configure

Set your LLM endpoint in the environment:

    export ANTHROPIC_API_KEY=sk-...       # for Anthropic
    # or
    export OPENAI_API_KEY=sk-...          # for OpenAI-compatible

## Run

    cd examples/aws_quickstart
    python run.py

## Simulated sub-agent

`generate_cfn` pretends to call a CloudFormation sub-agent at
`http://localhost:9090/generate/cfn`. The example uses a mock server
(`MockCfnServer`) so it runs without any real sub-agent process.
```

### `examples/aws_quickstart/forge_tools.yaml`

```yaml
# AWS Quickstart tool configuration
tools:
  - name: size_ec2
    handler: aws_handlers:size_ec2_handler
    memory_contract: true
    skill_guidance: skills/size_ec2_guidance.md

  - name: generate_cfn
    handler: skillforge.delegate:A2ADelegate
    handler_kwargs:
      base_url: "http://localhost:9090"
      endpoint: "/generate/cfn"
    memory_contract: false
```

### `examples/aws_quickstart/aws_handlers.py`

```python
"""
AWS-domain tool handlers for the SkillForge quickstart.
Zero OCI imports. Zero Archie imports.
"""
from __future__ import annotations
from skillforge.types import MemorySnapshot, ToolResult


async def size_ec2_handler(
    args: dict,
    *,
    memory: MemorySnapshot | None,
    context: dict,
    trace_id: str,
) -> ToolResult:
    """
    Simulates EC2 instance sizing based on workload description.
    In production, this would call the AWS Pricing API.
    """
    workload = args.get("workload", "unknown workload")
    # Simulated sizing logic
    sizing = {
        "instance_type": "m6i.2xlarge",
        "vcpu": 8,
        "memory_gb": 32,
        "estimated_monthly_usd": 280,
        "reasoning": f"Sized for: {workload}",
    }
    return ToolResult(
        summary=f"EC2 sized: m6i.2xlarge (8 vCPU, 32GB) ~$280/month for '{workload}'",
        status="ok",
        data={"facts": {"ec2_sizing": sizing}},
    )
```

### `examples/aws_quickstart/skills/size_ec2_guidance.md`

```markdown
## EC2 Sizing Guidance

Use `size_ec2` when the user describes a workload and needs instance recommendations.

**Required arg:** `workload` — describe the application tier, expected traffic,
and any specific requirements (e.g. "3-tier web app, 5k concurrent users,
stateless API tier").

**When to call:** After you understand the workload characteristics. Do not
call without at least a basic workload description.

**After sizing:** Offer to generate CloudFormation (`generate_cfn`) to
provision the recommended infrastructure.
```

### `examples/aws_quickstart/run.py`

```python
"""
AWS Quickstart — SkillForge multi-agent example.

Demonstrates:
- Declarative tool registration from forge_tools.yaml
- Local tool handler (size_ec2)
- Remote sub-agent via A2ADelegate (generate_cfn — mocked inline)
- SimpleMemory — no persistence required
- Skill guidance from .md file
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

# Allow running from this directory directly
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from skillforge import Forge, SimpleMemory
import agent.hat_engine as hat_engine  # reuse Archie's hat engine


# ── Mock sub-agent server (replaces a real CFN sub-agent for the demo) ───────

class _MockCfnResponder:
    """
    Intercepts A2ADelegate HTTP calls in-process so the example
    runs without a real sub-agent server.
    """
    async def __call__(self, args, *, memory, context, trace_id):
        from skillforge.types import ToolResult
        workload = (args.get("args") or {}).get("workload", "your workload")
        return ToolResult(
            summary=f"CloudFormation template generated for {workload}.",
            status="ok",
            artifact_key="cfn/stack.yaml",
        )


# ── LLM text runner ───────────────────────────────────────────────────────────

def _make_text_runner():
    """Build a text runner from ANTHROPIC_API_KEY or OPENAI_API_KEY."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        import anthropic
        client = anthropic.Anthropic()

        async def _run(prompt: str, system: str, label: str = "") -> str:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text
        return _run

    raise RuntimeError(
        "Set ANTHROPIC_API_KEY to run this example.\n"
        "  export ANTHROPIC_API_KEY=sk-..."
    )


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    here = Path(__file__).parent
    text_runner = _make_text_runner()

    forge = Forge(
        base_system_prompt="You are an AWS solutions architect assistant. Help the user size and provision AWS infrastructure.",
        hat_engine=hat_engine,
        memory=SimpleMemory(),
        text_runner=text_runner,
    )

    # Register tools from YAML — declarative wiring
    forge.register_tools_from_config(str(here / "forge_tools.yaml"), base_dir=str(here))

    # Replace generate_cfn handler with the mock responder (demo only)
    forge._registry._tools["generate_cfn"].handler = _MockCfnResponder()

    print("SkillForge AWS Quickstart")
    print("=" * 40)
    print("Type a message (e.g. 'Size a 3-tier web app for 5k users')")
    print("Type 'quit' to exit\n")

    context = {}
    history = []

    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in ("quit", "exit", "q"):
            break
        if not user_input:
            continue

        result = await forge.run_turn(
            session_id="aws-demo",
            user_message=user_input,
            context=context,
            history=history,
        )

        print(f"\nArchie: {result.reply}")
        if result.tool_calls:
            print(f"  [Tools used: {', '.join(tc.tool for tc in result.tool_calls)}]")
        if result.artifacts:
            print(f"  [Artifacts: {result.artifacts}]")
        print()

        history.append({"role": "user", "content": user_input})
        history.append({"role": "assistant", "content": result.reply})


if __name__ == "__main__":
    asyncio.run(main())
```

---

## Acceptance Criteria

1. `python3.11 -c "import examples.aws_quickstart.aws_handlers; print('ok')"` — exits 0
   (from repo root)
2. `python3.11 -m py_compile examples/aws_quickstart/run.py` — exits 0
3. `grep "from agent\." examples/aws_quickstart/aws_handlers.py` — no matches
   (aws_handlers.py has zero OCI/Archie imports)
4. `examples/aws_quickstart/skills/size_ec2_guidance.md` exists
5. `examples/aws_quickstart/forge_tools.yaml` is valid YAML with 2 tools

---

## Do NOT Do

- Do not add OCI imports to any file in `examples/aws_quickstart/`
- Do not require a running sub-agent process — the mock must work standalone
- Do not hardcode API keys — read from environment only

---

## Commit Message

```
p35d: add AWS quickstart example — multi-agent SkillForge in 30 minutes
```
