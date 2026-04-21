from __future__ import annotations

from dataclasses import asdict, dataclass
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REQUIRED_PATHS = (
    "diagram",
    "pov",
    "jep",
    "waf",
    "terraform",
    "summary_document",
)

REQUIRED_SECTIONS = (
    "Intent",
    "Preconditions",
    "Input Validation Rules",
    "Expected Output Contract",
    "Pushback Rules",
    "Escalation Questions Template",
    "Retry Guidance",
)


@dataclass(frozen=True)
class OrchestratorSkillDecision:
    path_id: str
    phase: str  # preflight | postflight
    status: str  # allow | block
    reasons: list[str]
    pushback_message: str
    retry_instructions: list[str]


class OrchestratorSkillPackError(RuntimeError):
    pass


class OrchestratorSkillEngine:
    def __init__(self, skill_root: Path | None = None) -> None:
        self.skill_root = skill_root or (Path(__file__).resolve().parent / "orchestrator_skills")
        self._loaded = False
        self._skills: dict[str, dict[str, str]] = {}

    def _load_required_skills(self) -> None:
        loaded: dict[str, dict[str, str]] = {}
        for path_id in REQUIRED_PATHS:
            skill_path = self.skill_root / path_id / "SKILL.md"
            try:
                text = skill_path.read_text(encoding="utf-8")
            except Exception as exc:
                raise OrchestratorSkillPackError(
                    f"Missing or unreadable skill file for path='{path_id}': {skill_path} ({exc})"
                ) from exc

            sections = self._parse_sections(text)
            missing = [name for name in REQUIRED_SECTIONS if not sections.get(name, "").strip()]
            if missing:
                raise OrchestratorSkillPackError(
                    f"Malformed skill file for path='{path_id}': missing sections={missing}"
                )
            loaded[path_id] = sections

        self._skills = loaded
        self._loaded = True

    @staticmethod
    def _parse_sections(text: str) -> dict[str, str]:
        # Canonical contract uses level-2 headings exactly matching REQUIRED_SECTIONS.
        pattern = re.compile(r"^##\s+(.+?)\s*$", flags=re.MULTILINE)
        matches = list(pattern.finditer(text))
        sections: dict[str, str] = {}
        for i, match in enumerate(matches):
            name = match.group(1).strip()
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            sections[name] = text[start:end].strip()
        return sections

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._load_required_skills()

    @staticmethod
    def _allow(path_id: str, phase: str) -> OrchestratorSkillDecision:
        return OrchestratorSkillDecision(
            path_id=path_id,
            phase=phase,
            status="allow",
            reasons=[],
            pushback_message="",
            retry_instructions=[],
        )

    @staticmethod
    def _block(
        *,
        path_id: str,
        phase: str,
        reasons: list[str],
        pushback_message: str,
        retry_instructions: list[str],
    ) -> OrchestratorSkillDecision:
        return OrchestratorSkillDecision(
            path_id=path_id,
            phase=phase,
            status="block",
            reasons=reasons,
            pushback_message=pushback_message,
            retry_instructions=retry_instructions,
        )

    def preflight_check(
        self,
        path_id: str,
        user_message: str,
        context_summary: str,
        current_state: dict[str, Any],
    ) -> OrchestratorSkillDecision:
        try:
            self._ensure_loaded()
        except OrchestratorSkillPackError as exc:
            logger.error("Orchestrator skill pack fail-closed during preflight: %s", exc)
            return self._block(
                path_id=path_id,
                phase="preflight",
                reasons=[str(exc)],
                pushback_message=(
                    "I cannot run this path safely right now because the orchestrator skill pack "
                    "is unavailable or malformed."
                ),
                retry_instructions=[
                    "Restore all required SKILL.md files under agent/orchestrator_skills/.",
                    "Retry after the skill pack integrity issue is fixed.",
                ],
            )

        args = current_state.get("args", {}) if isinstance(current_state, dict) else {}
        msg = (user_message or "").lower()
        ctx = (context_summary or "").lower()

        if path_id == "diagram":
            bom_text = str(args.get("bom_text", "")).strip()
            if not bom_text and "bom" not in msg:
                return self._block(
                    path_id=path_id,
                    phase="preflight",
                    reasons=["No BOM or equivalent diagram input provided."],
                    pushback_message=(
                        "I can generate the diagram once I have BOM content. "
                        "Please upload or paste BOM/resource details first."
                    ),
                    retry_instructions=[
                        "Provide bom_text with OCI resources or upload a BOM file.",
                        "Then retry generate_diagram.",
                    ],
                )

        if path_id in {"pov", "jep"}:
            no_context = "no engagement activity yet" in ctx
            has_notes_hint = any(token in ctx for token in ("note", "notes"))
            if no_context or not has_notes_hint:
                return self._block(
                    path_id=path_id,
                    phase="preflight",
                    reasons=["Required notes context is missing for document generation."],
                    pushback_message=(
                        "I need meeting notes context before generating this document. "
                        "Please save or provide notes first."
                    ),
                    retry_instructions=[
                        "Call save_notes with the latest customer notes.",
                        "Call get_summary and retry this generation.",
                    ],
                )

        if path_id == "waf" and "diagram" not in ctx:
            return self._block(
                path_id=path_id,
                phase="preflight",
                reasons=["WAF review requires an existing diagram context."],
                pushback_message=(
                    "I need a generated architecture diagram before I can run WAF review."
                ),
                retry_instructions=[
                    "Run generate_diagram first.",
                    "Retry generate_waf after diagram creation.",
                ],
            )

        if path_id == "terraform":
            tf_prompt = str(args.get("prompt", "")).strip()
            if not tf_prompt and len(msg) < 15:
                return self._block(
                    path_id=path_id,
                    phase="preflight",
                    reasons=["Terraform goals and constraints are underspecified."],
                    pushback_message=(
                        "I need Terraform scope details before generation (modules, network, security, environments)."
                    ),
                    retry_instructions=[
                        "Provide target OCI services and module boundaries.",
                        "Include environment and security constraints.",
                    ],
                )

        if path_id == "summary_document":
            if current_state.get("tool") == "get_document":
                doc_type = str(args.get("type", "")).strip().lower()
                if doc_type not in {"pov", "jep", "waf"}:
                    return self._block(
                        path_id=path_id,
                        phase="preflight",
                        reasons=["Unsupported document type requested."],
                        pushback_message=(
                            "I can only retrieve document types: pov, jep, or waf."
                        ),
                        retry_instructions=["Retry get_document with type set to pov, jep, or waf."],
                    )

        return self._allow(path_id, "preflight")

    def postflight_check(
        self,
        path_id: str,
        tool_result: str,
        artifacts: dict[str, Any],
        context_summary: str,
    ) -> OrchestratorSkillDecision:
        try:
            self._ensure_loaded()
        except OrchestratorSkillPackError as exc:
            logger.error("Orchestrator skill pack fail-closed during postflight: %s", exc)
            return self._block(
                path_id=path_id,
                phase="postflight",
                reasons=[str(exc)],
                pushback_message=(
                    "I cannot validate this result safely because the orchestrator skill pack "
                    "is unavailable or malformed."
                ),
                retry_instructions=[
                    "Restore all required SKILL.md files under agent/orchestrator_skills/.",
                    "Retry once skill pack validation passes.",
                ],
            )

        summary = (tool_result or "").lower()
        fail_markers = (
            "failed",
            "error",
            "unknown tool",
            "not yet enabled",
            "cannot",
        )
        if any(marker in summary for marker in fail_markers):
            return self._block(
                path_id=path_id,
                phase="postflight",
                reasons=["Tool result indicates failure or non-completion."],
                pushback_message="The specialist result did not meet completion requirements.",
                retry_instructions=["Address the reported failure and retry this path."],
            )

        artifact_key = str(artifacts.get("artifact_key", "") or "")

        if path_id in {"pov", "jep", "waf"}:
            if "saved" not in summary or not artifact_key:
                return self._block(
                    path_id=path_id,
                    phase="postflight",
                    reasons=["Expected persisted document artifact is missing."],
                    pushback_message=(
                        "I could not verify a persisted document artifact for this result."
                    ),
                    retry_instructions=[
                        "Retry the generation and ensure the result includes a saved key.",
                    ],
                )

        if path_id == "diagram":
            has_success_signal = bool(artifact_key) or "started" in summary or "poll" in summary
            if not has_success_signal:
                return self._block(
                    path_id=path_id,
                    phase="postflight",
                    reasons=["Diagram output contract not satisfied (no artifact or accepted async status)."],
                    pushback_message="Diagram generation did not return a verifiable result.",
                    retry_instructions=[
                        "Retry generate_diagram with BOM input.",
                        "If asynchronous, poll task status and provide the completion result.",
                    ],
                )

        if path_id == "summary_document" and "no " in summary and "found" in summary:
            return self._block(
                path_id=path_id,
                phase="postflight",
                reasons=["Requested document does not exist yet."],
                pushback_message="That document is not available yet for this customer.",
                retry_instructions=["Generate the document first, then request get_document again."],
            )

        _ = context_summary  # reserved for richer semantic checks
        return self._allow(path_id, "postflight")


def decision_to_dict(decision: OrchestratorSkillDecision) -> dict[str, Any]:
    return asdict(decision)
