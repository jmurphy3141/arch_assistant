from __future__ import annotations

import pytest

from agent import hat_engine


@pytest.fixture(autouse=True)
def known_hats(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        hat_engine,
        "_HAT_CACHE",
        {
            "bom_reviewer": "BOM reviewer instructions",
            "critic": "Critic instructions",
            "diagram_builder": "Diagram builder instructions",
            "governor": "Governor instructions",
        },
    )


def test_apply_hat_idempotent_add_no_duplicate_or_eviction() -> None:
    active_hats = ["bom_reviewer", "critic", "diagram_builder"]

    result = hat_engine.apply_hat(active_hats, "critic")

    assert result == active_hats
    assert result is active_hats


def test_apply_hat_normal_add_under_limit() -> None:
    assert hat_engine.apply_hat(["bom_reviewer"], "critic") == [
        "bom_reviewer",
        "critic",
    ]


def test_apply_hat_eviction_at_limit_removes_oldest() -> None:
    result = hat_engine.apply_hat(
        ["bom_reviewer", "critic", "diagram_builder"],
        "governor",
    )

    assert result == ["critic", "diagram_builder", "governor"]


def test_apply_hat_unknown_hat_raises_value_error() -> None:
    with pytest.raises(ValueError):
        hat_engine.apply_hat(["critic"], "unknown")


def test_drop_hat_removes_known_hat() -> None:
    result = hat_engine.drop_hat(
        ["bom_reviewer", "critic", "diagram_builder"],
        "critic",
    )

    assert result == ["bom_reviewer", "diagram_builder"]


def test_drop_hat_no_op_for_absent_hat() -> None:
    active_hats = ["bom_reviewer", "critic"]

    result = hat_engine.drop_hat(active_hats, "governor")

    assert result == active_hats
    assert result is not active_hats


def test_warn_stale_hats_returns_stale_names() -> None:
    result = hat_engine.warn_stale_hats(
        ["bom_reviewer", "critic", "diagram_builder"],
        {"bom_reviewer": 6, "critic": 5, "diagram_builder": 9},
    )

    assert result == ["bom_reviewer", "diagram_builder"]


def test_warn_stale_hats_returns_empty_list_when_none_stale() -> None:
    result = hat_engine.warn_stale_hats(
        ["bom_reviewer", "critic"],
        {"bom_reviewer": 5, "critic": 1},
    )

    assert result == []
