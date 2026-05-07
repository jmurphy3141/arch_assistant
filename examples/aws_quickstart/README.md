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
