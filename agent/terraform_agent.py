"""
agent/terraform_agent.py
-------------------------
Terraform code generator (Agent 6 in fleet).

Each run:
  1. Reads context/{customer_id}/context.json
  2. Identifies notes not yet incorporated by this agent
  3. Reads the latest architecture diagram from the bucket (via context)
  4. Fetches Terraform examples from Oracle GitHub repos (oracle-quickstart)
  5. Calls LLM (Grok code model when available, else standard model) to
     generate Terraform files
  6. Saves the generated files to terraform/{customer_id}/v{n}/
  7. Updates context file with this run's results

Storage
-------
  Reads:  context/{customer_id}/context.json
          notes/{customer_id}/* (new notes only, diffed against context)
          agent3/{customer_id}/*/LATEST.json (architecture diagram spec)
  Writes: terraform/{customer_id}/v{n}/main.tf
          terraform/{customer_id}/v{n}/variables.tf
          terraform/{customer_id}/v{n}/outputs.tf
          terraform/{customer_id}/v{n}/terraform.tfvars.example
          terraform/{customer_id}/MANIFEST.json
          terraform/{customer_id}/LATEST.json  (pointer to most recent version)
          context/{customer_id}/context.json (updated)
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Callable, Optional

from agent.context_store import (
    build_context_summary,
    get_new_notes,
    read_context,
    record_agent_run,
    write_context,
)
from agent.persistence_objectstore import ObjectStoreBase

logger = logging.getLogger(__name__)

AGENT_NAME = "terraform"

TERRAFORM_SYSTEM_MESSAGE = (
    "You are an expert OCI (Oracle Cloud Infrastructure) Terraform engineer. "
    "You write production-quality Terraform HCL for OCI deployments. "
    "Use the OCI Terraform provider (oracle/oci). "
    "Follow OCI best practices: use compartments, tag all resources, use security lists carefully, "
    "separate variables.tf and outputs.tf from main.tf. "
    "Generate real, working Terraform code — not pseudocode or placeholders. "
    "Use [TBD] only for values that must be filled in by the customer (OCIDs, tenancy info). "
    "Output ONLY the file contents in the exact format requested. No meta-commentary."
)

# GitHub repos searched for few-shot Terraform examples at generate-time.
# Synced from config.yaml terraform.example_repos; hardcoded here as fallback.
_EXAMPLE_REPOS = [
    "oracle-quickstart/oci-landing-zones",
    "oracle-quickstart/terraform-oci-oke",
    "ncusato/kove-terraform-oci",
]

_GITHUB_API = "https://api.github.com"
_FETCH_TIMEOUT = 5  # seconds — keep short; OCI instances may block outbound to github.com


def _github_headers() -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "arch-assistant/1.0"}
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _fetch_url(url: str) -> Optional[str]:
    """Fetch a URL; return text or None on any error."""
    try:
        req = urllib.request.Request(url, headers=_github_headers())
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        logger.debug("GitHub fetch failed %s: %s", url, exc)
        return None


def _search_github_examples(
    service_keywords: list[str],
    repos: Optional[list[str]] = None,
) -> str:
    """
    Search GitHub repos for .tf files matching service keywords.
    Returns concatenated example snippets (max ~3000 chars) or empty string.
    """
    if not service_keywords:
        return ""

    search_repos = repos if repos else _EXAMPLE_REPOS
    if not search_repos:
        return ""

    query_terms = "+".join(service_keywords[:4])  # limit query length
    repo_filter = "+".join(f"repo:{r}" for r in search_repos)
    url = (
        f"{_GITHUB_API}/search/code"
        f"?q={query_terms}+extension:tf+{repo_filter}"
        f"&per_page=3"
    )

    raw = _fetch_url(url)
    if not raw:
        return ""

    try:
        data = json.loads(raw)
        items = data.get("items", [])
    except (json.JSONDecodeError, KeyError):
        return ""

    snippets: list[str] = []
    total_len = 0
    for item in items:
        if total_len >= 3000:
            break
        raw_url = item.get("html_url", "").replace(
            "github.com", "raw.githubusercontent.com"
        ).replace("/blob/", "/")
        content = _fetch_url(raw_url)
        if content:
            chunk = content[:1000]
            snippets.append(f"# Example: {item.get('name', 'unknown')}\n{chunk}")
            total_len += len(chunk)

    return "\n\n".join(snippets) if snippets else ""


def _extract_service_keywords(context: dict, new_notes_text: str) -> list[str]:
    """Derive OCI service keywords from context and notes for GitHub search."""
    keywords: list[str] = []

    agents = context.get("agents", {})

    # Pull from diagram context
    if "diagram" in agents:
        d = agents["diagram"]
        name = d.get("diagram_name", "")
        if "oke" in name.lower() or "kubernetes" in name.lower():
            keywords.append("oke")
        if "vcn" in name.lower():
            keywords.append("vcn")

    # Pull keywords from notes
    lower_notes = new_notes_text.lower()
    service_map = {
        "kubernetes": "oke",
        "oke": "oke",
        "object storage": "object-storage",
        "database": "database",
        "adb": "autonomous-database",
        "vcn": "vcn",
        "load balancer": "load-balancer",
        "compute": "compute",
        "gpu": "gpu",
        "bastion": "bastion",
        "vault": "vault",
    }
    for needle, kw in service_map.items():
        if needle in lower_notes and kw not in keywords:
            keywords.append(kw)

    return keywords[:6]


def _read_diagram_spec(store: ObjectStoreBase, context: dict, persistence_prefix: str) -> str:
    """Try to read the draw_dict or spec.json from the latest diagram in context."""
    agents = context.get("agents", {})
    customer_id = context.get("customer_id", "")

    # Try diagram key from context
    diagram_key = None
    if "diagram" in agents:
        diagram_key = agents["diagram"].get("diagram_key")

    if not diagram_key:
        # Fall back to well-known LATEST.json location
        diagram_key = f"{persistence_prefix}/{customer_id}/LATEST.json"

    try:
        raw = store.get(diagram_key)
        latest = json.loads(raw.decode("utf-8"))
        spec_key = latest.get("artifacts", {}).get("spec.json")
        if spec_key:
            spec_raw = store.get(spec_key)
            return spec_raw.decode("utf-8")[:3000]
    except (KeyError, json.JSONDecodeError, Exception):
        pass

    return ""


def _manifest_key(customer_id: str) -> str:
    return f"terraform/{customer_id}/MANIFEST.json"


def _latest_key(customer_id: str) -> str:
    return f"terraform/{customer_id}/LATEST.json"


def _version_prefix(customer_id: str, version: int) -> str:
    return f"terraform/{customer_id}/v{version}"


def _get_next_version(store: ObjectStoreBase, customer_id: str) -> int:
    key = _manifest_key(customer_id)
    try:
        data = json.loads(store.get(key).decode("utf-8"))
        versions = data.get("versions", [])
        return (max(v["version"] for v in versions) + 1) if versions else 1
    except (KeyError, json.JSONDecodeError, ValueError):
        return 1


def _save_terraform_files(
    store: ObjectStoreBase,
    customer_id: str,
    version: int,
    files: dict[str, str],
    metadata: dict,
) -> dict:
    """
    Save terraform files for a version. Returns result dict with keys:
    version, prefix_key, file_count, files (dict name->key), latest_key
    """
    prefix = _version_prefix(customer_id, version)
    saved: dict[str, str] = {}

    for filename, content in files.items():
        key = f"{prefix}/{filename}"
        store.put(key, content.encode("utf-8"), "text/plain")
        saved[filename] = key

    # Update MANIFEST
    manifest_key = _manifest_key(customer_id)
    try:
        manifest = json.loads(store.get(manifest_key).decode("utf-8"))
    except (KeyError, json.JSONDecodeError):
        manifest = {"versions": []}

    manifest["versions"].append({
        "version":   version,
        "prefix":    prefix,
        "files":     saved,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "metadata":  metadata,
    })
    store.put(manifest_key, json.dumps(manifest, indent=2).encode("utf-8"), "application/json")

    # Update LATEST.json
    latest = {
        "schema_version": "1.0",
        "version":        version,
        "prefix":         prefix,
        "files":          saved,
        "timestamp":      datetime.now(timezone.utc).isoformat(),
    }
    store.put(
        _latest_key(customer_id),
        json.dumps(latest, indent=2).encode("utf-8"),
        "application/json",
    )

    return {
        "version":    version,
        "prefix_key": prefix,
        "file_count": len(saved),
        "files":      saved,
        "latest_key": _latest_key(customer_id),
    }


def _get_latest_terraform(store: ObjectStoreBase, customer_id: str) -> Optional[str]:
    """Return combined content of LATEST version files, or None."""
    try:
        raw = store.get(_latest_key(customer_id))
        latest = json.loads(raw.decode("utf-8"))
        parts: list[str] = []
        for fname, fkey in latest.get("files", {}).items():
            try:
                content = store.get(fkey).decode("utf-8")
                parts.append(f"# {fname}\n{content}")
            except KeyError:
                pass
        return "\n\n".join(parts) if parts else None
    except (KeyError, json.JSONDecodeError):
        return None


def generate_terraform(
    customer_id: str,
    customer_name: str,
    store: ObjectStoreBase,
    text_runner: Callable[[str, str], str],
    *,
    persistence_prefix: str = "agent3",
    example_repos: Optional[list[str]] = None,
) -> dict:
    """
    Generate or update Terraform files for a customer's OCI deployment.

    Args:
        customer_id:        Customer identifier — bucket key prefix.
        customer_name:      Human-readable customer name.
        store:              ObjectStoreBase instance.
        text_runner:        callable(prompt: str, system_message: str) -> str.
        persistence_prefix: Bucket prefix used by Agent 3 diagrams.
        example_repos:      GitHub repos to search for Terraform examples.
                            Overrides _EXAMPLE_REPOS when provided.

    Returns dict with keys:
        version (int), prefix_key (str), file_count (int), files (dict),
        latest_key (str), content (str), context (dict)
    """
    # ── Read context + diff new notes ─────────────────────────────────────────
    context = read_context(store, customer_id, customer_name)
    if customer_name and not context.get("customer_name"):
        context["customer_name"] = customer_name

    new_note_keys, new_notes_text = get_new_notes(store, context, AGENT_NAME)
    context_summary = build_context_summary(context)

    # ── Read diagram spec from bucket ─────────────────────────────────────────
    diagram_spec = _read_diagram_spec(store, context, persistence_prefix)

    # ── Fetch GitHub examples ─────────────────────────────────────────────────
    keywords = _extract_service_keywords(context, new_notes_text)
    repos = example_repos if example_repos is not None else _EXAMPLE_REPOS
    logger.info("Fetching Terraform examples for keywords=%s repos=%s", keywords, repos)
    github_examples = _search_github_examples(keywords, repos=repos)
    if github_examples:
        logger.info("Fetched %d chars of GitHub examples", len(github_examples))

    # ── Previous Terraform version ────────────────────────────────────────────
    previous_terraform = _get_latest_terraform(store, customer_id)

    # ── Build prompt sections ─────────────────────────────────────────────────
    sections: list[str] = []

    if context_summary:
        sections.append(context_summary)

    if new_notes_text:
        sections.append(
            "New meeting notes (not yet incorporated):\n"
            f"{new_notes_text[:3000]}"
        )
    elif not previous_terraform:
        sections.append("(No meeting notes — generate skeleton Terraform based on context.)")
    else:
        sections.append("(No new notes — review and refine existing Terraform if needed.)")

    if diagram_spec:
        sections.append(
            "Architecture diagram spec (JSON — use to infer OCI resource types):\n"
            "```json\n"
            f"{diagram_spec}\n"
            "```"
        )

    if github_examples:
        sections.append(
            "Reference examples from Oracle QuickStart repos (use as style guide):\n"
            "```hcl\n"
            f"{github_examples}\n"
            "```"
        )

    if previous_terraform:
        sections.append(
            "Previous Terraform version (update and improve — do not repeat verbatim):\n"
            "```hcl\n"
            f"{previous_terraform[:2500]}\n"
            "```"
        )

    context_block = "\n\n".join(sections)

    instructions = (
        "Update the existing Terraform to incorporate new notes and diagram changes."
        if previous_terraform else
        "This is the first Terraform for this customer. Write a complete, working draft."
    )

    prompt = f"""\
