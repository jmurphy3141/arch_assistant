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
