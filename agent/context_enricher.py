"""
agent/context_enricher.py
--------------------------
Parallel pre-turn context enrichment for Archie.

Three lightweight workers run concurrently via asyncio.gather before
forge.run_turn() fires. Each worker is pure Python (no LLM calls) and
completes in <5ms under normal conditions. A 200ms per-worker timeout
ensures enrichment never blocks a turn. Failed or timed-out workers are
silently skipped — enrichment is additive and best-effort.

Public API:
    async def enrich_turn_context(user_message, context, decision_context) -> dict
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CASE_STUDIES_PATH = Path(__file__).resolve().parent / "standards" / "oracle_customer_case_studies.json"
_WORKER_TIMEOUT = 0.2  # seconds — pure Python workers should finish in <5ms

# Domain keywords that indicate a substantive SA turn worth enriching
_DOMAIN_KEYWORDS = {
    "diagram", "architecture", "bom", "pricing", "cost", "poc", "pov", "waf",
    "terraform", "review", "migrate", "migration", "deploy", "deployment",
    "database", "db", "oke", "kubernetes", "genai", "ai", "ml", "rag",
    "chatbot", "streaming", "vault", "vcn", "subnet", "security", "compliance",
    "firewall", "load balancer", "object storage", "jep", "proposal", "tenant",
    "customer", "client", "workload", "production", "pilot", "performance",
}


def _is_conversational_turn(user_message: str) -> bool:
    """Return True for short/conversational messages that don't need enrichment."""
    msg = user_message.strip().lower()
    if len(msg.split()) < 8:
        return True
    return not any(kw in msg for kw in _DOMAIN_KEYWORDS)


# ---------------------------------------------------------------------------
# Worker 1 — Reference Architecture Matcher
# ---------------------------------------------------------------------------

