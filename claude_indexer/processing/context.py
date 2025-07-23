"""Processing context data structures."""

from dataclasses import dataclass


@dataclass  
class ProcessingContext:
    """Context information for content processing operations."""

    collection_name: str
    changed_entity_ids: set[str]
    implementation_entity_names: set[str]
    files_being_processed: set[str] = None  # NEW: Track files for replacement logic
    entities_to_delete: list[str] = None    # NEW: Track entity IDs to delete before upsert
    replacement_mode: bool = True           # NEW: Enable file-level replacement
    replaced_entity_ids: set[str] = None    # NEW: Track entities that were just replaced (skip dedup)
    total_tokens: int = 0
    total_cost: float = 0.0
    total_requests: int = 0
    
    def __post_init__(self):
        """Initialize mutable default values."""
        if self.files_being_processed is None:
            self.files_being_processed = set()
        if self.entities_to_delete is None:
            self.entities_to_delete = []
        if self.replaced_entity_ids is None:
            self.replaced_entity_ids = set()

    def add_metrics(
        self, tokens: int = 0, cost: float = 0.0, requests: int = 0
    ) -> None:
        """Add metrics to the context."""
        self.total_tokens += tokens
        self.total_cost += cost
        self.total_requests += requests
