from agent.archie_wiring import _EXPERT_IDENTITY


def test_expert_identity_contains_industry_inference_section_before_intelligence():
    inference_index = _EXPERT_IDENTITY.index("INDUSTRY INFERENCE:")
    intelligence_index = _EXPERT_IDENTITY.index("INDUSTRY INTELLIGENCE:")

    assert inference_index < intelligence_index


def test_industry_inference_signal_keywords_are_present():
    required_keywords = [
        "bank",
        "HSBC",
        "hospital",
        "HIPAA",
        "federal",
        "FedRAMP",
        "retail",
        "manufacturing",
        "migrating from AWS",
        "TCO comparison",
    ]

    for keyword in required_keywords:
        assert keyword in _EXPERT_IDENTITY


def test_industry_inference_correction_instruction_is_present():
    assert "Correct me if wrong" in _EXPERT_IDENTITY
    assert 'Do not ask "what industry are they in?"' in _EXPERT_IDENTITY
