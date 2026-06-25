from agent import archie_memory


def test_diagram_revision_without_prior_diagram_needs_source_even_if_context_mentions_waf():
    context = {
        "archie": {
            "facts_summary": "platform=VMware; security=waf=True",
            "client_facts": {"platform": "VMware", "security": {"waf": True}},
        }
    }

    assert (
        archie_memory._diagram_has_sufficient_context(
            context=context,
            args={"prompt": "I need the diagram updated to include the correct OCI Icons"},
            user_message="I need the diagram updated to include the correct OCI Icons",
        )
        is False
    )


def test_diagram_revision_with_explicit_components_can_proceed_without_prior_diagram():
    assert (
        archie_memory._diagram_has_sufficient_context(
            context={},
            args={"prompt": "Update the diagram to use correct OCI icons for WAF and load balancer."},
            user_message="Update the diagram to use correct OCI icons for WAF and load balancer.",
        )
        is True
    )


def test_diagram_revision_with_existing_diagram_can_proceed():
    context = {"agents": {"diagram": {"diagram_key": "agent3/acme/app/v1/diagram.drawio"}}}

    assert (
        archie_memory._diagram_has_sufficient_context(
            context=context,
            args={"prompt": "Update the diagram to use correct OCI icons."},
            user_message="Update the diagram to use correct OCI icons.",
        )
        is True
    )
