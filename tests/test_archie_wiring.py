import importlib
import sys

from agent.archie_wiring import (
    NATIVE_SYSTEM_IDENTITY,
    ArchiePromptEnricher,
    build_forge,
    get_registered_tool_specs,
)
from skillforge import Forge
from skillforge.types import MemorySnapshot


async def dummy_runner(prompt, system_msg, label):
    return "Done."


def make_forge():
    return build_forge(
        store=None,
        customer_id="c1",
        customer_name="Acme",
        text_runner=dummy_runner,
    )


def test_build_forge_returns_forge():
    forge = make_forge()

    assert isinstance(forge, Forge)


def test_all_tools_registered():
    forge = make_forge()
    names = [
        "save_notes",
        "get_summary",
        "get_document",
        "generate_bom",
        "generate_diagram",
        "generate_terraform",
        "generate_pov",
        "generate_jep",
        "generate_waf",
    ]

    for name in names:
        assert forge._registry.get(name) is not None


def test_memory_contract_tools():
    forge = make_forge()
    names = [
        "generate_bom",
        "generate_diagram",
        "generate_terraform",
        "generate_pov",
        "generate_jep",
        "generate_waf",
    ]

    for name in names:
        assert forge._registry.requires_memory(name)


def test_native_identity_makes_explicit_request_the_only_generation_authority():
    identity = NATIVE_SYSTEM_IDENTITY.lower()

    assert identity.count("call a generate_* tool only") == 1
    assert "sole authorization" in identity
    assert "c3e guides the conversation, never generation" in identity
    assert "only to decide whether to offer the next deliverable" in identity
    assert "phase or artifact gate is never a request to produce" in identity


def test_native_generation_descriptions_have_non_overlapping_triggers():
    forge = make_forge()
    native = {spec.name: spec.description for spec in get_registered_tool_specs(forge)}

    assert "written research report" in native["generate_tech_report"]
    assert "how to architect a workload" not in native["generate_tech_report"]
    assert "not for conversational architecture questions" in native["generate_tech_report"].lower()
    assert "poc presentation" in native["generate_presentation"].lower()
    assert "sales deck" in native["generate_sales_deck"].lower()
    assert "point of view document" in native["generate_pov"].lower()
    assert "technical proposal" in native["generate_technical_proposal"].lower()


def test_native_description_overrides_do_not_mutate_forge_registry():
    forge = make_forge()
    forge_description = forge._registry.get("generate_tech_report").description

    get_registered_tool_specs(forge)

    assert forge._registry.get("generate_tech_report").description == forge_description


def test_native_shared_retrieval_descriptions_have_distinct_boundaries():
    descriptions = {
        spec.name: spec.description.lower()
        for spec in get_registered_tool_specs(make_forge())
    }

    assert "gathered facts" in descriptions["get_summary"]
    assert "not for produced artifacts" in descriptions["get_summary"]
    assert "one produced deliverable" in descriptions["get_document"]
    assert "not for listing every deliverable" in descriptions["get_document"]


def test_prompt_enricher_injects_facts():
    enricher = ArchiePromptEnricher()
    snapshot = MemorySnapshot(
        session_id="s1", facts={"facts_summary": "3-tier app"}
    )

    result = enricher("USER: hello", snapshot)

    assert "[Archie Facts]" in result
    assert "3-tier app" in result


def test_prompt_enricher_empty_memory():
    enricher = ArchiePromptEnricher()
    snapshot = MemorySnapshot(session_id="s1")

    result = enricher("USER: hello", snapshot)

    assert result == "USER: hello"


def test_no_archie_session_import():
    for mod in list(sys.modules):
        if "archie_session" in mod:
            del sys.modules[mod]

    import agent.archie_wiring

    importlib.reload(agent.archie_wiring)
    assert "agent.archie_session" not in sys.modules, (
        "archie_wiring imported archie_session at module level - move to lazy "
        "import inside __call__"
    )
