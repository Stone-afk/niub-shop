"""Agent-owned demo user profile provider."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AgentProfileData:
    user_id: int
    source: str
    available: bool
    profile: dict[str, Any]


class AgentProfileProvider:
    """Provide the local profile that the Agent service owns for the demo user."""

    def get_profile(self, user_id: int) -> AgentProfileData:
        return AgentProfileData(
            user_id=user_id,
            source="agent",
            available=True,
            profile={
                "preferred_categories": ["drinkware"],
                "preferred_tags": ["coffee", "tea"],
                "price_range": {"min_cents": 20000, "max_cents": 80000},
                "style_preferences": ["simple", "practical"],
            },
        )