Generate Terraform HCL for an OCI deployment.

Customer: {customer_name}

{context_block}

{instructions}

Output EXACTLY four fenced code blocks, each preceded by a comment line with the filename:

// FILE: main.tf
```hcl
<content of main.tf>
```

// FILE: variables.tf
```hcl
<content of variables.tf>
```

// FILE: outputs.tf
```hcl
<content of outputs.tf>
```

// FILE: terraform.tfvars.example
```hcl
<content of terraform.tfvars.example>
```

Requirements for main.tf (MUST be non-empty — always include at minimum the provider block and a VCN resource):
- Provider block: oracle/oci, version ~> 5.0
- terraform {{ required_providers {{ oci = {{ source = "oracle/oci", version = "~> 5.0" }} }} }}
- All resources must have freeform_tags with "managed-by" = "terraform" and "customer" = "{customer_name}"
- Use compartment_id = var.compartment_id throughout
- Include VCN, subnets, security lists based on the diagram and notes
- Include compute / GPU instances, load balancers, databases as indicated
- If no specific resources are given, at minimum generate provider + VCN + two subnets as a skeleton

Requirements for variables.tf:
- tenancy_ocid, compartment_id, region as required variables
- All tunable values (shapes, CIDR blocks, counts) as variables with sensible defaults

