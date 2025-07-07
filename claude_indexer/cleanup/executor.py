"""
Cleanup action executor with safety limits.

Executes cleanup actions with rollback capability and safety checks.
"""

import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum
from datetime import datetime, timezone

from .resolver import ResolutionPlan, ResolutionAction
from .safety import SafetyManager

logger = logging.getLogger(__name__)


class ActionStatus(Enum):
    """Status of cleanup actions."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class SafetyConfig:
    """Safety configuration for cleanup operations."""
    max_deletion_percentage: float = 15.0  # Max % of entries to delete
    require_confirmation: List[str] = None  # Actions requiring confirmation
    backup_before_cleanup: bool = True
    rollback_window_hours: int = 24
    dry_run: bool = True  # Default to dry run
    
    def __post_init__(self):
        if self.require_confirmation is None:
            self.require_confirmation = ["delete", "major_merge"]


@dataclass
class ExecutionResult:
    """Result of executing cleanup actions."""
    actions_executed: int = 0
    actions_failed: int = 0
    entries_deleted: int = 0
    entries_merged: int = 0
    entries_archived: int = 0
    entries_modified: int = 0
    backup_created: Optional[str] = None
    execution_time: float = 0.0
    errors: List[str] = None
    
    def __post_init__(self):
        if self.errors is None:
            self.errors = []


class CleanupExecutor:
    """Execute cleanup actions with safety limits."""
    
    def __init__(self, store, safety_manager: SafetyManager = None):
        """
        Initialize the cleanup executor.
        
        Args:
            store: Storage backend (QdrantStore)
            safety_manager: Safety manager instance
        """
        self.store = store
        self.safety_manager = safety_manager or SafetyManager(store)
        self.logger = logging.getLogger(__name__)
    
    def execute_actions(
        self, 
        actions: List[ResolutionPlan], 
        safety_config: SafetyConfig,
        collection_name: str
    ) -> ExecutionResult:
        """
        Apply cleanup actions with rollback capability.
        
        Args:
            actions: List of resolution plans to execute
            safety_config: Safety configuration
            collection_name: Target collection name
            
        Returns:
            ExecutionResult with statistics and status
        """
        start_time = datetime.now()
        result = ExecutionResult()
        
        try:
            # Safety checks
            if not self._validate_safety_limits(actions, safety_config, collection_name):
                result.errors.append("Safety limits exceeded, aborting execution")
                return result
            
            # Create backup if required
            if safety_config.backup_before_cleanup:
                backup_id = self.safety_manager.create_backup(collection_name)
                result.backup_created = backup_id
                self.logger.info(f"Created backup: {backup_id}")
            
            # Execute actions
            for action in actions:
                if safety_config.dry_run:
                    self._log_dry_run_action(action)
                    continue
                
                try:
                    self._execute_single_action(action, safety_config, collection_name, result)
                    result.actions_executed += 1
                except Exception as e:
                    self.logger.error(f"Failed to execute action {action.action}: {e}")
                    result.errors.append(f"Action {action.action} failed: {str(e)}")
                    result.actions_failed += 1
            
            # Validate results
            if not safety_config.dry_run:
                validation_success = self.safety_manager.validate_cleanup(
                    collection_name, result
                )
                
                if not validation_success:
                    self.logger.warning("Cleanup validation failed, consider rollback")
                    result.errors.append("Cleanup validation failed")
            
        except Exception as e:
            self.logger.error(f"Critical error during cleanup execution: {e}")
            result.errors.append(f"Critical error: {str(e)}")
        
        finally:
            result.execution_time = (datetime.now() - start_time).total_seconds()
        
        return result
    
    def _validate_safety_limits(
        self, 
        actions: List[ResolutionPlan], 
        safety_config: SafetyConfig,
        collection_name: str
    ) -> bool:
        """Validate that actions don't exceed safety limits."""
        try:
            # Get current collection stats
            stats = self.store.get_collection_stats(collection_name)
            if not stats:
                self.logger.warning("Could not get collection stats for safety validation")
                return True  # Allow execution if stats unavailable
            
            total_entries = stats.get('vectors_count', 0)
            
            # Count deletion actions
            total_deletions = 0
            for action in actions:
                total_deletions += len(action.entries_to_remove)
            
            # Check deletion percentage
            if total_entries > 0:
                deletion_percentage = (total_deletions / total_entries) * 100
                if deletion_percentage > safety_config.max_deletion_percentage:
                    self.logger.error(
                        f"Deletion percentage ({deletion_percentage:.1f}%) exceeds limit "
                        f"({safety_config.max_deletion_percentage}%)"
                    )
                    return False
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error validating safety limits: {e}")
            return False  # Fail safe
    
    def _execute_single_action(
        self, 
        action: ResolutionPlan, 
        safety_config: SafetyConfig,
        collection_name: str,
        result: ExecutionResult
    ):
        """Execute a single resolution plan."""
        self.logger.info(f"Executing {action.action} with confidence {action.confidence}")
        
        if action.action == ResolutionAction.KEEP_HIGHEST_QUALITY:
            self._execute_deletion(action.entries_to_remove, collection_name, result)
            
        elif action.action == ResolutionAction.MERGE_COMPATIBLE:
            self._execute_merge(action, collection_name, result)
            
        elif action.action == ResolutionAction.ARCHIVE_OUTDATED:
            self._execute_archive(action.entries_to_remove, collection_name, result)
            
        elif action.action == ResolutionAction.VERSION_SEPARATE:
            self._execute_versioning(action, collection_name, result)
            
        elif action.action == ResolutionAction.PLATFORM_SEPARATE:
            self._execute_platform_separation(action, collection_name, result)
            
        elif action.action == ResolutionAction.MANUAL_REVIEW:
            self.logger.info(f"Marked {len(action.entries_to_keep)} entries for manual review")
            # Don't actually modify entries marked for manual review
            
        else:
            self.logger.warning(f"Unknown action type: {action.action}")
    
    def _execute_deletion(
        self, 
        entries_to_delete: List[Dict[str, Any]], 
        collection_name: str,
        result: ExecutionResult
    ):
        """Execute deletion of entries."""
        if not entries_to_delete:
            return
        
        try:
            # Extract IDs for deletion
            ids_to_delete = []
            for entry in entries_to_delete:
                entry_id = entry.get('id') or entry.get('payload', {}).get('id')
                if entry_id:
                    ids_to_delete.append(entry_id)
            
            if ids_to_delete:
                # Use batch delete if available
                delete_result = self.store.delete_points(collection_name, ids_to_delete)
                if delete_result:
                    result.entries_deleted += len(ids_to_delete)
                    self.logger.info(f"Deleted {len(ids_to_delete)} entries")
                else:
                    result.errors.append(f"Failed to delete {len(ids_to_delete)} entries")
            
        except Exception as e:
            self.logger.error(f"Error executing deletion: {e}")
            result.errors.append(f"Deletion error: {str(e)}")
    
    def _execute_merge(
        self, 
        action: ResolutionPlan, 
        collection_name: str,
        result: ExecutionResult
    ):
        """Execute merging of compatible entries."""
        if not action.merged_entry or not action.entries_to_merge:
            return
        
        try:
            # Delete original entries
            self._execute_deletion(action.entries_to_merge, collection_name, result)
            
            # Create merged entry
            merged_result = self.store.upsert_entities(collection_name, [action.merged_entry])
            if merged_result:
                result.entries_merged += len(action.entries_to_merge)
                self.logger.info(f"Merged {len(action.entries_to_merge)} entries into 1")
            else:
                result.errors.append("Failed to create merged entry")
            
        except Exception as e:
            self.logger.error(f"Error executing merge: {e}")
            result.errors.append(f"Merge error: {str(e)}")
    
    def _execute_archive(
        self, 
        entries_to_archive: List[Dict[str, Any]], 
        collection_name: str,
        result: ExecutionResult
    ):
        """Execute archiving of outdated entries."""
        if not entries_to_archive:
            return
        
        try:
            # For now, archiving means adding archive metadata and moving to archive collection
            archive_collection = f"{collection_name}_archive"
            
            # Modify entries to mark as archived
            archived_entries = []
            for entry in entries_to_archive:
                archived_entry = entry.copy()
                payload = archived_entry.get('payload', archived_entry)
                payload['archived_at'] = datetime.now(timezone.utc).isoformat()
                payload['archived_from'] = collection_name
                archived_entries.append(archived_entry)
            
            # Create archive collection if it doesn't exist
            try:
                self.store.create_collection(archive_collection)
            except Exception:
                pass  # Collection might already exist
            
            # Move to archive
            archive_result = self.store.upsert_entities(archive_collection, archived_entries)
            if archive_result:
                # Delete from main collection
                self._execute_deletion(entries_to_archive, collection_name, result)
                result.entries_archived += len(archived_entries)
                self.logger.info(f"Archived {len(archived_entries)} entries")
            else:
                result.errors.append("Failed to archive entries")
            
        except Exception as e:
            self.logger.error(f"Error executing archive: {e}")
            result.errors.append(f"Archive error: {str(e)}")
    
    def _execute_versioning(
        self, 
        action: ResolutionPlan, 
        collection_name: str,
        result: ExecutionResult
    ):
        """Execute version-based organization."""
        # For versioning, we typically update metadata rather than delete
        try:
            modified_entries = []
            for entry in action.entries_to_keep:
                modified_entry = entry.copy()
                payload = modified_entry.get('payload', modified_entry)
                
                # Add version organization metadata
                payload['version_organized'] = True
                payload['organization_timestamp'] = datetime.now(timezone.utc).isoformat()
                modified_entries.append(modified_entry)
            
            # Update entries with new metadata
            update_result = self.store.upsert_entities(collection_name, modified_entries)
            if update_result:
                result.entries_modified += len(modified_entries)
                self.logger.info(f"Version-organized {len(modified_entries)} entries")
            else:
                result.errors.append("Failed to update version organization")
            
        except Exception as e:
            self.logger.error(f"Error executing versioning: {e}")
            result.errors.append(f"Versioning error: {str(e)}")
    
    def _execute_platform_separation(
        self, 
        action: ResolutionPlan, 
        collection_name: str,
        result: ExecutionResult
    ):
        """Execute platform-based organization."""
        # Similar to versioning, update metadata for platform organization
        try:
            modified_entries = []
            for entry in action.entries_to_keep:
                modified_entry = entry.copy()
                payload = modified_entry.get('payload', modified_entry)
                
                # Add platform organization metadata
                payload['platform_organized'] = True
                payload['organization_timestamp'] = datetime.now(timezone.utc).isoformat()
                modified_entries.append(modified_entry)
            
            # Update entries with new metadata
            update_result = self.store.upsert_entities(collection_name, modified_entries)
            if update_result:
                result.entries_modified += len(modified_entries)
                self.logger.info(f"Platform-organized {len(modified_entries)} entries")
            else:
                result.errors.append("Failed to update platform organization")
            
        except Exception as e:
            self.logger.error(f"Error executing platform separation: {e}")
            result.errors.append(f"Platform separation error: {str(e)}")
    
    def _log_dry_run_action(self, action: ResolutionPlan):
        """Log what would happen in dry run mode."""
        self.logger.info(f"[DRY RUN] Would execute {action.action}")
        self.logger.info(f"[DRY RUN] Reasoning: {action.reasoning}")
        self.logger.info(f"[DRY RUN] Confidence: {action.confidence}")
        
        if action.entries_to_remove:
            self.logger.info(f"[DRY RUN] Would remove {len(action.entries_to_remove)} entries")
        
        if action.entries_to_merge:
            self.logger.info(f"[DRY RUN] Would merge {len(action.entries_to_merge)} entries")
        
        if action.merged_entry:
            self.logger.info(f"[DRY RUN] Would create merged entry: {action.merged_entry.get('payload', {}).get('name', 'unnamed')}")