from agent_service.profile_provider import AgentProfileProvider


def test_agent_profile_provider_returns_demo_profile() -> None:
    profile = AgentProfileProvider().get_profile(1)

    assert profile.source == "agent"
    assert profile.available is True
    assert profile.profile["preferred_categories"] == ["drinkware"]
    assert profile.profile["price_range"] == {
        "min_cents": 20000,
        "max_cents": 80000,
    }
