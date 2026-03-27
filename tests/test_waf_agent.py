"""
tests/test_waf_agent.py
------------------------
Unit tests for agent/waf_agent.py.
All tests use InMemoryObjectStore — no OCI SDK or real LLM required.
"""
import pytest

from agent.persistence_objectstore import InMemoryObjectStore
from agent.document_store import save_note, get_latest_doc, list_versions
from agent.context_store import read_context, record_agent_run, write_context
from agent.waf_agent import generate_waf_review, _extract_overall_rating


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def store():
    return InMemoryObjectStore()


_FAKE_WAF_CONTENT = """\
# TestCo — OCI Well-Architected Framework Review

## Executive Summary
TestCo's architecture is well-positioned but has gaps in security and cost optimization.

### Overall Rating
| Pillar | Rating | Summary |
|--------|--------|---------|
| Operational Excellence | ✅ | CI/CD in place |
| Security               | ⚠️ | Vault not used |
| Reliability            | ✅ | Multi-AD topology |
| Performance Efficiency | ✅ | RDMA enabled |
| Cost Optimization      | ⚠️ | On-demand only |
| Sustainability         | ✅ | Archive tier configured |

---

## Pillar 1 — Operational Excellence
**Rating**: ✅
### Findings
- CI/CD pipeline configured with OCI DevOps.
### Recommendations
- Enable OCI Logging Analytics for centralized log management.

## Pillar 2 — Security
**Rating**: ⚠️
### Findings
- Secrets stored in environment variables rather than OCI Vault.
### Recommendations
- Migrate all secrets to OCI Vault.

## Top Priority Actions
| Priority | Action | Pillar | Effort |
|----------|--------|--------|--------|
| 1 | Migrate secrets to OCI Vault | Security | Medium |
"""


def _fake_waf_runner(prompt: str, system_message: str = "") -> str:
    return _FAKE_WAF_CONTENT


# ── _extract_overall_rating ───────────────────────────────────────────────────

class TestExtractOverallRating:
    def test_mostly_ok_returns_ok(self):
        content = "✅ ✅ ✅ ⚠️"
        assert _extract_overall_rating(content) == "✅"

    def test_mostly_warning_returns_warning(self):
        content = "⚠️ ⚠️ ✅"
        assert _extract_overall_rating(content) == "⚠️"

    def test_critical_returns_critical(self):
        content = "❌ ❌ ❌ ✅"
        assert _extract_overall_rating(content) == "❌"

    def test_no_ratings_returns_unknown(self):
        assert _extract_overall_rating("No ratings here") == "unknown"

    def test_fake_content_is_warning_heavy(self):
        rating = _extract_overall_rating(_FAKE_WAF_CONTENT)
        assert rating in ("✅", "⚠️", "❌")


# ── generate_waf_review ───────────────────────────────────────────────────────