Requirements for outputs.tf:
- Output key resource OCIDs and IPs

Requirements for terraform.tfvars.example:
- Show all variables with example/placeholder values
- Comment each variable with a description
"""

    # ── Generate ──────────────────────────────────────────────────────────────
    logger.info(
        "Generating Terraform: customer_id=%s new_notes=%d", customer_id, len(new_note_keys)
    )
    raw_content = text_runner(prompt, TERRAFORM_SYSTEM_MESSAGE)

    # ── Parse generated files from LLM output ────────────────────────────────
    files = _parse_terraform_files(raw_content)

    # ── Get next version + persist ────────────────────────────────────────────
    version = _get_next_version(store, customer_id)
    result = _save_terraform_files(
        store,
        customer_id,
        version,
        files,
        {"customer_name": customer_name, "file_count": len(files)},
    )

    # ── Update + write context ────────────────────────────────────────────────
    context = record_agent_run(
        context,
        AGENT_NAME,
        new_note_keys,
        {
            "version":    result["version"],
            "prefix_key": result["prefix_key"],
            "file_count": result["file_count"],
        },
    )
    write_context(store, customer_id, context)

    result["content"] = raw_content
    result["context"] = context
    logger.info("Terraform saved: version=%d prefix=%s", result["version"], result["prefix_key"])
    return result


def _parse_terraform_files(raw: str) -> dict[str, str]:
    """
    Parse LLM output into individual .tf files.

    Expects blocks like:
        // FILE: main.tf
        ```hcl
        ...content...
        ```
    Falls back to putting everything in main.tf if parsing fails.
    """
    import re

    expected = ["main.tf", "variables.tf", "outputs.tf", "terraform.tfvars.example"]
    files: dict[str, str] = {}

    # Try structured extraction: // FILE: fname\n```...\n<content>\n```
    for fname in expected:
        pattern = rf"//\s*FILE:\s*{re.escape(fname)}\s*\n```[^\n]*\n(.*?)```"
        m = re.search(pattern, raw, re.DOTALL | re.IGNORECASE)
        if m:
            files[fname] = m.group(1).strip()

    # Also try: ```hcl\n// FILE: fname\n<content>\n``` (label inside the fence)
    for fname in expected:
        if not files.get(fname):
            pattern = rf"```[^\n]*\n//\s*FILE:\s*{re.escape(fname)}\s*\n(.*?)```"
            m = re.search(pattern, raw, re.DOTALL | re.IGNORECASE)
            if m:
                files[fname] = m.group(1).strip()

    # If we got main.tf with actual content, fill missing stubs and return
    if files.get("main.tf"):
        for fname in expected:
            if fname not in files:
                files[fname] = f"# {fname} — not generated\n"
        return files

    # Fallback: look for fenced blocks in order; only fill files still missing/empty.
    # Skip blocks that are just // FILE: label lines with no real code.
    def _is_label_only(block: str) -> bool:
        lines = [l for l in block.strip().splitlines() if l.strip()]
        return len(lines) <= 1 and bool(re.match(r"//\s*FILE:", block.strip()))

    blocks = re.findall(r"```(?:hcl|terraform)?\n(.*?)```", raw, re.DOTALL)
    block_iter = iter(blocks)
    for fname in expected:
        if not files.get(fname):
            block = next(block_iter, None)
            # Skip label-only blocks (e.g. just "// FILE: main.tf") looking for real code
            while block is not None and _is_label_only(block):
                block = next(block_iter, None)
            if block and block.strip():
                files[fname] = block.strip()

    # Final fallback: dump everything into main.tf
    if not files.get("main.tf"):
        files["main.tf"] = raw
        for fname in expected[1:]:
            if not files.get(fname):
                files[fname] = f"# {fname} — see main.tf\n"

    return files


def get_latest_terraform_files(store: ObjectStoreBase, customer_id: str) -> dict:
    """
    Return the latest Terraform version info and file contents.

    Returns dict with keys: version, files (dict name -> content), prefix_key
    Raises KeyError if no Terraform has been generated yet.
    """
    raw = store.get(_latest_key(customer_id))
    latest = json.loads(raw.decode("utf-8"))
    files: dict[str, str] = {}
    for fname, fkey in latest.get("files", {}).items():
        try:
            files[fname] = store.get(fkey).decode("utf-8")
        except KeyError:
            files[fname] = ""
    return {
        "version":    latest["version"],
        "prefix_key": latest["prefix"],
        "files":      files,
    }


def list_terraform_versions(store: ObjectStoreBase, customer_id: str) -> list[dict]:
    """Return all Terraform version entries from MANIFEST.json."""
    try:
        raw = store.get(_manifest_key(customer_id))
        return json.loads(raw.decode("utf-8")).get("versions", [])
    except (KeyError, json.JSONDecodeError):
        return []
