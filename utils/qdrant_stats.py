#!/usr/bin/env python3
"""
Qdrant Vector Database Statistics Script

Shows comprehensive statistics about Qdrant collections including:
- Total entries and files
- File type distribution
- Manual vs automated entries
- Health status
- Performance metrics
"""

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

# Add claude_indexer to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from claude_indexer.config import IndexerConfig
from claude_indexer.indexer_logging import get_logger
from claude_indexer.storage.registry import create_store_from_config


class QdrantStatsCollector:
    """Collects comprehensive statistics from Qdrant vector database."""

    def __init__(self, config: IndexerConfig):
        self.config = config
        self.storage = create_store_from_config(config.model_dump())
        self.logger = get_logger()

    def get_all_collections(self) -> list[str]:
        """Get list of all collections."""
        try:
            return self.storage.list_collections()
        except Exception as e:
            print(f"Error getting collections: {e}")
            return []

    def get_collection_stats(self, collection_name: str) -> dict[str, Any]:
        """Get detailed statistics for a collection."""
        stats = self.storage.get_collection_info(collection_name)

        if "error" in stats:
            return stats

        # Add file type analysis
        file_types = self._analyze_file_types(collection_name)
        manual_entries = self._count_manual_entries(collection_name)
        tracked_files = self._get_tracked_files_count(collection_name)

        # Get direct API health status
        direct_health = self._get_health_status_from_api(collection_name)

        # Get collection config for threshold info
        raw_config = None
        try:
            collection_info = self.storage.client.get_collection(collection_name)
            if hasattr(collection_info, "config"):
                raw_config = collection_info.config
        except (ConnectionError, TimeoutError) as e:
            self.logger.error(
                f"Failed to get collection config for {collection_name}: {e}"
            )
        except Exception as e:
            self.logger.error(
                f"Unexpected error getting collection config for {collection_name}: {e}"
            )

        stats.update(
            {
                "file_types": file_types,
                "manual_entries_count": manual_entries,
                "automated_entries_count": stats.get("points_count", 0)
                - manual_entries,
                "tracked_files_count": tracked_files,
                "health_status": self._get_health_status(stats),
                "health_details": self._get_detailed_health_info(
                    collection_name, stats, raw_config
                ),
                "direct_api_health": direct_health,
            }
        )

        return stats

    def _analyze_file_types(self, collection_name: str) -> dict[str, int]:
        """Analyze file types in collection based on v2.4 chunk data."""
        try:
            # Use scroll to get ALL points with payloads (fixed pagination bug)
            all_points = []

            scroll_result = self.storage.client.scroll(
                collection_name=collection_name,
                limit=1000,
                with_payload=True,
                with_vectors=False,
            )

            points, next_page_offset = scroll_result
            all_points.extend(points)

            # Continue scrolling if there are more points
            while next_page_offset:
                scroll_result = self.storage.client.scroll(
                    collection_name=collection_name,
                    limit=1000,
                    offset=next_page_offset,
                    with_payload=True,
                    with_vectors=False,
                )
                points, next_page_offset = scroll_result
                all_points.extend(points)

            entity_types = Counter()
            chunk_types = Counter()
            file_extensions = Counter()
            auto_vs_manual = Counter()
            manual_entity_types = Counter()

            for point in all_points:
                if hasattr(point, "payload") and point.payload:
                    # Detect relations first - skip entity_type counting for relations (v2.4 format)
                    has_relation_structure = (
                        "entity_name" in point.payload
                        and "relation_target" in point.payload
                        and "relation_type" in point.payload
                    )

                    # Check both locations for entity_type (new: metadata.entity_type, fallback: top-level)
                    entity_type = point.payload.get("metadata", {}).get("entity_type") or point.payload.get("entity_type", "unknown")

                    # Only count entity_type for non-relation entries
                    if not has_relation_structure:
                        entity_types[entity_type] += 1

                    # Track v2.4 chunk types + ensure relations are counted
                    chunk_type = point.payload.get("chunk_type", "unknown")
                    if chunk_type != "unknown":
                        chunk_types[chunk_type] += 1
                    elif has_relation_structure:
                        # Count relations that don't have explicit chunk_type='relation'
                        chunk_types["relation"] += 1

                    # Detect if auto-generated vs manual using exact clear_collection logic
                    has_file_path = (
                        "file_path" in point.payload and point.payload["file_path"]
                    ) or (
                        "metadata" in point.payload 
                        and isinstance(point.payload["metadata"], dict)
                        and "file_path" in point.payload["metadata"] 
                        and point.payload["metadata"]["file_path"]
                    )

                    if has_file_path or has_relation_structure:
                        auto_vs_manual["auto_generated"] += 1
                    else:
                        auto_vs_manual["manual"] += 1
                        # Track manual entity types separately
                        manual_entity_types[entity_type] += 1

                    # Count file extensions for entities with file_path (check metadata field)
                    file_path = None
                    if "file_path" in point.payload and point.payload["file_path"]:
                        file_path = point.payload["file_path"]
                    elif "metadata" in point.payload and isinstance(point.payload["metadata"], dict):
                        metadata = point.payload["metadata"]
                        if "file_path" in metadata and metadata["file_path"]:
                            file_path = metadata["file_path"]

                    # Track file extensions (will be deduplicated later)

            # Count unique files and their extensions
            unique_files = set()
            for point in all_points:
                if not (hasattr(point, "payload") and point.payload):
                    continue

                file_path = None
                # Check top-level file_path
                if "file_path" in point.payload and point.payload["file_path"]:
                    file_path = point.payload["file_path"]
                # Check metadata.file_path
                elif "metadata" in point.payload and isinstance(point.payload["metadata"], dict):
                    metadata = point.payload["metadata"]
                    if "file_path" in metadata and metadata["file_path"]:
                        file_path = metadata["file_path"]

                if file_path:
                    unique_files.add(file_path)

            # Count extensions from unique files only
            file_extensions = Counter()
            for file_path in unique_files:
                ext = Path(file_path).suffix.lower()
                if ext:
                    file_extensions[ext] += 1
                else:
                    file_extensions["no_extension"] += 1

            total_files_count = len(unique_files)

            return {
                "total_files": total_files_count,
                "entity_breakdown": dict(entity_types),
                "chunk_type_breakdown": dict(chunk_types),
                "manual_entity_breakdown": dict(manual_entity_types),
                "file_extensions": dict(file_extensions),
                "auto_vs_manual": dict(auto_vs_manual),
                "total_analyzed": len(all_points),
            }

        except Exception as e:
            print(f"Error analyzing entity types for {collection_name}: {e}")
            return {
                "total_files": 0,
                "entity_breakdown": {},
                "chunk_type_breakdown": {},
                "manual_entity_breakdown": {},
                "file_extensions": {},
                "auto_vs_manual": {},
                "total_analyzed": 0,
            }

    def _count_manual_entries(self, collection_name: str) -> int:
        """Count manually added entries using comprehensive detection logic."""
        try:
            # Use scroll to get ALL points like backup script
            all_points = []

            scroll_result = self.storage.client.scroll(
                collection_name=collection_name,
                limit=1000,
                with_payload=True,
                with_vectors=False,
            )

            points, next_page_offset = scroll_result
            all_points.extend(points)

            # Continue scrolling if there are more points
            while next_page_offset:
                scroll_result = self.storage.client.scroll(
                    collection_name=collection_name,
                    limit=1000,
                    offset=next_page_offset,
                    with_payload=True,
                    with_vectors=False,
                )
                points, next_page_offset = scroll_result
                all_points.extend(points)

            manual_count = 0
            for point in all_points:
                if hasattr(point, "payload") and point.payload and self._is_truly_manual_entry(point.payload):
                    manual_count += 1

            return manual_count

        except Exception as e:
            print(f"Error counting manual entries for {collection_name}: {e}")
            return 0

    def _is_truly_manual_entry(self, payload: dict[str, Any]) -> bool:
        """Enhanced logic for v2.4 chunk format."""
        # Pattern 1: Auto entities have file_path field
        if "file_path" in payload or "file_path" in payload.get("metadata", {}):
            return False

        # Pattern 2: Auto relations have entity_name/relation_target/relation_type structure
        if all(
            field in payload
            for field in ["entity_name", "relation_target", "relation_type"]
        ):
            return False

        # Pattern 3: Auto entities have extended metadata fields (check both locations)
        automation_fields = {
            "line_number",
            "ast_data",
            "signature",
            "docstring",
            "full_name",
            "ast_type",
            "start_line",
            "end_line",
            "source_hash",
            "parsed_at",
            # Removed 'has_implementation' - manual entries can have this field in v2.4 format
            # Removed 'collection' - manual docs can have collection field
        }
        metadata = payload.get("metadata", {})
        if any(field in payload for field in automation_fields) or any(field in metadata for field in automation_fields):
            return False

        # v2.4 specific: Don't reject based on chunk_type alone
        # Both manual and auto entries can have chunk_type in v2.4
        # Manual entries from MCP also get type='chunk' + chunk_type='metadata'

        # True manual entries have minimal fields: entity_name, entity_type, observations
        # v2.4 format: check both top-level and nested metadata
        has_name = "entity_name" in payload
        has_type = "entity_type" in payload or "entity_type" in payload.get("metadata", {})

        if not (has_name and has_type):
            return False

        # Additional check: Manual entries typically have meaningful content
        # Check for observations or content (v2.4 MCP format with nested observations)
        observations = payload.get("metadata", {}).get("observations", [])
        content = payload.get("content", "")

        has_meaningful_content = (
            observations and isinstance(observations, list) and len(observations) > 0
        ) or (content and isinstance(content, str) and len(content.strip()) > 0)

        return has_meaningful_content

    def _get_health_status(self, stats: dict[str, Any]) -> str:
        """Determine comprehensive health status based on collection statistics."""
        if "error" in stats:
            return "UNHEALTHY"

        status = stats.get("status", "").upper()
        points_count = stats.get("points_count", 0)
        indexed_count = stats.get("indexed_vectors_count", 0)
        stats.get("segments_count", 0)

        # Handle all Qdrant status values
        if status == "GREEN":
            if points_count == 0:
                return "EMPTY"
            elif indexed_count >= points_count * 0.98:  # 98% indexed = healthy
                return "HEALTHY"
            elif indexed_count >= points_count * 0.90:  # 90-98% = indexing
                return "INDEXING"
            else:  # <90% indexed = performance issue
                return "DEGRADED"
        elif status == "YELLOW":
            # Optimizations in progress (background processing)
            return "OPTIMIZING"
        elif status == "GREY":
            # Optimizations paused/pending (after restart)
            return "OPTIMIZATION_PENDING"
        elif status == "RED":
            return "FAILED"
        else:
            return "UNKNOWN"

    def _get_detailed_health_info(
        self, collection_name: str, stats: dict[str, Any], raw_config=None
    ) -> dict[str, Any]:
        """Get detailed health analysis for troubleshooting."""
        health_info = {
            "basic_status": self._get_health_status(stats),
            "connection_ok": True,
            "optimization_progress": 0.0,
            "segment_health": "UNKNOWN",
            "performance_indicators": {},
            "raw_config": raw_config,
        }

        if "error" in stats:
            health_info["connection_ok"] = False
            health_info["error_details"] = stats["error"]
            return health_info

        try:
            points_count = stats.get("points_count", 0)
            indexed_count = stats.get("indexed_vectors_count", 0)
            segments_count = stats.get("segments_count", 0)

            # Calculate optimization progress
            if points_count > 0:
                health_info["optimization_progress"] = (
                    indexed_count / points_count
                ) * 100

            # Segment health analysis
            if segments_count > 0:
                if segments_count <= 10:
                    health_info["segment_health"] = "OPTIMAL"
                elif segments_count <= 50:
                    health_info["segment_health"] = "GOOD"
                elif segments_count <= 100:
                    health_info["segment_health"] = "ACCEPTABLE"
                else:
                    health_info["segment_health"] = "FRAGMENTED"

            # Performance indicators
            health_info["performance_indicators"] = {
                "indexing_ratio": round(indexed_count / max(points_count, 1), 3),
                "segments_per_1k_points": round(
                    (segments_count * 1000) / max(points_count, 1), 2
                ),
                "avg_points_per_segment": round(
                    points_count / max(segments_count, 1), 0
                ),
            }

            # Test connection responsiveness
            import time

            start_time = time.time()
            try:
                self.storage.client.get_collection(collection_name)
                response_time = time.time() - start_time
                health_info["response_time_ms"] = round(response_time * 1000, 1)
                health_info["connection_ok"] = response_time < 1.0  # 1 second timeout
            except Exception as e:
                health_info["connection_ok"] = False
                health_info["connection_error"] = str(e)

        except Exception as e:
            health_info["analysis_error"] = str(e)

        return health_info

    def _get_health_explanation(
        self, health_status: str, health_details: dict[str, Any]
    ) -> str:
        """Get human-readable explanation of health status with actionable advice."""
        progress = health_details.get("optimization_progress", 0)

        explanations = {
            "HEALTHY": "Collection is fully optimized and performing well",
            "EMPTY": "Collection exists but contains no data",
            "INDEXING": f"Indexing in progress ({progress:.1f}% complete) - normal during background optimization",
            "DEGRADED": f"Critical issue: Only {progress:.1f}% indexed - searches will be extremely slow. Restart Qdrant or check disk space.",
            "OPTIMIZING": "Background optimizations running - performance may be temporarily reduced",
            "OPTIMIZATION_PENDING": "Optimizations paused (restart detected) - will resume automatically",
            "FAILED": "Collection in failed state - check Qdrant logs immediately",
            "UNHEALTHY": "Connection or configuration problems detected",
            "UNKNOWN": "Status cannot be determined - verify Qdrant connection",
        }

        explanation = explanations.get(
            health_status, f"Unknown status: {health_status}"
        )

        # Add critical issue warnings
        if health_status == "DEGRADED" and progress < 10:
            explanation += " ⚠️ URGENT: Vector search unusable!"
        elif health_status == "FAILED":
            explanation += " 🔥 CRITICAL: Immediate attention required!"

        return explanation

    def get_collection_stats_direct_api(self, collection_name: str) -> dict[str, Any]:
        """Get collection statistics using direct Qdrant API methods."""
        try:
            # Direct API call to get collection info
            collection_info = self.storage.client.get_collection(collection_name)

            # Extract raw statistics from API response
            if hasattr(collection_info, "model_dump"):
                info_dict = collection_info.model_dump()
            elif hasattr(collection_info, "dict"):
                info_dict = collection_info.dict()
            else:
                info_dict = (
                    collection_info.__dict__
                    if hasattr(collection_info, "__dict__")
                    else {}
                )

            # Parse the response structure
            status = info_dict.get("status", "UNKNOWN")
            config = info_dict.get("config", {})
            vectors_count = info_dict.get("vectors_count", 0)
            indexed_vectors_count = info_dict.get("indexed_vectors_count", 0)
            points_count = info_dict.get("points_count", 0)
            segments_count = info_dict.get("segments_count", 0)

            # Calculate health metrics
            indexing_progress = 0.0
            if points_count > 0:
                indexing_progress = (indexed_vectors_count / points_count) * 100

            return {
                "collection_name": collection_name,
                "status": status,
                "points_count": points_count,
                "vectors_count": vectors_count,
                "indexed_vectors_count": indexed_vectors_count,
                "segments_count": segments_count,
                "indexing_progress": round(indexing_progress, 2),
                "config": config,
                "raw_response": info_dict,
            }

        except Exception as e:
            return {
                "collection_name": collection_name,
                "error": f"Direct API call failed: {str(e)}",
                "error_type": type(e).__name__,
            }

    def _get_health_status_from_api(self, collection_name: str) -> dict[str, Any]:
        """Get health status using direct API call - example implementation."""
        try:
            # Get raw collection info from direct API
            api_stats = self.get_collection_stats_direct_api(collection_name)

            if "error" in api_stats:
                return {
                    "status": "API_ERROR",
                    "message": api_stats["error"],
                    "details": "Failed to connect to Qdrant API",
                }

            # Analyze the direct API response
            status = api_stats.get("status", "UNKNOWN").upper()
            points_count = api_stats.get("points_count", 0)
            indexed_count = api_stats.get("indexed_vectors_count", 0)
            segments_count = api_stats.get("segments_count", 0)
            progress = api_stats.get("indexing_progress", 0)

            # Direct health determination based on API values
            if status == "GREEN":
                if points_count == 0:
                    health_status = "EMPTY_COLLECTION"
                elif progress >= 98.0:
                    health_status = "FULLY_OPTIMIZED"
                elif progress >= 90.0:
                    health_status = "INDEXING_ACTIVE"
                else:
                    health_status = "PERFORMANCE_DEGRADED"
            elif status == "YELLOW":
                health_status = "OPTIMIZATION_RUNNING"
            elif status == "RED":
                health_status = "CRITICAL_FAILURE"
            elif status == "GREY":
                health_status = "OPTIMIZATION_PAUSED"
            else:
                health_status = "STATUS_UNKNOWN"

            # Calculate additional diagnostics
            diagnostics = {
                "indexing_efficiency": round(progress, 2),
                "segment_density": round(points_count / max(segments_count, 1), 0),
                "fragmentation_ratio": round(
                    segments_count / max(points_count / 1000, 1), 2
                ),
                "index_completeness": "Complete" if progress >= 98 else "Incomplete",
            }

            # Determine severity level
            if health_status in ["CRITICAL_FAILURE", "PERFORMANCE_DEGRADED"]:
                severity = "HIGH"
            elif health_status in ["INDEXING_ACTIVE", "OPTIMIZATION_RUNNING"]:
                severity = "MEDIUM"
            else:
                severity = "LOW"

            return {
                "api_status": status,
                "health_status": health_status,
                "severity": severity,
                "indexing_progress": progress,
                "diagnostics": diagnostics,
                "recommendations": self._get_health_recommendations(
                    health_status, diagnostics
                ),
                "timestamp": datetime.now().isoformat(),
                "raw_metrics": {
                    "points": points_count,
                    "indexed": indexed_count,
                    "segments": segments_count,
                },
            }

        except Exception as e:
            return {
                "status": "HEALTH_CHECK_FAILED",
                "error": str(e),
                "error_type": type(e).__name__,
                "timestamp": datetime.now().isoformat(),
            }

    def _get_health_recommendations(
        self, health_status: str, diagnostics: dict[str, Any]
    ) -> list[str]:
        """Get actionable recommendations based on health status."""
        recommendations = []

        if health_status == "PERFORMANCE_DEGRADED":
            recommendations.extend(
                [
                    "🔄 Restart Qdrant service to trigger re-indexing",
                    "💾 Check available disk space (indexing may be stalled)",
                    "⏱️ Monitor indexing progress - may take time for large collections",
                    "🔍 Consider reducing collection size if performance persists",
                ]
            )
        elif health_status == "CRITICAL_FAILURE":
            recommendations.extend(
                [
                    "🚨 Check Qdrant server logs immediately",
                    "🔧 Verify Qdrant configuration and memory limits",
                    "💽 Check disk space and I/O performance",
                    "🔄 Restart Qdrant service if safe to do so",
                ]
            )
        elif health_status == "INDEXING_ACTIVE":
            recommendations.extend(
                [
                    "⏳ Wait for indexing to complete (normal operation)",
                    "📊 Monitor progress - should improve over time",
                    "⚡ Avoid heavy query load during indexing",
                ]
            )
        elif health_status == "OPTIMIZATION_RUNNING":
            recommendations.extend(
                [
                    "⚙️ Background optimization in progress",
                    "🎯 Performance may be temporarily reduced",
                    "📈 Should automatically return to optimal state",
                ]
            )
        elif health_status == "EMPTY_COLLECTION":
            recommendations.extend(
                [
                    "📋 Collection is empty - add data to begin indexing",
                    "🔄 Run indexer script to populate collection",
                ]
            )
        elif health_status == "FULLY_OPTIMIZED":
            recommendations.extend(
                [
                    "✅ Collection is performing optimally",
                    "📊 Monitor regularly to maintain performance",
                ]
            )

        # Add fragmentation-specific recommendations
        if diagnostics.get("fragmentation_ratio", 0) > 10:
            recommendations.append(
                "🗂️ High fragmentation detected - consider collection optimization"
            )

        return recommendations

    def _get_tracked_files_count(self, collection_name: str) -> int:
        """Get count of files actually tracked in indexer state files for this collection."""
        try:
            # Check config-registered projects first
            config_path = Path.home() / ".claude-indexer" / "config.json"
            if config_path.exists():
                with open(config_path) as f:
                    config = json.load(f)
                projects = config.get("projects", [])
                for project in projects:
                    if project.get("collection") == collection_name:
                        project_path = Path(project.get("path", ""))
                        if project_path.exists():
                            state_file = project_path / ".claude-indexer" / f"{collection_name}.json"
                            if state_file.exists():
                                with open(state_file) as f:
                                    state_data = json.load(f)
                                return len([k for k in state_data if not k.startswith("_")])
                        break

            # Fallback: search for state files - prioritize subdirectories for test collections
            search_paths = [
                *list(Path.cwd().parent.glob(f"*/.claude-indexer/{collection_name}.json")),  # Parent directory siblings
                *list(Path.cwd().glob(f"*/*/.claude-indexer/{collection_name}.json")),
                *list(Path.cwd().glob(f"*/.claude-indexer/{collection_name}.json")),
                Path.cwd() / ".claude-indexer" / f"{collection_name}.json",
            ]

            for state_path in search_paths:
                if state_path.exists():
                    with open(state_path) as f:
                        state_data = json.load(f)
                    count = len([k for k in state_data if not k.startswith("_")])
                    if count > 0:  # Return first non-empty state file
                        return count

        except Exception:
            pass

        return 0

    def get_database_overview(self) -> dict[str, Any]:
        """Get overall database statistics."""
        collections = self.get_all_collections()

        total_points = 0
        total_files = 0
        total_manual = 0
        total_automated = 0
        all_file_types = Counter()
        health_summary = Counter()

        collection_stats = {}

        for collection in collections:
            stats = self.get_collection_stats(collection)
            collection_stats[collection] = stats

            if "error" not in stats:
                total_points += stats.get("points_count", 0)
                total_manual += stats.get("manual_entries_count", 0)
                total_automated += stats.get("automated_entries_count", 0)

                # Sum file types
                file_types_data = stats.get("file_types", {})
                if isinstance(file_types_data, dict):
                    for ext, count in file_types_data.get(
                        "file_extensions", {}
                    ).items():
                        all_file_types[ext] += count
                    total_files += file_types_data.get("total_files", 0)

                # Count health statuses
                health_summary[stats.get("health_status", "UNKNOWN")] += 1

        return {
            "timestamp": datetime.now().isoformat(),
            "summary": {
                "total_collections": len(collections),
                "total_entries": total_points,
                "total_files": total_files,
                "manual_entries": total_manual,
                "automated_entries": total_automated,
                "file_types": dict(all_file_types),
                "health_summary": dict(health_summary),
            },
            "collections": collection_stats,
        }

    def print_stats(self, detailed: bool = False, collection_name: str | None = None):
        """Print formatted statistics."""
        if collection_name:
            # Single collection stats
            stats = self.get_collection_stats(collection_name)
            self._print_collection_stats(collection_name, stats, detailed=True)
        else:
            # Database overview
            overview = self.get_database_overview()
            self._print_overview(overview, detailed)

    def _print_overview(self, overview: dict[str, Any], detailed: bool):
        """Print database overview statistics with improved layout."""
        summary = overview["summary"]

        # Header block
        print("🔍 QDRANT DATABASE STATISTICS")
        print("=" * 70)
        print()

        # Core metrics block
        print("📊 CORE METRICS")
        print("-" * 30)
        print(f"Collections:      {summary['total_collections']:>8}")
        print(f"Total Entries:    {summary['total_entries']:>8,}")
        print(f"Files Indexed:    {summary['total_files']:>8,}")
        print(f"Manual Entries:   {summary['manual_entries']:>8,}")
        print(f"Automated:        {summary['automated_entries']:>8,}")
        print("\n" * 2)  # 50px equivalent spacing

        # Health status block
        print("🏥 HEALTH STATUS")
        print("-" * 30)
        for status, count in summary["health_summary"].items():
            emoji = {
                "HEALTHY": "✅",
                "UNHEALTHY": "❌",
                "INDEXING": "⏳",
                "EMPTY": "📭",
                "DEGRADED": "⚠️",
                "OPTIMIZING": "🔄",
                "OPTIMIZATION_PENDING": "⏸️",
                "FAILED": "🔥",
                "UNKNOWN": "❓",
            }.get(status, "❓")
            print(f"{emoji} {status:<18} {count:>6}")
        print("\n" * 2)  # 50px equivalent spacing

        # File types block (if any files)
        if summary["file_types"]:
            print("📄 FILE DISTRIBUTION")
            print("-" * 30)

            # Show all file types, but with special icons for .py and .md
            for ext, count in sorted(
                summary["file_types"].items(), key=lambda x: x[1], reverse=True
            ):
                if ext == ".py":
                    print(f"🐍 Python (.py):     {count:>6,}")
                elif ext == ".md":
                    print(f"📝 Markdown (.md):   {count:>6,}")
                elif ext == ".txt":
                    print(f"📄 Text (.txt):      {count:>6,}")
                elif ext == ".json":
                    print(f"⚙️  JSON (.json):     {count:>6,}")
                elif ext == ".yaml" or ext == ".yml":
                    print(f"📋 YAML ({ext}):      {count:>6,}")
                elif ext == ".toml":
                    print(f"🔧 TOML (.toml):     {count:>6,}")
                elif ext == ".ini":
                    print(f"⚙️  INI (.ini):       {count:>6,}")
                elif ext == ".cfg":
                    print(f"⚙️  Config (.cfg):    {count:>6,}")
                elif ext == ".sh":
                    print(f"🔨 Shell (.sh):      {count:>6,}")
                elif ext == ".js":
                    print(f"💛 JavaScript (.js): {count:>6,}")
                elif ext == ".ts":
                    print(f"🔷 TypeScript (.ts): {count:>6,}")
                elif ext == ".html":
                    print(f"🌐 HTML (.html):     {count:>6,}")
                elif ext == ".css":
                    print(f"🎨 CSS (.css):       {count:>6,}")
                elif ext == ".sql":
                    print(f"🗄️  SQL (.sql):       {count:>6,}")
                elif ext == "no_extension":
                    print(f"📝 No extension:     {count:>6,}")
                else:
                    print(f"📄 {ext:<15}   {count:>6,}")

            print("\n" * 2)  # 50px equivalent spacing

        if detailed:
            print("📚 COLLECTION BREAKDOWN")
            print("=" * 70)
            print()
            for collection, stats in overview["collections"].items():
                self._print_collection_stats(collection, stats, detailed=False)
                print("\n")  # Space between collections

    def _print_collection_stats(
        self, collection_name: str, stats: dict[str, Any], detailed: bool
    ):
        """Print individual collection statistics."""
        if "error" in stats:
            print(f"❌ {collection_name}: ERROR - {stats['error']}")
            return

        health_emoji = {
            "HEALTHY": "✅",
            "UNHEALTHY": "❌",
            "INDEXING": "⏳",
            "EMPTY": "📭",
            "DEGRADED": "⚠️",
            "OPTIMIZING": "🔄",
            "OPTIMIZATION_PENDING": "⏸️",
            "FAILED": "🔥",
            "UNKNOWN": "❓",
        }.get(stats.get("health_status"), "❓")

        print(f"{health_emoji} {collection_name}")
        print(f"  📊 Points: {stats.get('points_count', 0):,}")
        print(f"  🔍 Indexed: {stats.get('indexed_vectors_count', 0):,}")
        print(f"  🤖 Automated: {stats.get('automated_entries_count', 0):,}")

        # Show health details with explanations
        health_details = stats.get("health_details", {})
        stats.get("health_status", "UNKNOWN")
        stats.get("direct_api_health", {})

        if health_details:
            # Show simplified health metrics only
            if health_details.get("optimization_progress", 0) >= 0:
                progress = health_details["optimization_progress"]
                print(
                    f"  🏥 Indexing:  ✅  {progress:.1f}% ({stats.get('indexed_vectors_count', 0):,}/{stats.get('points_count', 0):,})"
                )

            if health_details.get("segment_health") != "UNKNOWN":
                seg_health = health_details["segment_health"]
                segments = stats.get("segments_count", 0)
                print(f"     Segments: {seg_health} ({segments} segments)")

            # Show indexing threshold from direct API data
            if "raw_config" in health_details and health_details["raw_config"]:
                raw_config = health_details["raw_config"]
                if hasattr(raw_config, "optimizer_config"):
                    optimizer_config = raw_config.optimizer_config
                    if hasattr(optimizer_config, "indexing_threshold"):
                        threshold = optimizer_config.indexing_threshold
                        if threshold is not None:
                            print(f"  ⚙️  Threshold: {threshold:,} KB")

            if health_details.get("response_time_ms"):
                response_time = health_details["response_time_ms"]
                response_rating = (
                    "excellent"
                    if response_time < 10
                    else "good"
                    if response_time < 50
                    else "slow"
                )
                print(f"     Response: {response_time:.1f}ms ({response_rating})")

        if detailed:
            print(f"  📏 Vector Size: {stats.get('vector_size', 0)}")
            print(f"  📐 Distance: {stats.get('distance_metric', 'unknown')}")
            print(f"  🗂️  Segments: {stats.get('segments_count', 0)}")

            file_analysis = stats.get("file_types", {})
            if file_analysis:
                print("\n" * 1)  # Block spacing

                # Files section
                if file_analysis.get("total_files", 0) > 0:
                    print("  📁 FILES INDEXED")
                    print("  " + "-" * 20)
                    print(
                        f"    Total Vectored Files: {file_analysis['total_files']:>6}"
                    )

                    # Add tracked files count
                    tracked_count = stats.get("tracked_files_count", 0)
                    print(f"    Tracked Files:        {tracked_count:>6}")

                    file_extensions = file_analysis.get("file_extensions", {})
                    if file_extensions:
                        # Extension to display name mapping
                        ext_display_names = {
                            ".py": "Python (.py)",
                            ".js": "JavaScript (.js)",
                            ".jsx": "React (.jsx)",
                            ".ts": "TypeScript (.ts)",
                            ".tsx": "React TS (.tsx)",
                            ".mjs": "ES Module (.mjs)",
                            ".cjs": "CommonJS (.cjs)",
                            ".md": "Markdown (.md)",
                            ".markdown": "Markdown (.markdown)",
                            ".json": "JSON (.json)",
                            ".yaml": "YAML (.yaml)",
                            ".yml": "YAML (.yml)",
                            ".html": "HTML (.html)",
                            ".htm": "HTML (.htm)",
                            ".css": "CSS (.css)",
                            ".scss": "Sass (.scss)",
                            ".sass": "Sass (.sass)",
                            ".txt": "Text (.txt)",
                            ".log": "Log (.log)",
                            ".csv": "CSV (.csv)",
                            ".ini": "Config (.ini)",
                            ".conf": "Config (.conf)",
                            ".cfg": "Config (.cfg)",
                        }

                        # Sort by count (descending) and then by extension name
                        sorted_extensions = sorted(
                            [
                                (ext, count)
                                for ext, count in file_extensions.items()
                                if count > 0
                            ],
                            key=lambda x: (-x[1], x[0]),
                        )

                        for ext, count in sorted_extensions:
                            display_name = ext_display_names.get(
                                ext, f"{ext.upper()[1:]} ({ext})"
                            )
                            print(f"    {display_name:<20} {count:>6}")
                    print()

                # v2.4 Chunk Types section
                chunk_type_breakdown = file_analysis.get("chunk_type_breakdown", {})
                manual_count = stats.get("manual_entries_count", 0)

                if chunk_type_breakdown or manual_count > 0:
                    print("  🧩 CHUNK TYPES")
                    print("  " + "-" * 25)

                    # Show manual entries first
                    if manual_count > 0:
                        print(f"    ✍️  Manual:          {manual_count:>6,}")

                    # Show auto-generated chunk types
                    for chunk_type, count in sorted(
                        chunk_type_breakdown.items(), key=lambda x: x[1], reverse=True
                    ):
                        if chunk_type == "metadata":
                            print(f"    📋 Metadata:         {count:>6,}")
                        elif chunk_type == "implementation":
                            print(f"    💻 Implementation:   {count:>6,}")
                        elif chunk_type == "relation":
                            print(f"    🔗 Relation:         {count:>6,}")
                        else:
                            print(f"    📄 {chunk_type:<15} {count:>6,}")
                    print()

                # Entity types section showing total counts
                entity_breakdown = file_analysis.get("entity_breakdown", {})

                if entity_breakdown:
                    print("  🏷️  ENTITY TYPES (TOP 10)")
                    print("  " + "-" * 30)
                    
                    # Get top 10 by total count
                    sorted_entities = sorted(
                        entity_breakdown.items(),
                        key=lambda x: x[1],
                        reverse=True,
                    )[:10]

                    for entity_type, count in sorted_entities:
                        print(f"    {entity_type:<25} {count:>6,}")
                    print()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Qdrant Vector Database Statistics")
    parser.add_argument("-c", "--collection", help="Show stats for specific collection")
    parser.add_argument(
        "-d", "--detailed", action="store_true", help="Show detailed statistics"
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--config", help="Path to config file")
    parser.add_argument(
        "--watch",
        type=int,
        metavar="SECONDS",
        help="Watch mode: refresh every N seconds (30-300 recommended, 5s=high cost)",
    )
    parser.add_argument(
        "--light",
        action="store_true",
        help="Light monitoring mode (no scroll operations, faster but less detailed)",
    )
    parser.add_argument(
        "--health",
        action="store_true",
        help="Show detailed health diagnostics and troubleshooting info",
    )

    args = parser.parse_args()

    try:
        # Load configuration
        config_path = args.config or "settings.txt"
        if not os.path.exists(config_path):
            print(f"❌ Config file not found: {config_path}")
            print("💡 Create settings.txt with your Qdrant configuration")
            return 1

        # Simple config creation - create QdrantStore directly
        from claude_indexer.storage.qdrant import QdrantStore

        # Read settings manually
        settings = {}
        with open(config_path) as f:
            for line in f:
                if "=" in line and not line.strip().startswith("#"):
                    key, value = line.strip().split("=", 1)
                    settings[key.strip()] = value.strip()

        # Create QdrantStore directly
        storage = QdrantStore(
            url=settings.get("qdrant_url", "http://localhost:6333"),
            api_key=settings.get("qdrant_api_key"),
        )

        # Create collector with direct storage
        collector = QdrantStatsCollector.__new__(QdrantStatsCollector)
        collector.storage = storage

        # Watch mode with refresh
        if args.watch:
            import time

            # Validate interval
            if args.watch < 5:
                print("⚠️  WARNING: Intervals <5s may overload Qdrant server")
            elif args.watch < 30:
                print(f"⚠️  WARNING: {args.watch}s refresh is resource-intensive")
                print("💡 Recommended: 30-300s for production, 60-120s optimal")

            print(f"📊 Starting watch mode: refreshing every {args.watch}s")
            print("Press Ctrl+C to stop")
            print("=" * 50)

            try:
                while True:
                    # Clear screen with ANSI escape sequences
                    print("\033[H\033[2J\033[3J", end="", flush=True)

                    # Show timestamp
                    print(f"🕐 Last update: {datetime.now().strftime('%H:%M:%S')}")
                    print()

                    # Show stats
                    if args.json:
                        if args.collection:
                            stats = collector.get_collection_stats(args.collection)
                            print(json.dumps(stats, indent=2))
                        else:
                            overview = collector.get_database_overview()
                            print(json.dumps(overview, indent=2))
                    else:
                        collector.print_stats(
                            detailed=args.detailed, collection_name=args.collection
                        )

                    # Show cost warning for high frequency
                    if args.watch <= 10:
                        print()
                        print("💰 HIGH COST MODE: 5-10s refresh = ~50k API calls/day")

                    time.sleep(args.watch)

            except KeyboardInterrupt:
                print("\n👋 Watch mode stopped")
                return 0

        # Single run mode
        else:
            if args.json:
                # JSON output
                if args.collection:
                    stats = collector.get_collection_stats(args.collection)
                    print(json.dumps(stats, indent=2))
                else:
                    overview = collector.get_database_overview()
                    print(json.dumps(overview, indent=2))
            else:
                # Formatted output
                collector.print_stats(
                    detailed=args.detailed, collection_name=args.collection
                )

        return 0

    except Exception as e:
        print(f"❌ Error: {e}")
        return 1


if __name__ == "__main__":
    exit(main())