def _worker_ref_arch(user_message: str, context: dict[str, Any]) -> dict[str, Any] | None:
    try:
        import agent.reference_architecture as _ref_mod
        from agent import context_store

        archie = context_store.get_archie_state(context)
        facts = str(archie.get("facts_summary", "") or "").strip()
        infra = str(archie.get("infrastructure_profile", "") or "").strip()
        combined_text = "\n".join(t for t in (user_message, facts, infra) if t)

        sel = _ref_mod.select_reference_architecture(text=combined_text)
        if not sel.reference_family or sel.reference_confidence < 0.4:
            return None

        constraints = sel.family_constraints or {}
        required = constraints.get("required_containers", [])
        connectors = constraints.get("connector_lanes", [])

        lines = [f"Best match: {sel.reference_label} ({sel.reference_family})"]
        lines.append(f"Confidence: {sel.reference_confidence:.0%} | Mode: {sel.reference_mode}")
        if required:
            lines.append(f"Required subnets/containers: {', '.join(str(r) for r in required[:6])}")
        if connectors:
            lines.append(f"Connector lanes: {', '.join(str(c) for c in connectors[:4])}")

        return {"family": sel.reference_family, "label": sel.reference_label,
                "confidence": sel.reference_confidence, "text": "\n".join(lines)}
    except Exception:
        logger.debug("ref_arch worker failed", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Worker 2 — Case Study Scorer
# ---------------------------------------------------------------------------

def _score_case_study(study: dict[str, Any], tokens: set[str]) -> int:
    """Simple keyword overlap score."""
    score = 0
    for field in ("industry", "use_case_type", "summary"):
        val = str(study.get(field, "") or "").lower()
        score += sum(1 for t in tokens if t in val)
    services = [str(s).lower() for s in (study.get("oci_services") or [])]
    score += sum(1 for t in tokens if any(t in s for s in services))
    return score


def _worker_case_studies(user_message: str, decision_context: dict[str, Any]) -> dict[str, Any] | None:
    try:
        if not _CASE_STUDIES_PATH.exists():
            return None

        data = json.loads(_CASE_STUDIES_PATH.read_text(encoding="utf-8"))
        studies = data.get("case_studies", [])
        if not studies:
            return None

        pain = str(decision_context.get("pain_statement") or user_message or "").lower()
        tokens = {w for w in pain.split() if len(w) > 3} | {
            kw for kw in _DOMAIN_KEYWORDS if kw in pain
        }
        if not tokens:
            return None

        scored = sorted(
            ((s, _score_case_study(s, tokens)) for s in studies),
            key=lambda x: x[1],
            reverse=True,
        )
        top = [(s, sc) for s, sc in scored[:2] if sc > 0]
        if not top:
            return None

        lines = []
        for study, _ in top:
            outcomes = study.get("quantified_outcomes", {}) or {}
            metric = (
                outcomes.get("cost_savings_percent")
                or outcomes.get("performance")
                or outcomes.get("cost_savings_annual_usd")
                or "see case study"
            )
            if isinstance(metric, str) and len(metric) > 60:
                metric = metric[:57] + "..."
            lines.append(
                f"• {study.get('company')} ({study.get('industry')}) — "
                f"{study.get('use_case_type')}: {metric}"
            )

        return {"studies": [s for s, _ in top], "text": "\n".join(lines)}
    except Exception:
        logger.debug("case_study worker failed", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Worker 3 — Pre-Flight Risk Checker
# ---------------------------------------------------------------------------

def _worker_preflight_risks(
    user_message: str,
    context: dict[str, Any],
    decision_context: dict[str, Any],
) -> dict[str, Any] | None:
    try:
        risks: list[dict[str, str]] = []

        # Surface medium/high-risk assumptions from decision_context
        for assumption in (decision_context.get("assumptions") or []):
            if not isinstance(assumption, dict):
                continue
            risk_level = str(assumption.get("risk", "low") or "low").lower()
            if risk_level in ("medium", "high"):
                level = "P1" if risk_level == "high" else "P2"
                stmt = str(assumption.get("statement", "") or "").strip()
                reason = str(assumption.get("reason", "") or "").strip()
                if stmt:
                    risks.append({"level": level, "risk": stmt, "recommendation": reason})

        # Diagram sufficiency gate
        msg_lc = user_message.lower()
        diagram_intent = any(kw in msg_lc for kw in ("diagram", "architecture", "draw", "topology", "vcn"))
        if diagram_intent:
            try:
                from agent import archie_memory
                sufficient = archie_memory._diagram_has_sufficient_context(
                    context=context, args={}, user_message=user_message
                )
                if not sufficient:
                    risks.append({
                        "level": "P2",
                        "risk": "Diagram requested but topology intent is ambiguous.",
                        "recommendation": "Confirm: subnet layout, tier count, HA mode, and any hybrid connectivity requirements before generating.",
                    })
            except Exception:
                pass

        if not risks:
            return None

        lines = [f"[{r['level']}] {r['risk']}" + (f" — {r['recommendation']}" if r['recommendation'] else "") for r in risks]
        return {"risks": risks, "text": "\n".join(lines)}
    except Exception:
        logger.debug("preflight worker failed", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

async def enrich_turn_context(
    user_message: str,
    context: dict[str, Any],
    decision_context: dict[str, Any],
) -> dict[str, Any]:
    """
    Run 3 enrichment workers in parallel and return a dict with any results.
    Returns {} for conversational turns (intent guard) or when all workers
    return no signal. Never raises — all errors are caught internally.
    """
    if _is_conversational_turn(user_message):
        return {}

    async def _run(fn, *args):
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(fn, *args),
                timeout=_WORKER_TIMEOUT,
            )
        except (asyncio.TimeoutError, Exception):
            logger.debug("enrichment worker timed out or failed")
            return None

    ref_result, case_result, risk_result = await asyncio.gather(
        _run(_worker_ref_arch, user_message, context),
        _run(_worker_case_studies, user_message, decision_context),
        _run(_worker_preflight_risks, user_message, context, decision_context),
    )

    enrichment: dict[str, Any] = {}
    if ref_result:
        enrichment["ref_arch"] = ref_result
    if case_result:
        enrichment["case_studies"] = case_result
    if risk_result:
        enrichment["preflight_risks"] = risk_result

    if enrichment:
        logger.info(
            "[ENRICHMENT] injected blocks: %s",
            ", ".join(enrichment.keys()),
        )

    return enrichment
