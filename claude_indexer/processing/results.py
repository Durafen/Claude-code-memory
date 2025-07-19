"""Processing result data structures."""

from dataclasses import dataclass


@dataclass
class ProcessingResult:
    """Result of content processing operations."""

    success: bool
    items_processed: int = 0
    embeddings_saved: int = 0
    embeddings_skipped: int = 0
    total_tokens: int = 0
    total_cost: float = 0.0
    total_requests: int = 0
    points_created: list | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if self.points_created is None:
            self.points_created = []

    @classmethod
    def success_result(
        cls,
        items_processed: int = 0,
        embeddings_saved: int = 0,
        embeddings_skipped: int = 0,
        total_tokens: int = 0,
        total_cost: float = 0.0,
        total_requests: int = 0,
        points_created: list | None = None,
    ) -> "ProcessingResult":
        """Create a successful processing result."""
        return cls(
            success=True,
            items_processed=items_processed,
            embeddings_saved=embeddings_saved,
            embeddings_skipped=embeddings_skipped,
            total_tokens=total_tokens,
            total_cost=total_cost,
            total_requests=total_requests,
            points_created=points_created or [],
        )

    @classmethod
    def failure_result(cls, error: str) -> "ProcessingResult":
        """Create a failed processing result."""
        return cls(success=False, error=error)

    def combine_with(self, other: "ProcessingResult") -> "ProcessingResult":
        """Combine this result with another result."""
        if not other.success:
            return other  # Propagate failure

        combined_points = (self.points_created or []) + (other.points_created or [])

        return ProcessingResult(
            success=self.success and other.success,
            items_processed=self.items_processed + other.items_processed,
            embeddings_saved=self.embeddings_saved + other.embeddings_saved,
            embeddings_skipped=self.embeddings_skipped + other.embeddings_skipped,
            total_tokens=self.total_tokens + other.total_tokens,
            total_cost=self.total_cost + other.total_cost,
            total_requests=self.total_requests + other.total_requests,
            points_created=combined_points,
            error=self.error or other.error,
        )
