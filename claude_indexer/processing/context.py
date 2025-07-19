"""Processing context data structures."""

from dataclasses import dataclass


@dataclass
class ProcessingContext:
    """Context information for content processing operations."""

    collection_name: str
    changed_entity_ids: set[str]
    implementation_entity_names: set[str]
    total_tokens: int = 0
    total_cost: float = 0.0
    total_requests: int = 0

    def add_metrics(
        self, tokens: int = 0, cost: float = 0.0, requests: int = 0
    ) -> None:
        """Add metrics to the context."""
        self.total_tokens += tokens
        self.total_cost += cost
        self.total_requests += requests
