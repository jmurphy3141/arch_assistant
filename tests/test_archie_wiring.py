import importlib
import sys

from agent.archie_wiring import ArchiePromptEnricher, build_forge
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