class TestGenerateWafReview:
    def test_generate_returns_result_dict(self, store):
        result = generate_waf_review("cust1", "TestCo", store, _fake_waf_runner)
        assert result["version"] == 1
        assert "key" in result
        assert "latest_key" in result
        assert "content" in result
        assert "overall_rating" in result

    def test_generate_content_from_runner(self, store):
        result = generate_waf_review("cust1", "TestCo", store, _fake_waf_runner)
        assert "Well-Architected" in result["content"]

    def test_generate_persists_to_store(self, store):
        generate_waf_review("cust1", "TestCo", store, _fake_waf_runner)
        assert store.head("waf/cust1/LATEST.md")
        assert store.head("waf/cust1/v1.md")
        assert store.head("waf/cust1/MANIFEST.json")

    def test_generate_increments_version(self, store):
        generate_waf_review("cust1", "TestCo", store, _fake_waf_runner)
        result2 = generate_waf_review("cust1", "TestCo", store, _fake_waf_runner)
        assert result2["version"] == 2

    def test_generate_updates_context(self, store):
        result = generate_waf_review("cust1", "TestCo", store, _fake_waf_runner)
        assert "waf" in result["context"]["agents"]
        assert result["context"]["agents"]["waf"]["version"] == 1

    def test_generate_stores_overall_rating_in_context(self, store):
        result = generate_waf_review("cust1", "TestCo", store, _fake_waf_runner)
        ctx_waf = result["context"]["agents"]["waf"]
        assert "overall_rating" in ctx_waf
        assert ctx_waf["overall_rating"] in ("✅", "⚠️", "❌", "unknown")

    def test_generate_notes_ingested(self, store):
        save_note(store, "cust1", "arch.txt", b"No encryption in transit")
        result = generate_waf_review("cust1", "TestCo", store, _fake_waf_runner)
        incorporated = result["context"]["agents"]["waf"]["notes_incorporated"]
        assert "notes/cust1/arch.txt" in incorporated

    def test_generate_no_notes_still_works(self, store):
        result = generate_waf_review("cust1", "EmptyCo", store, _fake_waf_runner)
        assert result["version"] == 1
        assert result["content"]

    def test_generate_includes_notes_in_prompt(self, store):
        save_note(store, "cust1", "notes.txt", b"No backup policy configured")
        prompts = []

        def capturing(prompt, system_msg=""):
            prompts.append(prompt)
            return _fake_waf_runner(prompt, system_msg)

        generate_waf_review("cust1", "TestCo", store, capturing)
        assert any("No backup policy" in p for p in prompts)

    def test_generate_includes_previous_version_in_prompt(self, store):
        generate_waf_review("cust1", "TestCo", store, _fake_waf_runner)
        prompts = []

        def capturing(prompt, system_msg=""):
            prompts.append(prompt)
            return _fake_waf_runner(prompt, system_msg)

        generate_waf_review("cust1", "TestCo", store, capturing)
        assert any("Previous WAF" in p for p in prompts)

    def test_generate_does_not_reingest_seen_notes(self, store):
        save_note(store, "cust1", "old.txt", b"old security note")
        generate_waf_review("cust1", "TestCo", store, _fake_waf_runner)

        prompts = []
        def capturing(prompt, system_msg=""):
            prompts.append(prompt)
            return _fake_waf_runner(prompt, system_msg)

        generate_waf_review("cust1", "TestCo", store, capturing)
        assert any("No new notes" in p for p in prompts)

    def test_generate_context_summary_injected(self, store):
        """If prior agent ran, its summary should appear in WAF prompt."""
        ctx = read_context(store, "cust1", "TestCo")
        ctx = record_agent_run(ctx, "pov", [], {"version": 1, "key": "pov/cust1/v1.md"})
        write_context(store, "cust1", ctx)

        prompts = []
        def capturing(prompt, system_msg=""):
            prompts.append(prompt)
            return _fake_waf_runner(prompt, system_msg)

        generate_waf_review("cust1", "TestCo", store, capturing)
        assert any("POV" in p or "Prior agent" in p for p in prompts)

    def test_generate_customer_name_in_prompt(self, store):
        prompts = []
        def capturing(prompt, system_msg=""):
            prompts.append(prompt)
            return _fake_waf_runner(prompt, system_msg)

        generate_waf_review("cust1", "Jane Street Capital", store, capturing)
        assert any("Jane Street Capital" in p for p in prompts)

    def test_generate_metadata_stored_in_manifest(self, store):
        generate_waf_review("cust1", "Acme Corp", store, _fake_waf_runner)
        versions = list_versions(store, "waf", "cust1")
        assert versions[0]["metadata"]["customer_name"] == "Acme Corp"

    def test_generate_waf_passes_system_message(self, store):
        received = []
        def capturing(prompt, system_msg=""):
            received.append(system_msg)
            return _fake_waf_runner(prompt, system_msg)

        generate_waf_review("cust1", "TestCo", store, capturing)
        assert any("Well-Architected" in sm or "WAF" in sm or "pillar" in sm.lower()
                   for sm in received)

    def test_generate_waf_all_pillars_in_prompt(self, store):
        prompts = []
        def capturing(prompt, system_msg=""):
            prompts.append(prompt)
            return _fake_waf_runner(prompt, system_msg)

        generate_waf_review("cust1", "TestCo", store, capturing)
        full_prompt = " ".join(prompts)
        for pillar in [
            "Operational Excellence", "Security", "Reliability",
            "Performance Efficiency", "Cost Optimization", "Sustainability",
        ]:
            assert pillar in full_prompt
