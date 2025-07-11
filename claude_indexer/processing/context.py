"""Processing context data structures."""

from dataclasses import dataclass
from typing import Set


@dataclass
class ProcessingContext:
    """Context information for content processing operations."""
    
    collection_name: str
    changed_entity_ids: Set[str]
    implementation_entity_names: Set[str]
    total_tokens: int = 0
    total_cost: float = 0.0
    total_requests: int = 0
    
    def add_metrics(self, tokens: int = 0, cost: float = 0.0, requests: int = 0):
        """Add metrics to the context."""
        self.total_tokens += tokens
        self.total_cost += cost
        self.total_requests += requests