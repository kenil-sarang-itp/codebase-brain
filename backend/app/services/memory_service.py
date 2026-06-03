"""
Memory service — persistent conversational memory.

Implements the spec's persistent-memory feature in two tiers:

    * Short-term: the recent message history of the current chat session,
      injected into prompts so follow-up questions have context.
    * Long-term: a `DeveloperProfile` that accumulates across sessions — which
      modules a developer asks about, their preferred answer depth — so answers
      become personalised over time.

The service exposes load/save operations; the `ChatService` calls them around
each agent run. Profile *learning* is intentionally simple and transparent
(frequency counts), not a black box.
"""

from __future__ import annotations

from collections import Counter

from app.core.logging import get_logger
from app.db.repositories.memory_repository import MemoryRepository

logger = get_logger(__name__)

# How many recent messages to load as short-term context.
_HISTORY_WINDOW = 10


class MemoryService:
    """Loads and updates short-term history and long-term developer profiles."""

    def __init__(self, memory_repo: MemoryRepository) -> None:
        """Inject the memory repository."""
        self._memory = memory_repo

    # ---------------------------------------------------------- load context
    async def load_context(
        self, *, session_id: str, developer_id: str
    ) -> tuple[str, str]:
        """Load prompt-ready short-term history and long-term profile text.

        Returns:
            A tuple `(history_text, profile_text)`. Either may be empty for a
            brand-new session or developer.
        """
        history = await self._memory.get_recent_history(
            session_id, limit=_HISTORY_WINDOW
        )
        history_text = "\n".join(
            f"{msg.role}: {msg.message}" for msg in history
        )

        profile = await self._memory.get_profile(developer_id)
        if profile is None:
            profile_text = ""
        else:
            modules = ", ".join(profile.common_modules or []) or "none yet"
            profile_text = (
                f"Frequently explores: {modules}. "
                f"Question style: {profile.question_style or 'unknown'}. "
                f"Preferred depth: {profile.preferred_depth or 'unspecified'}. "
                f"Total interactions: {profile.interaction_count}."
            )

        return history_text, profile_text

    # ----------------------------------------------------------- record turn
    async def record_exchange(
        self,
        *,
        session_id: str,
        developer_id: str,
        question: str,
        answer: str,
        referenced_files: list[str] | None = None,
    ) -> None:
        """Persist one Q&A exchange to short-term memory and update the profile.

        Args:
            session_id: The chat session id.
            developer_id: The developer's stable id.
            question: The developer's question.
            answer: The assistant's answer.
            referenced_files: Files cited in the answer — used to learn which
                modules the developer commonly explores.
        """
        # 1. Short-term: append both messages.
        await self._memory.add_message(
            session_id=session_id,
            developer_id=developer_id,
            role="user",
            message=question,
        )
        await self._memory.add_message(
            session_id=session_id,
            developer_id=developer_id,
            role="assistant",
            message=answer,
        )

        # 2. Long-term: update the developer profile.
        await self._update_profile(developer_id, question, referenced_files or [])

    async def _update_profile(
        self,
        developer_id: str,
        question: str,
        referenced_files: list[str],
    ) -> None:
        """Refine the developer's long-term profile from this interaction.

        The learning rule is deliberately simple and explainable: track the
        most-referenced modules and infer a rough question style. Transparency
        beats cleverness for a feature that shapes future answers.
        """
        existing = await self._memory.get_profile(developer_id)
        prior_modules: list[str] = list(existing.common_modules) if existing else []

        # Merge prior modules with this turn's referenced files, keep the top 8.
        module_counts = Counter(prior_modules)
        module_counts.update(referenced_files)
        common_modules = [m for m, _ in module_counts.most_common(8)]

        # Infer a coarse question style from simple cues.
        lowered = question.lower()
        if any(w in lowered for w in ("why", "how", "explain")):
            style = "conceptual"
        elif any(w in lowered for w in ("where", "which file", "find")):
            style = "navigational"
        else:
            style = "factual"

        await self._memory.upsert_profile(
            developer_id=developer_id,
            common_modules=common_modules,
            question_style=style,
        )
        logger.debug("Updated developer profile for %s", developer_id)
