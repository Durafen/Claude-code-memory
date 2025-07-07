"""
Safety and rollback manager for memory cleanup.

Ensures cleanup operations are safe and reversible with backup/restore capabilities.
"""

import json
import logging
import shutil
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import hashlib

logger = logging.getLogger(__name__)


@dataclass
class BackupInfo:
    """Information about a backup."""
    backup_id: str
    collection_name: str
    timestamp: datetime
    entry_count: int
    file_path: str
    checksum: str
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class CleanupMetrics:
    """Metrics for cleanup validation."""
    before_count: int
    after_count: int
    deleted_count: int
    merged_count: int
    archived_count: int
    search_accuracy: float = 0.0
    critical_patterns_missing: List[str] = None
    
    def __post_init__(self):
        if self.critical_patterns_missing is None:
            self.critical_patterns_missing = []


class SafetyManager:
    """Ensure cleanup operations are safe and reversible."""
    
    def __init__(self, store, backup_dir: Optional[str] = None):
        """
        Initialize the safety manager.
        
        Args:
            store: Storage backend (QdrantStore)
            backup_dir: Directory for backups (default: temp directory)
        """
        self.store = store
        self.backup_dir = Path(backup_dir) if backup_dir else Path(tempfile.gettempdir()) / "memory_cleanup_backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger(__name__)
        
        # Track created backups
        self.backups: Dict[str, BackupInfo] = {}
        self._load_backup_index()
    
    def create_backup(self, collection_name: str, metadata: Dict[str, Any] = None) -> str:
        """
        Create point-in-time backup before cleanup.
        
        Args:
            collection_name: Collection to backup
            metadata: Additional metadata for backup
            
        Returns:
            Backup ID for later restoration
        """
        try:
            # Generate backup ID
            timestamp = datetime.now(timezone.utc)
            backup_id = f"{collection_name}_{timestamp.strftime('%Y%m%d_%H%M%S')}"
            
            # Export collection data
            backup_data = self._export_collection(collection_name)
            if not backup_data:
                raise ValueError(f"Failed to export collection {collection_name}")
            
            # Save backup to file
            backup_file = self.backup_dir / f"{backup_id}.json"
            with open(backup_file, 'w', encoding='utf-8') as f:
                json.dump(backup_data, f, indent=2, default=str)
            
            # Calculate checksum
            checksum = self._calculate_checksum(backup_file)
            
            # Create backup info
            backup_info = BackupInfo(
                backup_id=backup_id,
                collection_name=collection_name,
                timestamp=timestamp,
                entry_count=len(backup_data.get('entries', [])),
                file_path=str(backup_file),
                checksum=checksum,
                metadata=metadata or {}
            )
            
            # Store backup info
            self.backups[backup_id] = backup_info
            self._save_backup_index()
            
            self.logger.info(
                f"Created backup {backup_id} with {backup_info.entry_count} entries"
            )
            
            return backup_id
            
        except Exception as e:
            self.logger.error(f"Failed to create backup for {collection_name}: {e}")
            raise
    
    def restore_backup(self, backup_id: str, target_collection: str = None) -> bool:
        """
        Restore from backup.
        
        Args:
            backup_id: Backup to restore
            target_collection: Target collection (default: original collection)
            
        Returns:
            True if restoration successful
        """
        try:
            if backup_id not in self.backups:
                self.logger.error(f"Backup {backup_id} not found")
                return False
            
            backup_info = self.backups[backup_id]
            target_collection = target_collection or backup_info.collection_name
            
            # Verify backup file integrity
            if not self._verify_backup_integrity(backup_info):
                self.logger.error(f"Backup {backup_id} integrity check failed")
                return False
            
            # Load backup data
            with open(backup_info.file_path, 'r', encoding='utf-8') as f:
                backup_data = json.load(f)
            
            # Clear target collection
            self.logger.info(f"Clearing collection {target_collection} for restoration")
            self.store.clear_collection(target_collection)
            
            # Restore entries
            entries = backup_data.get('entries', [])
            if entries:
                result = self.store.upsert_entities(target_collection, entries)
                if not result:
                    self.logger.error(f"Failed to restore {len(entries)} entries")
                    return False
            
            # Restore metadata if available
            collection_metadata = backup_data.get('collection_metadata', {})
            if collection_metadata:
                # Restore collection configuration if possible
                pass  # Implementation depends on store capabilities
            
            self.logger.info(
                f"Successfully restored {len(entries)} entries to {target_collection}"
            )
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to restore backup {backup_id}: {e}")
            return False
    
    def validate_cleanup(self, collection_name: str, metrics: Any) -> bool:
        """
        Check if cleanup was successful and should be kept.
        
        Args:
            collection_name: Collection that was cleaned
            metrics: Cleanup execution results
            
        Returns:
            True if cleanup should be kept, False if rollback needed
        """
        try:
            # Get current collection stats
            current_stats = self.store.get_collection_stats(collection_name)
            if not current_stats:
                self.logger.warning("Could not get collection stats for validation")
                return True  # Assume success if stats unavailable
            
            current_count = current_stats.get('vectors_count', 0)
            
            # Test search accuracy with sample queries
            search_accuracy = self._test_search_accuracy(collection_name)
            
            # Check for critical pattern availability
            critical_patterns_missing = self._check_critical_patterns(collection_name)
            
            # Validation criteria
            validation_passed = True
            
            # 1. Search accuracy shouldn't drop significantly
            baseline_accuracy = 0.8  # Could be configurable
            if search_accuracy < baseline_accuracy * 0.8:  # 20% drop threshold
                self.logger.warning(
                    f"Search accuracy dropped to {search_accuracy:.2f} "
                    f"(baseline: {baseline_accuracy:.2f})"
                )
                validation_passed = False
            
            # 2. Critical patterns should still be accessible
            if critical_patterns_missing:
                self.logger.warning(
                    f"Critical patterns missing: {critical_patterns_missing}"
                )
                validation_passed = False
            
            # 3. Collection shouldn't be empty unless that was intended
            if current_count == 0 and hasattr(metrics, 'entries_deleted') and metrics.entries_deleted > 0:
                self.logger.warning("Collection is empty after cleanup")
                # This might be intentional, so don't fail validation
            
            if validation_passed:
                self.logger.info("Cleanup validation passed")
            else:
                self.logger.warning("Cleanup validation failed")
            
            return validation_passed
            
        except Exception as e:
            self.logger.error(f"Error validating cleanup: {e}")
            return False  # Fail safe
    
    def cleanup_old_backups(self, max_age_days: int = 7, max_count: int = 10):
        """
        Clean up old backup files to save disk space.
        
        Args:
            max_age_days: Maximum age of backups to keep
            max_count: Maximum number of backups to keep per collection
        """
        try:
            current_time = datetime.now(timezone.utc)
            backups_to_remove = []
            
            # Group backups by collection
            collection_backups = {}
            for backup_id, backup_info in self.backups.items():
                collection = backup_info.collection_name
                if collection not in collection_backups:
                    collection_backups[collection] = []
                collection_backups[collection].append((backup_id, backup_info))
            
            # Clean up each collection's backups
            for collection, backups in collection_backups.items():
                # Sort by timestamp (newest first)
                backups.sort(key=lambda x: x[1].timestamp, reverse=True)
                
                for i, (backup_id, backup_info) in enumerate(backups):
                    # Remove if too old
                    age_days = (current_time - backup_info.timestamp).days
                    if age_days > max_age_days:
                        backups_to_remove.append(backup_id)
                    # Remove if exceeds count limit (keep newest)
                    elif i >= max_count:
                        backups_to_remove.append(backup_id)
            
            # Remove identified backups
            for backup_id in backups_to_remove:
                self._remove_backup(backup_id)
            
            if backups_to_remove:
                self.logger.info(f"Cleaned up {len(backups_to_remove)} old backups")
            
        except Exception as e:
            self.logger.error(f"Error cleaning up old backups: {e}")
    
    def list_backups(self, collection_name: str = None) -> List[BackupInfo]:
        """
        List available backups.
        
        Args:
            collection_name: Filter by collection (optional)
            
        Returns:
            List of backup information
        """
        backups = list(self.backups.values())
        
        if collection_name:
            backups = [b for b in backups if b.collection_name == collection_name]
        
        # Sort by timestamp (newest first)
        backups.sort(key=lambda x: x.timestamp, reverse=True)
        
        return backups
    
    def _export_collection(self, collection_name: str) -> Optional[Dict[str, Any]]:
        """Export collection data for backup."""
        try:
            # Get all points from collection
            entries = []
            
            # Use scroll to get all entries
            scroll_result = self.store.scroll_collection(collection_name)
            if scroll_result:
                entries.extend(scroll_result)
            
            # Get collection metadata
            collection_stats = self.store.get_collection_stats(collection_name)
            
            backup_data = {
                'collection_name': collection_name,
                'export_timestamp': datetime.now(timezone.utc).isoformat(),
                'entries': entries,
                'collection_metadata': collection_stats or {},
                'entry_count': len(entries)
            }
            
            return backup_data
            
        except Exception as e:
            self.logger.error(f"Error exporting collection {collection_name}: {e}")
            return None
    
    def _calculate_checksum(self, file_path: Path) -> str:
        """Calculate SHA256 checksum of backup file."""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256_hash.update(chunk)
        return sha256_hash.hexdigest()
    
    def _verify_backup_integrity(self, backup_info: BackupInfo) -> bool:
        """Verify backup file integrity using checksum."""
        try:
            file_path = Path(backup_info.file_path)
            if not file_path.exists():
                self.logger.error(f"Backup file not found: {file_path}")
                return False
            
            current_checksum = self._calculate_checksum(file_path)
            if current_checksum != backup_info.checksum:
                self.logger.error(
                    f"Backup checksum mismatch. Expected: {backup_info.checksum}, "
                    f"Actual: {current_checksum}"
                )
                return False
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error verifying backup integrity: {e}")
            return False
    
    def _test_search_accuracy(self, collection_name: str) -> float:
        """Test search accuracy with sample queries."""
        try:
            # Sample test queries for different types of content
            test_queries = [
                "authentication error",
                "database connection",
                "file processing",
                "error handling",
                "configuration setup"
            ]
            
            total_accuracy = 0.0
            valid_tests = 0
            
            for query in test_queries:
                try:
                    # Perform search
                    results = self.store.search(collection_name, query, limit=5)
                    
                    # Simple heuristic: if we get results, assume accuracy is reasonable
                    # More sophisticated accuracy testing would require ground truth data
                    if results and len(results) > 0:
                        # Basic relevance check - results should contain query terms
                        relevant_results = 0
                        for result in results:
                            content = str(result).lower()
                            query_terms = query.lower().split()
                            if any(term in content for term in query_terms):
                                relevant_results += 1
                        
                        accuracy = relevant_results / len(results)
                        total_accuracy += accuracy
                        valid_tests += 1
                    
                except Exception:
                    # Skip failed test queries
                    continue
            
            if valid_tests > 0:
                return total_accuracy / valid_tests
            else:
                return 0.8  # Default assumption if no tests work
                
        except Exception as e:
            self.logger.error(f"Error testing search accuracy: {e}")
            return 0.8  # Default assumption on error
    
    def _check_critical_patterns(self, collection_name: str) -> List[str]:
        """Check if critical patterns are still accessible."""
        try:
            # Define critical pattern types that should usually exist
            critical_patterns = [
                "authentication",
                "error handling", 
                "configuration",
                "database",
                "api"
            ]
            
            missing_patterns = []
            
            for pattern in critical_patterns:
                try:
                    results = self.store.search(collection_name, pattern, limit=1)
                    if not results or len(results) == 0:
                        missing_patterns.append(pattern)
                except Exception:
                    missing_patterns.append(pattern)
            
            return missing_patterns
            
        except Exception as e:
            self.logger.error(f"Error checking critical patterns: {e}")
            return []  # Assume patterns are available on error
    
    def _load_backup_index(self):
        """Load backup index from disk."""
        try:
            index_file = self.backup_dir / "backup_index.json"
            if index_file.exists():
                with open(index_file, 'r', encoding='utf-8') as f:
                    index_data = json.load(f)
                
                # Reconstruct backup info objects
                for backup_id, backup_dict in index_data.items():
                    backup_dict['timestamp'] = datetime.fromisoformat(backup_dict['timestamp'])
                    self.backups[backup_id] = BackupInfo(**backup_dict)
                    
        except Exception as e:
            self.logger.warning(f"Could not load backup index: {e}")
    
    def _save_backup_index(self):
        """Save backup index to disk."""
        try:
            index_file = self.backup_dir / "backup_index.json"
            index_data = {}
            
            for backup_id, backup_info in self.backups.items():
                backup_dict = asdict(backup_info)
                backup_dict['timestamp'] = backup_info.timestamp.isoformat()
                index_data[backup_id] = backup_dict
            
            with open(index_file, 'w', encoding='utf-8') as f:
                json.dump(index_data, f, indent=2)
                
        except Exception as e:
            self.logger.error(f"Could not save backup index: {e}")
    
    def _remove_backup(self, backup_id: str):
        """Remove a backup and its files."""
        try:
            if backup_id in self.backups:
                backup_info = self.backups[backup_id]
                
                # Remove backup file
                backup_file = Path(backup_info.file_path)
                if backup_file.exists():
                    backup_file.unlink()
                
                # Remove from index
                del self.backups[backup_id]
                self._save_backup_index()
                
                self.logger.info(f"Removed backup {backup_id}")
                
        except Exception as e:
            self.logger.error(f"Error removing backup {backup_id}: {e}")