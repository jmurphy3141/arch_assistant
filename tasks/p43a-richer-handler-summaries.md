# Task p43a: Richer Handler Result Summaries

## Objective

The expert post-review asks the manager to check "All BOM services
represented" and "AI/ML nodes present" — but every handler returns a
one-liner summary. The reviewer has nothing to check against.

- Diagram: `"Diagram generated. Key: diagrams/x.drawio"` — no node list
- BOM: `"BOM generated with structured payload."` — no service list
- WAF: `"WAF v2 saved."` — no findings count

Enrich each summary with verifiable data already present in the handler's
result payload, so the post-review LLM can meaningfully evaluate completeness.

---

## Scope

**Touch:**
- `agent/tools/diagram.py` — add `_summarise_drawio()` helper, enrich summary
- `agent/tools/bom.py` — enrich summary with service count, names, total
- `agent/tools/specialists.py` — enrich WAF summary with findings count and P1 count

**Do NOT touch:** `skillforge/`, hat markdown files, tests, other modules.
Leave PovHandler and JepHandler summaries unchanged.

---

## Prerequisite Check

```bash
python3.11 -m compileall agent/tools/diagram.py agent/tools/bom.py agent/tools/specialists.py
grep "_summarise_drawio\|findings_summary\|service_count\|nodes:" agent/tools/diagram.py agent/tools/bom.py agent/tools/specialists.py
# must be zero matches
```

---

## Changes

### 1. `agent/tools/diagram.py`

Add a module-level helper function `_summarise_drawio` before the handler class:

```python
def _summarise_drawio(xml: str) -> str:
    """Return a brief service-inventory string parsed from drawio XML."""
    import re
    categories: dict[str, int] = {}
    for m in re.finditer(r'shape=mxgraph\.oci2\.(\w+)', xml):
        cat = m.group(1).lower()
        categories[cat] = categories.get(cat, 0) + 1
    if not categories:
        return ""
    total = sum(categories.values())
    parts = [f"{cat}×{n}" for cat, n in sorted(categories.items())]
    return f"{total} nodes: {', '.join(parts)}"
```

In the `__call__` method, after `result_data` is populated and before returning
`ToolResult`, enrich the summary:

```python
    xml = result_data.get("drawio_xml") or ""
    inventory = _summarise_drawio(xml) if xml else ""
    full_summary = f"{summary} ({inventory})" if inventory else summary
    return ToolResult(
        summary=full_summary,
        status="ok",
        artifact_key=artifact_key,
        data=result_data,
    )
```

Note: `summary` is the first return value from `_call_generate_diagram(...)`.
The original `return ToolResult(summary=summary, ...)` line is what you are
replacing.

### 2. `agent/tools/bom.py`

After `bom_payload` is constructed (after the `_extract_bom_payload` call and
all enrichment), replace the existing `summary` string construction with:

```python
    line_items = bom_payload.get("line_items") or []
    service_count = len(line_items)
    service_names = ", ".join(
        str(item.get("description") or item.get("sku") or "")[:30]
        for item in line_items[:6]
    )
    if len(line_items) > 6:
        service_names += f", +{len(line_items) - 6} more"
    monthly = bom_payload.get("monthly_total") or 0
    bom_summary = (
        f"BOM generated ({service_count} services, ${monthly:,.2f}/mo): "
        f"{service_names}."
        if service_names else
        f"BOM generated ({service_count} services, ${monthly:,.2f}/mo)."
    )
```

Then pass `bom_summary` as the `summary=` argument to `ToolResult(...)`.
Find where `summary="BOM generated with structured payload."` is currently
set and replace it.

### 3. `agent/tools/specialists.py` — WafHandler only

In `_SpecialistHandler.__call__`, after `content` is retrieved and after
`saved = self._store.save_document(...)` completes, add findings extraction
**before** the `return ToolResult(...)` call. Guard it with a check on
`self._agent_name == "waf"` so POV and JEP are unaffected:

```python
    findings_summary = ""
    if self._agent_name == "waf":
        try:
            import json as _json
            waf_data = (
                _json.loads(content)
                if content.strip().startswith("{")
                else {}
            )
            pillars = waf_data.get("pillars") or {}
            total_findings = sum(
                len(v.get("findings", []))
                for v in pillars.values()
                if isinstance(v, dict)
            )
            p1_count = sum(
                1
                for v in pillars.values()
                if isinstance(v, dict)
                for f in v.get("findings", [])
                if f.get("severity") == "P1"
            )
            if total_findings:
                findings_summary = (
                    f" {total_findings} findings ({p1_count} P1)."
                )
        except Exception:
            pass

    return ToolResult(
        summary=(
            f"{self._agent_name.upper()} v{saved.get('version')} saved."
            f"{findings_summary}"
        ),
        status="ok",
        artifact_key=key,
        data=response,
    )
```

---

## Acceptance Criteria

1. Compiles cleanly:
   ```bash
   python3.11 -m compileall agent/tools/diagram.py agent/tools/bom.py agent/tools/specialists.py
   ```

2. New symbols present:
   ```bash
   grep "_summarise_drawio\|nodes:.*×\|services.*\/mo\|findings.*P1" \
     agent/tools/diagram.py agent/tools/bom.py agent/tools/specialists.py | wc -l
   # must be ≥ 3
   ```

3. No regressions:
   ```bash
   pytest tests/ -q --tb=short -m "not live" -x
   ```

---

## Commit Message

```
p43a: richer handler summaries — node inventory, service list, WAF findings for post-review
```

Branch: `claude/p43a` (from main). Push when done.
