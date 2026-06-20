"""
ClauseOps — Configuration for Obligation Detection & Task Generation

v4.1: Production-ready configuration system for filtering, thresholds, and options.
"""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class TaskGenerationConfig:
    """Configuration for task generation behavior."""
    
    # PERMISSION filtering (v4.1 — addresses 56% noise issue)
    exclude_permissions: bool = True
    """
    If True (default), PERMISSION-type obligations are filtered out.
    Permissions are rights (may/can), not actionable obligations.
    
    Impact: Reduces task count by ~55% (only true obligations remain).
    """
    
    # Confidence threshold
    min_confidence: float = 0.55
    """
    Minimum MODALITY classifier confidence to accept an obligation as a task.
    Range: 0.0-1.0. Default: 0.55 (balanced precision/recall).
    ENFORCED as of M2 (Phase E): obligations below this are dropped — this is
    the primary precision control that stops borderline/misclassified clauses
    from becoming tasks. Lower = more recall, higher = more precision.
    """

    # Agent (party) grounding-score threshold
    min_agent_score: float = 4.0
    """
    Minimum QA agent-span score to trust an extracted party (M2, Phase E).
    Passive/agent-less clauses produce low-score spans (e.g. "be submitted to
    mediation", score ~3.2) that are not real parties. Obligations whose agent
    scores below this AND whose modality confidence is not high are dropped.
    Set to 0.0 to disable. The QA agent score is an unbounded logit (~3-19 in
    practice); 4.0 trims only clear noise.
    """

    review_low_quality: bool = True
    """
    If True (M2, Phase E), obligations that are uncertain but not clearly wrong
    (action abstained, or borderline agent score) are KEPT but flagged
    requires_review=True instead of being silently dropped — never fabricate,
    never silently lose a possible obligation.
    """
    
    # Priority filtering
    exclude_low_priority: bool = False
    """
    If True, LOW priority tasks are excluded.
    Useful for executives who only want CRITICAL/HIGH/MEDIUM alerts.
    """
    
    # Task limits
    max_tasks_per_clause: int = 6
    """
    Safety cap on tasks per clause to prevent pathological explosions.
    As of M2 task generation is obligation-centric (1 task per distinct
    obligation), so this is a safety valve, not the primary limiter — raised
    from 3 to 6 so legitimate multi-obligation sections (e.g. a reporting
    clause with report + dispute-notice + negotiate duties) aren't clipped.
    """
    
    max_total_tasks: int | None = None
    """
    Optional hard cap on total tasks per contract.
    If exceeded, only highest-priority tasks are kept.
    """
    
    # Debugging
    verbose_logging: bool = False
    """Enable detailed logging for debugging."""
    
    include_source_text: bool = True
    """Include clause source text in task descriptions (for audit trail)."""


# ─── Global default config ───────────────────────────────────────────────────
DEFAULT_CONFIG = TaskGenerationConfig()


def get_default_config() -> TaskGenerationConfig:
    """Get the default configuration."""
    return DEFAULT_CONFIG


def create_permissive_config() -> TaskGenerationConfig:
    """
    Create a permissive config (includes permissions, low confidence, etc.).
    Useful for legal review where recall > precision.
    """
    return TaskGenerationConfig(
        exclude_permissions=False,
        min_confidence=0.45,
        min_agent_score=0.0,
        review_low_quality=True,
        exclude_low_priority=False,
        max_tasks_per_clause=5,
    )


def create_strict_config() -> TaskGenerationConfig:
    """
    Create a strict config (high confidence, no permissions, no low priority).
    Useful for executive dashboards where precision > recall.
    """
    return TaskGenerationConfig(
        exclude_permissions=True,
        min_confidence=0.70,
        min_agent_score=6.0,
        review_low_quality=False,
        exclude_low_priority=True,
        max_tasks_per_clause=2,
    )
