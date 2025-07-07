"""
Memory cleanup system for Claude Code Memory.

This module provides intelligent cleanup of manual memory entries while preserving
auto-indexed code. Uses dynamic field-based detection for future-proof categorization.
"""

from .detection import is_manual_entry
from .pipeline import MemoryCleanupPipeline
from .clustering import SimilarityClusterer
from .scorer import QualityScorer
from .resolver import ConflictResolver
from .executor import CleanupExecutor
from .safety import SafetyManager

__all__ = [
    'is_manual_entry',
    'MemoryCleanupPipeline', 
    'SimilarityClusterer',
    'QualityScorer',
    'ConflictResolver',
    'CleanupExecutor',
    'SafetyManager'
]