# skills/

Skill files are markdown documents that provide per-tool guidance injected
into the LLM prompt before each tool invocation.

Each file should contain:
- A brief description of what the tool does and when to use it
- Constraints or rules the LLM should follow when calling this tool
- Examples of good vs. bad tool arguments (optional)

Register a skill file with:
    forge.register_tool("my_tool", handler, skill_guidance="skills/my_tool.md")

Or via YAML config:
    - name: my_tool
      handler: my_module:MyHandler
      skill_guidance: skills/my_tool.md
