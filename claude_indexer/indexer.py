"""Core indexing orchestrator - stateless domain service."""

import time
import hashlib
import json
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Union, Set
from dataclasses import dataclass

from .config import IndexerConfig
from .analysis.parser import ParserRegistry, ParserResult
from .analysis.entities import Entity, Relation, EntityChunk, RelationChunk
from .embeddings.base import Embedder
from .storage.base import VectorStore
from .indexer_logging import get_logger

logger = get_logger()


def format_change(current: int, previous: int) -> str:
    """Format a change value with +/- indicator."""
    change = current - previous
    if change > 0:
        return f"{current} (+{change})"
    elif change < 0:
        return f"{current} ({change})"
    else:
        return f"{current} (+0)" if previous > 0 else str(current)


@dataclass
class IndexingResult:
    """Result of an indexing operation."""
    
    success: bool
    operation: str  # "full", "incremental", "single_file"
    
    # Metrics
    files_processed: int = 0
    files_failed: int = 0
    entities_created: int = 0
    relations_created: int = 0
    implementation_chunks_created: int = 0  # Progressive disclosure metric
    processing_time: float = 0.0
    
    # Cost tracking
    total_tokens: int = 0
    total_cost_estimate: float = 0.0
    embedding_requests: int = 0
    
    # File tracking
    processed_files: List[str] = None
    failed_files: List[str] = None
    
    # Errors and warnings
    errors: List[str] = None
    warnings: List[str] = None
    
    def __post_init__(self):
        if self.processed_files is None:
            self.processed_files = []
        if self.failed_files is None:
            self.failed_files = []
        if self.errors is None:
            self.errors = []
        if self.warnings is None:
            self.warnings = []
    
    @property
    def total_items(self) -> int:
        """Total entities and relations created."""
        return self.entities_created + self.relations_created
    
    @property
    def success_rate(self) -> float:
        """File processing success rate."""
        total = self.files_processed + self.files_failed
        if total == 0:
            return 1.0
        return self.files_processed / total
     
    @property  
    def duration(self) -> float:
        """Alias for processing_time for backward compatibility."""
        return self.processing_time


class CoreIndexer:
    """Stateless core indexing service orchestrating all components."""
    
    def __init__(self, config: IndexerConfig, embedder: Embedder, 
                 vector_store: VectorStore, project_path: Path):
        self.config = config
        self.embedder = embedder
        self.vector_store = vector_store
        self.project_path = project_path
        self.logger = get_logger()
        
        # Initialize parser registry
        self.parser_registry = ParserRegistry(project_path)
    
    def _create_batch_callback(self, collection_name: str):
        """Create a callback function for batch processing during streaming."""
        def batch_callback(entities, relations, chunks):
            """Process a batch of entities, relations, and chunks immediately."""
            try:
                # Git+Meta: compute changed entity IDs for event awareness
                changed_entity_ids = {f"{entity.file_path}::{entity.name}" if entity.file_path else entity.name for entity in entities} if entities else set()
                # Use existing _store_vectors method for immediate processing
                success = self._store_vectors(collection_name, entities, relations, chunks, changed_entity_ids)
                return success
            except Exception as e:
                self.logger.error(f"❌ Batch processing failed: {e}")
                return False
        
        return batch_callback
    
    def _should_use_batch_processing(self, file_path: Path) -> bool:
        """Determine if a file should use batch processing during parsing."""
        try:
            # Only JSON files with content_only mode should use batch processing
            if file_path.suffix != '.json':
                return False
            
            # Check if this is a project that uses content_only mode for JSON
            # Use the same config loading approach as ParserRegistry
            from .config.project_config import ProjectConfigManager
            config_manager = ProjectConfigManager(self.project_path)
            if not config_manager.exists:
                return False
            
            project_config = config_manager.load()
            
            # Check if content_only is enabled for JSON parsing
            self.logger.debug(f"🔍 Batch check for {file_path.name}: project_config type = {type(project_config)}")
            if hasattr(project_config, 'indexing') and project_config.indexing:
                if hasattr(project_config.indexing, 'parser_config') and project_config.indexing.parser_config:
                    json_parser_config = project_config.indexing.parser_config.get('json', None)
                    self.logger.debug(f"🔍 Found JSON config: {json_parser_config}")
                    if json_parser_config and getattr(json_parser_config, 'content_only', False):
                        self.logger.debug(f"✅ Batch processing enabled for {file_path.name}")
                        return True
            
            self.logger.debug(f"❌ Batch processing disabled for {file_path.name}")
            return False
            
        except Exception as e:
            self.logger.debug(f"❌ Batch processing check failed for {file_path.name}: {e}")
            return False
    
    def _inject_parser_configs(self):
        """Inject project-specific parser configurations."""
        for parser in self.parser_registry._parsers:
            parser_name = parser.__class__.__name__.lower().replace('parser', '')
            parser_config = self.config_loader.get_parser_config(parser_name)
            if parser_config and hasattr(parser, 'update_config'):
                parser.update_config(parser_config)
        
    def _get_state_directory(self) -> Path:
        """Get state directory (configurable for test isolation)."""
        # Use configured state directory if provided (for tests)
        if self.config.state_directory is not None:
            state_dir = self.config.state_directory
        else:
            # Default to project-local state directory
            state_dir = self.project_path / '.claude-indexer'
        
        state_dir.mkdir(parents=True, exist_ok=True)
        return state_dir
    
    def _get_state_file(self, collection_name: str) -> Path:
        """Get collection-specific state file with simple naming."""
        # Simple, predictable naming: just use collection name
        filename = f"{collection_name}.json"
        new_state_file = self._get_state_directory() / filename
        
        # Auto-migrate from global state directory if exists
        if not new_state_file.exists():
            old_global_state_file = Path.home() / '.claude-indexer' / 'state' / filename
            if old_global_state_file.exists():
                try:
                    # Copy state file content to new location
                    with open(old_global_state_file, 'r') as old_f:
                        state_data = old_f.read()
                    with open(new_state_file, 'w') as new_f:
                        new_f.write(state_data)
                    
                    # Remove old state file
                    old_global_state_file.unlink()
                    self.logger.info(f"Migrated state file: {old_global_state_file} -> {new_state_file}")
                except Exception as e:
                    self.logger.warning(f"Failed to migrate state file {old_global_state_file}: {e}")
        
        return new_state_file
    
    @property
    def state_file(self) -> Path:
        """Default state file for backward compatibility with tests."""
        return self._get_state_file("default")
    
    def index_project(self, collection_name: str, include_tests: bool = False, verbose: bool = False) -> IndexingResult:
        """Index an entire project with automatic incremental detection."""
        start_time = time.time()
        
        # Auto-detect incremental mode based on state file existence (like watcher pattern)
        state_file = self._get_state_file(collection_name)
        incremental = state_file.exists()
        
        if verbose:
            logger.info(f"🔍 === INDEXING MODE DETECTION ===")
            logger.info(f"   Collection: {collection_name}")
            logger.info(f"   State file exists: {incremental}")
            logger.info(f"   Mode: {'INCREMENTAL' if incremental else 'FULL'}")
            logger.info(f"   Orphan cleanup will run: YES (both modes)")
        
        result = IndexingResult(success=True, operation="incremental" if incremental else "full")
        
        try:
            # Find files to process
            if incremental:
                files_to_process, deleted_files = self._find_changed_files(include_tests, collection_name)
                
                # Handle deleted files using consolidated function
                if deleted_files:
                    self._handle_deleted_files(collection_name, deleted_files, verbose)
                    # State cleanup happens automatically in _update_state when no files_to_process
                    result.warnings.append(f"Handled {len(deleted_files)} deleted files")
            else:
                files_to_process = self._find_all_files(include_tests)
                deleted_files = []
            
            if not files_to_process:
                # Even if no files to process, update state to remove deleted files
                if incremental and deleted_files:
                    # Use incremental mode to preserve existing files while removing deleted ones
                    self._update_state([], collection_name, verbose, full_rebuild=False, deleted_files=deleted_files)
                result.warnings.append("No files to process")
                result.processing_time = time.time() - start_time
                return result
            
            self.logger.info(f"Found {len(files_to_process)} files to process")
            
            # Process files in batches with progressive disclosure support
            batch_size = self.config.batch_size
            all_entities = []
            all_relations = []
            all_implementation_chunks = []
            
            for i in range(0, len(files_to_process), batch_size):
                batch = files_to_process[i:i + batch_size]
                batch_num = (i // batch_size) + 1
                total_batches = (len(files_to_process) + batch_size - 1) // batch_size
                self.logger.info(f"Processing batch {batch_num}/{total_batches} ({len(batch)} files)")
                
                batch_entities, batch_relations, batch_implementation_chunks, batch_errors = self._process_file_batch(batch, collection_name, verbose)
                
                all_entities.extend(batch_entities)
                all_relations.extend(batch_relations)
                all_implementation_chunks.extend(batch_implementation_chunks)
                result.errors.extend(batch_errors)
                
                # Track failed files properly
                failed_files_in_batch = [str(f) for f in batch if str(f) in batch_errors]
                result.failed_files.extend(failed_files_in_batch)
                
                # Print specific file errors for debugging
                for error_msg in batch_errors:
                    for file_path in batch:
                        if str(file_path) in error_msg:
                            logger.error(f"❌ Error processing file: {file_path} - {error_msg}")
                            break
                
                # Update metrics
                result.files_processed += len([f for f in batch if str(f) not in batch_errors])
                result.files_failed += len(batch_errors)
            
            # Apply in-memory orphan filtering before storage to avoid wasted embeddings
            if all_relations:
                # Get global entity names for filtering
                global_entity_names = self._get_all_entity_names(collection_name)
                
                # CRITICAL: Add entities from current batch to avoid filtering legitimate relations
                current_batch_entity_names = {entity.name for entity in all_entities}
                combined_entity_names = global_entity_names | current_batch_entity_names
                
                if combined_entity_names:
                    original_count = len(all_relations)
                    all_relations = self._filter_orphan_relations_in_memory(all_relations, combined_entity_names)
                    filtered_count = original_count - len(all_relations)
                    self.logger.info(f"🧹 Pre-storage filtering: {filtered_count} orphan relations removed, {len(all_relations)} valid relations kept")
                    self.logger.debug(f"   Entity awareness: {len(global_entity_names)} from DB + {len(current_batch_entity_names)} from current batch = {len(combined_entity_names)} total")
                else:
                    self.logger.warning("⚠️ No entities available for filtering - proceeding without pre-filtering")
            
            # Store vectors using direct Qdrant automation with progressive disclosure
            if all_entities or all_relations or all_implementation_chunks:
                # Git+Meta: compute changed entity IDs for event awareness
                changed_entity_ids = {f"{entity.file_path}::{entity.name}" if entity.file_path else entity.name for entity in all_entities} if all_entities else set()
                # Use direct Qdrant automation via existing _store_vectors method
                storage_success = self._store_vectors(collection_name, all_entities, all_relations, all_implementation_chunks, changed_entity_ids)
                if not storage_success:
                    result.success = False
                    result.errors.append("Failed to store vectors in Qdrant")
                else:
                    result.entities_created = len(all_entities)
                    result.relations_created = len(all_relations)
                    result.implementation_chunks_created = len(all_implementation_chunks)
            
            # Update state file - merge successfully processed files with existing state
            successfully_processed = [f for f in files_to_process if str(f) not in result.failed_files]
            if successfully_processed:
                self._update_state(successfully_processed, collection_name, verbose, deleted_files=deleted_files if incremental else None)
                # Store processed files in result for test verification
                result.processed_files = [str(f) for f in successfully_processed]
                
                # Clean up orphaned relations after processing files (both full and incremental modes)
                if successfully_processed:
                    logger.info(f"🧹 === ORPHAN CLEANUP TRIGGERED ===")
                    logger.info(f"   Mode: {'INCREMENTAL' if incremental else 'FULL'}")
                    logger.info(f"   Files processed: {len(successfully_processed)}")
                    logger.info(f"   Starting orphan cleanup...")
                    orphaned_deleted = self.vector_store._cleanup_orphaned_relations(collection_name, verbose)
                    if orphaned_deleted > 0:
                        logger.info(f"✅ Cleanup complete: {orphaned_deleted} orphaned relations removed")
                    else:
                        logger.info("✅ No orphaned relations found")
                else:
                    logger.info(f"🚫 === ORPHAN CLEANUP SKIPPED ===")
                    logger.info(f"   Incremental: {incremental}")
                    logger.info(f"   Successfully processed: {len(successfully_processed) if successfully_processed else 0}")
                    logger.info(f"   Reason: No files processed")
            elif verbose:
                logger.warning(f"⚠️  No files to save state for (all {len(files_to_process)} files failed)")
            
            # Transfer cost data to result
            if hasattr(self, '_session_cost_data'):
                result.total_tokens = self._session_cost_data.get('tokens', 0)
                result.total_cost_estimate = self._session_cost_data.get('cost', 0.0)
                result.embedding_requests = self._session_cost_data.get('requests', 0)
                # Reset for next operation
                self._session_cost_data = {'tokens': 0, 'cost': 0.0, 'requests': 0}
            
        except Exception as e:
            result.success = False
            result.errors.append(f"Indexing failed: {e}")
        
        result.processing_time = time.time() - start_time
        return result
    
    def index_single_file(self, file_path: Path, collection_name: str) -> IndexingResult:
        """Index a single file with Git+Meta deduplication."""
        start_time = time.time()
        result = IndexingResult(success=True, operation="single_file")
        
        try:
            # Parse file first to get entities for Git+Meta deduplication check
            batch_callback = None
            if self._should_use_batch_processing(file_path):
                batch_callback = self._create_batch_callback(collection_name)
                self.logger.info(f"🚀 Enabling batch processing for large file: {file_path.name}")
            
            # Get global entity names for relation validation (same as batch processing)
            if not hasattr(self, '_cached_global_entities'):
                self._cached_global_entities = self._get_all_entity_names(collection_name)
                if self._cached_global_entities:
                    self.logger.debug(f"🌐 Cached {len(self._cached_global_entities)} global entities for cross-file relation filtering")
            
            parse_result = self.parser_registry.parse_file(file_path, batch_callback, global_entity_names=self._cached_global_entities)
            
            if not parse_result.success:
                result.success = False
                result.files_failed = 1
                result.errors.extend(parse_result.errors)
                return result
            
            # Git+Meta: Check if file content has changed by comparing entity hashes
            # Only proceed with cleanup if content actually changed
            should_cleanup = True
            if parse_result.entities and self.vector_store.collection_exists(collection_name):
                # Check if all entities have unchanged content
                unchanged_entities = 0
                for entity in parse_result.entities:
                    metadata_chunk = EntityChunk.create_metadata_chunk(entity, False)
                    content_hash = metadata_chunk.to_vector_payload()["content_hash"]
                    if self.vector_store.check_content_exists(collection_name, content_hash):
                        unchanged_entities += 1
                
                # If all entities are unchanged, skip cleanup and storage
                if unchanged_entities == len(parse_result.entities):
                    should_cleanup = False
                    logger.info(f"⚡ Git+Meta: All {unchanged_entities} entities unchanged, skipping cleanup and storage")
                    
                    # Return success with zero operations
                    result.files_processed = 1
                    result.entities_created = 0
                    result.relations_created = 0
                    result.implementation_chunks_created = 0
                    result.processed_files = [str(file_path)]
                    result.total_tokens = 0
                    result.total_cost_estimate = 0.0
                    result.embedding_requests = 0
                    result.processing_time = time.time() - start_time
                    return result
            
            # Clean up existing entities for this file only if content changed
            if should_cleanup:
                try:
                    relative_path = str(file_path.relative_to(self.project_path))
                    logger.info(f"🧹 DEBUG: Single file cleanup starting for: {relative_path}")
                    
                    # Check if collection exists first
                    collection_exists = self.vector_store.collection_exists(collection_name)
                    logger.info(f"🧹 DEBUG: Collection '{collection_name}' exists: {collection_exists}")
                    
                    if collection_exists:
                        # Try to find entities before deletion
                        full_path = str(file_path)
                        logger.info(f"🧹 DEBUG: Searching for entities with path: {full_path}")
                        found_entities = self.vector_store.find_entities_for_file(collection_name, full_path)
                        logger.info(f"🧹 DEBUG: Found {len(found_entities)} entities before cleanup")
                        
                        # Run cleanup
                        self._handle_deleted_files(collection_name, relative_path, verbose=True)
                        
                        # Check after cleanup
                        found_entities_after = self.vector_store.find_entities_for_file(collection_name, full_path)
                        logger.info(f"🧹 DEBUG: Found {len(found_entities_after)} entities after cleanup")
                    else:
                        logger.info(f"🧹 DEBUG: Collection doesn't exist, skipping cleanup")
                        
                except Exception as e:
                    logger.warning(f"⚠️ Failed to clean existing entities for {file_path}: {e}")
                    import traceback
                    logger.warning(f"⚠️ Traceback: {traceback.format_exc()}")
            
            # Handle storage based on whether batch processing was used
            if batch_callback:
                # Batch processing was used - data already stored via callback
                storage_success = True
                result.files_processed = 1
                result.entities_created = getattr(parse_result, 'entities_created', 0)
                result.relations_created = 0  # Relations not used in content_only mode
                result.implementation_chunks_created = getattr(parse_result, 'implementation_chunks_created', 0)
                result.processed_files = [str(file_path)]
                self.logger.info(f"✅ Streaming batch processing completed for {file_path.name}")
            else:
                # Git+Meta: compute changed entity IDs for event awareness
                changed_entity_ids = {f"{entity.file_path}::{entity.name}" if entity.file_path else entity.name for entity in parse_result.entities} if parse_result.entities else set()
                # Traditional processing - store accumulated results
                storage_success = self._store_vectors(collection_name, parse_result.entities, parse_result.relations, parse_result.implementation_chunks, changed_entity_ids)
                
                if storage_success:
                    result.files_processed = 1
                    result.entities_created = len(parse_result.entities)
                    result.relations_created = len(parse_result.relations)
                    result.implementation_chunks_created = len(parse_result.implementation_chunks)
                    result.processed_files = [str(file_path)]
                else:
                    result.success = False
                    result.files_failed = 1
                    result.errors.append("Failed to store vectors")
            
        except Exception as e:
            result.success = False
            result.files_failed = 1
            result.errors.append(f"Failed to index {file_path}: {e}")
        
        # Transfer cost data to result
        if hasattr(self, '_session_cost_data'):
            result.total_tokens = self._session_cost_data.get('tokens', 0)
            result.total_cost_estimate = self._session_cost_data.get('cost', 0.0)
            result.embedding_requests = self._session_cost_data.get('requests', 0)
            # Reset for next operation
            self._session_cost_data = {'tokens': 0, 'cost': 0.0, 'requests': 0}
        
        result.processing_time = time.time() - start_time
        return result
    
    def search_similar(self, collection_name: str, query: str, 
                      limit: int = 10, filter_type: str = None, 
                      chunk_type: str = None) -> List[Dict[str, Any]]:
        """Search for similar entities/relations.
        
        Args:
            collection_name: Name of the collection to search
            query: Search query text
            limit: Maximum number of results
            filter_type: Filter by entity type (e.g., 'function', 'class')
            chunk_type: Filter by chunk type (e.g., 'implementation', 'metadata')
        """
        try:
            # Check if collection exists before searching
            if not self.vector_store.collection_exists(collection_name):
                logger.warning(f"Collection '{collection_name}' does not exist")
                return []
            
            # Generate query embedding
            embedding_result = self.embedder.embed_text(query)
            if not embedding_result.success:
                return []
            
            # Build filter
            filter_conditions = {}
            if filter_type:
                filter_conditions["type"] = filter_type
            if chunk_type:
                filter_conditions["chunk_type"] = chunk_type
            
            # Search vector store
            search_result = self.vector_store.search_similar(
                collection_name=collection_name,
                query_vector=embedding_result.embedding,
                limit=limit,
                filter_conditions=filter_conditions
            )
            
            return search_result.results if search_result.success else []
            
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []
    
    def clear_collection(self, collection_name: str, preserve_manual: bool = True) -> bool:
        """Clear collection data.
        
        Args:
            collection_name: Name of the collection
            preserve_manual: If True (default), preserve manually-added memories
        """
        try:
            # Clear vector store
            result = self.vector_store.clear_collection(collection_name, preserve_manual=preserve_manual)
            
            # Clear state file (only tracks code-indexed files)
            state_file = self._get_state_file(collection_name)
            if state_file.exists():
                state_file.unlink()
            
            return result.success
            
        except Exception as e:
            logger.error(f"Failed to clear collection: {e}")
            return False
    
    
    
    def _find_all_files(self, include_tests: bool = False) -> List[Path]:
        """Find all files matching project patterns."""
        files = set()  # Use set to prevent duplicates
        
        # Use project-specific patterns
        include_patterns = self.config.include_patterns
        exclude_patterns = self.config.exclude_patterns
        
        # No fallback patterns - use what's configured
        if not include_patterns:
            raise ValueError("No include patterns configured")
        
        # Find files matching include patterns
        for pattern in include_patterns:
            # Handle patterns that already include ** vs those that don't
            if pattern.startswith('**/'):
                glob_pattern = pattern
            else:
                glob_pattern = f"**/{pattern}"
            
            found = list(self.project_path.glob(glob_pattern))
            files.update(found)  # Use update instead of extend to prevent duplicates
        
        # Filter files
        filtered_files = []
        for file_path in files:
            # Skip files matching exclude patterns
            relative_path = file_path.relative_to(self.project_path)
            if any(relative_path.match(pattern) for pattern in exclude_patterns):
                continue
            
            # Skip files in excluded directories
            path_str = str(relative_path)
            if any(excluded in path_str for excluded in exclude_patterns):
                continue
            
            # Check file size
            if file_path.stat().st_size > self.config.max_file_size:
                continue
            
            filtered_files.append(file_path)
        
        return list(filtered_files)  # Convert back to list for return type consistency
    
    def _get_files_needing_processing(self, include_tests: bool = False, collection_name: str = None) -> List[Path]:
        """Get files that need processing for incremental indexing."""
        return self._find_changed_files(include_tests, collection_name)[0]
    
    def _find_changed_files(self, include_tests: bool = False, collection_name: str = None) -> Tuple[List[Path], List[str]]:
        """Find files that have changed since last indexing."""
        current_files = self._find_all_files(include_tests)
        current_state = self._get_current_state(current_files)
        previous_state = self._load_state(collection_name)
        
        changed_files = []
        deleted_files = []
        
        # Find new and modified files
        for file_path in current_files:
            file_key = str(file_path.relative_to(self.project_path))
            current_hash = current_state.get(file_key, {}).get("hash", "")
            previous_hash = previous_state.get(file_key, {}).get("hash", "")
            
            if current_hash != previous_hash:
                changed_files.append(file_path)
        
        # Find deleted files
        current_keys = set(current_state.keys())
        previous_keys = set(previous_state.keys())
        deleted_keys = previous_keys - current_keys
        deleted_files.extend(deleted_keys)
        
        return changed_files, deleted_files
    
    def _categorize_file_changes(self, include_tests: bool = False, collection_name: str = None) -> Tuple[List[Path], List[Path], List[str]]:
        """Categorize files into new, modified, and deleted."""
        current_files = self._find_all_files(include_tests)
        current_state = self._get_current_state(current_files)
        previous_state = self._load_state(collection_name)
        
        new_files = []
        modified_files = []
        deleted_files = []
        
        # Categorize changed files
        for file_path in current_files:
            file_key = str(file_path.relative_to(self.project_path))
            current_hash = current_state.get(file_key, {}).get("hash", "")
            previous_hash = previous_state.get(file_key, {}).get("hash", "")
            
            if current_hash != previous_hash:
                if previous_hash == "":  # Not in previous state = new file
                    new_files.append(file_path)
                else:  # In previous state but different hash = modified file
                    modified_files.append(file_path)
        
        # Find deleted files
        current_keys = set(current_state.keys())
        previous_keys = set(k for k in previous_state.keys() if not k.startswith('_'))  # Exclude metadata
        deleted_keys = previous_keys - current_keys
        deleted_files.extend(deleted_keys)
        
        return new_files, modified_files, deleted_files
    
    def _get_vectored_files(self, collection_name: str) -> Set[str]:
        """Get set of files that currently have entities in the vector database."""
        try:
            from qdrant_client.http import models
            
            # Access the underlying QdrantStore client (bypass CachingVectorStore wrapper)
            if hasattr(self.vector_store, 'backend'):
                qdrant_client = self.vector_store.backend.client
            else:
                qdrant_client = self.vector_store.client
            
            # Scroll through all points to get file paths
            file_paths = set()
            scroll_result = qdrant_client.scroll(
                collection_name=collection_name,
                limit=10000,  # Large batch size
                with_payload=True,
                with_vectors=False
            )
            
            points = scroll_result[0]  # First element is the points list
            next_page_offset = scroll_result[1]  # Second element is next page offset
            
            # Process first batch
            for point in points:
                payload = point.payload if hasattr(point, 'payload') else {}
                file_path = payload.get('file_path')
                if file_path:
                    # Convert to relative path for consistency
                    try:
                        rel_path = str(Path(file_path).relative_to(self.project_path))
                        file_paths.add(rel_path)
                    except ValueError:
                        # If relative_to fails, use the file_path as-is
                        file_paths.add(file_path)
            
            # Handle pagination if there are more points
            while next_page_offset is not None:
                scroll_result = qdrant_client.scroll(
                    collection_name=collection_name,
                    offset=next_page_offset,
                    limit=10000,
                    with_payload=True,
                    with_vectors=False
                )
                
                points = scroll_result[0]
                next_page_offset = scroll_result[1]
                
                for point in points:
                    payload = point.payload if hasattr(point, 'payload') else {}
                    file_path = payload.get('file_path')
                    if file_path:
                        try:
                            rel_path = str(Path(file_path).relative_to(self.project_path))
                            file_paths.add(rel_path)
                        except ValueError:
                            file_paths.add(file_path)
            
            return file_paths
        except Exception as e:
            logger.warning(f"Failed to get vectored files: {e}")
            return set()
    
    def _categorize_vectored_file_changes(self, collection_name: str, before_vectored_files: Set[str] = None, processed_files: Set[str] = None) -> Tuple[List[str], List[str], List[str]]:
        """Categorize vectored files (files with entities in database) into new, modified, and deleted."""
        current_vectored_files = self._get_vectored_files(collection_name)
        
        if before_vectored_files is None:
            # If no before state provided, we can't determine what changed
            # Return empty lists instead of showing all files as modified
            return [], [], []
        
        new_vectored = list(current_vectored_files - before_vectored_files)
        deleted_vectored = list(before_vectored_files - current_vectored_files)
        
        # Only show files as modified if they were actually processed AND existed before
        # This prevents showing all existing files as "modified"
        if processed_files:
            # Convert processed_files paths to relative paths for comparison
            processed_relative = set()
            for file_path in processed_files:
                try:
                    if isinstance(file_path, Path):
                        rel_path = str(file_path.relative_to(self.project_path))
                    else:
                        # Assume it's already a relative path string
                        rel_path = str(file_path)
                    processed_relative.add(rel_path)
                except (ValueError, AttributeError):
                    # If relative_to fails or file_path is not Path-like
                    processed_relative.add(str(file_path))
            
            # Modified files are those that were processed AND existed in database before
            modified_vectored = list(processed_relative & before_vectored_files & current_vectored_files)
        else:
            # No processed files info - can't determine modified files accurately
            modified_vectored = []
        
        return new_vectored, modified_vectored, deleted_vectored
    
    def _filter_orphan_relations_in_memory(self, relations: List['Relation'], global_entity_names: set) -> List['Relation']:
        """Filter out relations pointing to non-existent entities - handles CALLS and IMPORTS."""
        if not global_entity_names:
            self.logger.warning("No global entities available - keeping all relations")
            return relations
            
        def resolve_module_name(module_name: str) -> bool:
            """Check if module name resolves to any existing entity."""
            if module_name in global_entity_names:
                return True
            
            # Handle relative imports (.chat.parser, ..config, etc.)
            if module_name.startswith('.'):
                clean_name = module_name.lstrip('.')
                for entity_name in global_entity_names:
                    # Direct pattern match first
                    if entity_name.endswith(f"/{clean_name}.py") or entity_name.endswith(f"\\{clean_name}.py"):
                        return True
                    # Handle dot notation (chat.parser -> chat/parser.py)
                    if '.' in clean_name:
                        path_version = clean_name.replace('.', '/')
                        if entity_name.endswith(f"/{path_version}.py") or entity_name.endswith(f"\\{path_version}.py"):
                            return True
                    # Fallback: contains check
                    if clean_name in entity_name and entity_name.endswith('.py'):
                        return True
            
            # Handle absolute module paths (claude_indexer.analysis.entities)
            elif '.' in module_name:
                path_parts = module_name.split('.')
                for entity_name in global_entity_names:
                    # Check if entity path contains module structure and ends with .py
                    if (all(part in entity_name for part in path_parts) and 
                        entity_name.endswith('.py') and path_parts[-1] in entity_name):
                        return True
            
            return False
            
        valid_relations = []
        orphan_count = 0
        import_orphan_count = 0
        
        for relation in relations:
            # For CALLS relations, check if target entity exists
            if relation.relation_type.value == 'calls':
                if relation.to_entity in global_entity_names:
                    valid_relations.append(relation)
                    self.logger.debug(f"✅ Kept relation: {relation.from_entity} -> {relation.to_entity}")
                else:
                    orphan_count += 1
                    self.logger.debug(f"🚫 Filtered orphan: {relation.from_entity} -> {relation.to_entity}")
            
            # For IMPORTS relations, use module resolution logic
            elif relation.relation_type.value == 'imports':
                if resolve_module_name(relation.to_entity):
                    valid_relations.append(relation)
                    self.logger.debug(f"✅ Kept import: {relation.to_entity}")
                else:
                    import_orphan_count += 1
                    self.logger.debug(f"🚫 Filtered external import: {relation.to_entity}")
            else:
                # Keep all other relations (contains, inherits, etc.)
                valid_relations.append(relation)
        
        self.logger.info(f"Filtered {orphan_count} orphan CALLS relations, {import_orphan_count} external imports")
        return valid_relations
    
    def _get_all_entity_names(self, collection_name: str) -> set:
        """Get all entity names from vector store for global entity awareness."""
        try:
            # Get Qdrant client from vector store
            if hasattr(self.vector_store, 'backend') and hasattr(self.vector_store.backend, 'client'):
                qdrant_client = self.vector_store.backend.client
            elif hasattr(self.vector_store, 'client'):
                qdrant_client = self.vector_store.client
            else:
                self.logger.warning("Cannot access Qdrant client for global entity awareness")
                return set()
            
            entity_names = set()
            next_page_offset = None
            
            # Scroll through all entity points (not relations) to get entity names
            while True:
                from qdrant_client.models import Filter, FieldCondition, MatchValue
                
                scroll_result = qdrant_client.scroll(
                    collection_name=collection_name,
                    offset=next_page_offset,
                    limit=1000,
                    with_payload=True,
                    with_vectors=False,
                    scroll_filter=Filter(
                        must_not=[
                            FieldCondition(key="type", match=MatchValue(value="relation"))
                        ]
                    )
                )
                
                points, next_page_offset = scroll_result
                
                if not points:
                    break
                
                # Extract entity names from payloads (both chunks and entities)
                for point in points:
                    payload = point.payload
                    if payload:
                        # Try entity_name first (chunks), then name (legacy entities)
                        entity_name = payload.get("entity_name") or payload.get("name")
                        if entity_name:
                            entity_names.add(entity_name)
                
                # Break if no more pages
                if next_page_offset is None:
                    break
            
            self.logger.debug(f"🌐 Retrieved {len(entity_names)} global entity names for entity-aware filtering")
            return entity_names
            
        except Exception as e:
            self.logger.warning(f"Failed to get global entities: {e}")
            return set()
    
    def _process_file_batch(self, files: List[Path], collection_name: str, verbose: bool = False) -> Tuple[List[Entity], List[Relation], List[EntityChunk], List[str]]:
        """Process a batch of files with progressive disclosure support."""
        all_entities = []
        all_relations = []
        all_implementation_chunks = []
        errors = []
        
        for file_path in files:
            try:
                relative_path = file_path.relative_to(self.project_path)
                
                # Determine file status using existing changed files logic
                current_state = self._get_current_state([file_path])
                previous_state = self._load_state(collection_name)
                
                file_key = str(relative_path)
                if file_key not in previous_state:
                    file_status = "ADDED"
                else:
                    current_hash = current_state.get(file_key, {}).get("hash", "")
                    previous_hash = previous_state.get(file_key, {}).get("hash", "")
                    file_status = "MODIFIED" if current_hash != previous_hash else "UNCHANGED"
                
                self.logger.debug(f"Processing file [{file_status}]: {relative_path}")
                
                # Check for batch processing (streaming for large JSON files)
                batch_callback = None
                if self._should_use_batch_processing(file_path):
                    batch_callback = self._create_batch_callback(collection_name)
                    self.logger.info(f"🚀 Enabling batch processing for large file: {file_path.name}")
                
                # Get global entity names for entity-aware filtering (once per batch, cached)
                if not hasattr(self, '_cached_global_entities'):
                    self._cached_global_entities = self._get_all_entity_names(collection_name)
                    if self._cached_global_entities:
                        self.logger.debug(f"🌐 Cached {len(self._cached_global_entities)} global entities for cross-file relation filtering")
                
                result = self.parser_registry.parse_file(file_path, batch_callback, global_entity_names=self._cached_global_entities)
                
                if result.success:
                    all_entities.extend(result.entities)
                    all_relations.extend(result.relations)
                    all_implementation_chunks.extend(result.implementation_chunks)
                    self.logger.debug(f"  Found {len(result.entities)} entities, {len(result.relations)} relations, {len(result.implementation_chunks)} implementation chunks")
                else:
                    error_msg = f"Failed to parse {relative_path}"
                    errors.append(error_msg)
                    self.logger.warning(f"  {error_msg}")
                    logger.error(f"❌ Parse error in {file_path}: Parse failure")
                    
            except Exception as e:
                error_msg = f"Error processing {file_path}: {e}"
                errors.append(error_msg)
                self.logger.error(f"  {error_msg}")
                logger.error(f"❌ Processing error in {file_path}: {e}")
        
        return all_entities, all_relations, all_implementation_chunks, errors
    
    def _store_vectors(self, collection_name: str, entities: List[Entity], 
                      relations: List[Relation], implementation_chunks: List[EntityChunk] = None,
                      changed_entity_ids: set = None) -> bool:
        """Store entities, relations, and implementation chunks with Git+Meta content deduplication."""
        if implementation_chunks is None:
            implementation_chunks = []
        if changed_entity_ids is None:
            changed_entity_ids = set()
        
        logger = self.logger if hasattr(self, 'logger') else None
        if logger:
            logger.debug(f"🔄 Starting Git+Meta storage: {len(entities)} entities, {len(relations)} relations, {len(implementation_chunks)} chunks")
        
        try:
            all_points = []
            total_tokens = 0
            total_cost = 0.0
            total_requests = 0
            
            # Phase 1: Content hash analysis for entities (Git+Meta approach)
            entity_hashes = {}
            chunks_to_embed = []
            chunks_to_skip = []
            
            # Create implementation chunk lookup for has_implementation flags
            implementation_file_paths = set()
            if implementation_chunks:
                implementation_file_paths = {chunk.metadata.get('file_path') for chunk in implementation_chunks if chunk.metadata.get('file_path')}
            
            # Check which entities need embedding (Git+Meta deduplication)
            if entities:
                if logger:
                    logger.debug(f"🧠 Processing entities with Git+Meta deduplication: {len(entities)} items")
                
                for entity in entities:
                    has_implementation = str(entity.file_path) in implementation_file_paths
                    metadata_chunk = EntityChunk.create_metadata_chunk(entity, has_implementation)
                    content_hash = metadata_chunk.to_vector_payload()["content_hash"]
                    entity_id = f"{entity.file_path}::{entity.name}" if entity.file_path else entity.name
                    entity_hashes[entity_id] = content_hash
                    
                    if self.vector_store.check_content_exists(collection_name, content_hash):
                        chunks_to_skip.append((metadata_chunk, "exists"))
                        if logger:
                            logger.debug(f"⚡ Skipping unchanged entity: {entity.name}")
                    else:
                        chunks_to_embed.append(metadata_chunk)
                        changed_entity_ids.add(entity_id)
                        if logger:
                            pass  # logger.debug(f"🔄 Processing changed entity: {entity.name}")
                
                # Log efficiency gains
                if chunks_to_skip:
                    logger.info(f"⚡ Git+Meta Efficiency: Skipped {len(chunks_to_skip)} unchanged entities (saved {len(chunks_to_skip)} embeddings)")
                
                # Phase 2: Embed only new/changed content
                if chunks_to_embed:
                    metadata_texts = [chunk.content for chunk in chunks_to_embed]
                    if logger:
                        logger.debug(f"🔤 Generating embeddings for {len(metadata_texts)} new/changed entity texts")
                    
                    embedding_results = self.embedder.embed_batch(metadata_texts)
                    if logger:
                        logger.debug(f"✅ Entity embeddings completed: {sum(1 for r in embedding_results if r.success)}/{len(embedding_results)} successful")
                    
                    # Process embedding results for new/changed entities only
                    cost_data = self._collect_embedding_cost_data(embedding_results)
                    total_tokens += cost_data['tokens']
                    total_cost += cost_data['cost']
                    total_requests += cost_data['requests']
                    
                    failed_entities = 0
                    for chunk, embedding_result in zip(chunks_to_embed, embedding_results):
                        if embedding_result.success:
                            point = self.vector_store.create_chunk_point(
                                chunk, embedding_result.embedding, collection_name
                            )
                            all_points.append(point)
                        else:
                            failed_entities += 1
                            if logger:
                                logger.warning(f"❌ Entity embedding failed: {chunk.entity_name} - {getattr(embedding_result, 'error', 'Unknown error')}")
                    
                    if failed_entities > 0 and logger:
                        logger.warning(f"⚠️ {failed_entities} entity embeddings failed and were skipped")
            
            # Phase 3: Smart Relations Processing (Git+Meta approach)
            if relations:
                if logger:
                    logger.debug(f"🔗 Processing relations with Git+Meta smart filtering: {len(relations)} items")
                
                # Import SmartRelationsProcessor
                from .storage.diff_layers import SmartRelationsProcessor
                relations_processor = SmartRelationsProcessor()
                
                # Split relations based on entity involvement
                if changed_entity_ids:
                    relations_to_embed, relations_unchanged = relations_processor.filter_relations_for_changes(
                        relations, changed_entity_ids
                    )
                    
                    if relations_unchanged:
                        logger.info(f"⚡ Smart Relations: Skipped {len(relations_unchanged)} unchanged relations (saved {len(relations_unchanged)} embeddings)")
                    
                    relations_to_process = relations_to_embed
                else:
                    # Fallback: process all relations (initial indexing)
                    relations_to_process = relations
                
                if relations_to_process:
                    # Deduplicate relations BEFORE embedding to save API costs
                    seen_relation_keys = set()
                    unique_relations = []
                    duplicate_count = 0
                    duplicate_details = {}
                    
                    logger.debug(f"🔍 === RELATION DEDUPLICATION ===")
                    logger.debug(f"   Total relations to process: {len(relations_to_process)}")
                    
                    for relation in relations_to_process:
                        # Generate the same key that will be used for storage
                        relation_chunk = RelationChunk.from_relation(relation)
                        relation_key = relation_chunk.id
                        import_type = relation.metadata.get('import_type', 'none') if relation.metadata else 'none'
                        
                        if relation_key not in seen_relation_keys:
                            seen_relation_keys.add(relation_key)
                            unique_relations.append(relation)
                            if logger and len(unique_relations) <= 5:
                                logger.debug(f"   ✅ Unique: {relation_key} [import_type: {import_type}]")
                        else:
                            duplicate_count += 1
                            if import_type not in duplicate_details:
                                duplicate_details[import_type] = 0
                            duplicate_details[import_type] += 1
                            if logger and duplicate_count <= 5:
                                logger.debug(f"   ❌ Duplicate: {relation_key} [import_type: {import_type}]")
                    
                    logger.debug(f"   Unique relations: {len(unique_relations)}")
                    logger.debug(f"   Duplicates removed: {duplicate_count}")
                    if duplicate_details:
                        logger.debug(f"   Duplicates by type: {duplicate_details}")
                        
                    relation_texts = [self._relation_to_text(relation) for relation in unique_relations]
                    if logger:
                        logger.debug(f"🔤 Generating embeddings for {len(relation_texts)} unique relation texts")
                        
                    embedding_results = self.embedder.embed_batch(relation_texts)
                    if logger:
                        logger.debug(f"✅ Relation embeddings completed: {sum(1 for r in embedding_results if r.success)}/{len(embedding_results)} successful")
                    
                    # Process embedding results for relations
                    cost_data = self._collect_embedding_cost_data(embedding_results)
                    total_tokens += cost_data['tokens']
                    total_cost += cost_data['cost']
                    total_requests += cost_data['requests']
                    
                    failed_relations = 0
                    for relation, embedding_result in zip(unique_relations, embedding_results):
                        if embedding_result.success:
                            # Convert relation to chunk for v2.4 pure architecture
                            relation_chunk = RelationChunk.from_relation(relation)
                            point = self.vector_store.create_relation_chunk_point(
                                relation_chunk, embedding_result.embedding, collection_name
                            )
                            all_points.append(point)
                        else:
                            failed_relations += 1
                            if logger:
                                logger.warning(f"❌ Relation embedding failed: {relation.from_entity} -> {relation.to_entity} - {getattr(embedding_result, 'error', 'Unknown error')}")
                    
                    if failed_relations > 0 and logger:
                        logger.warning(f"⚠️ {failed_relations} relation embeddings failed and were skipped")
            
            # Phase 4: Implementation chunks with Git+Meta deduplication
            if implementation_chunks:
                if logger:
                    logger.debug(f"💻 Processing implementation chunks with Git+Meta deduplication: {len(implementation_chunks)} items")
                
                # Check which implementation chunks need embedding
                impl_chunks_to_embed = []
                impl_chunks_to_skip = []
                
                for impl_chunk in implementation_chunks:
                    content_hash = impl_chunk.to_vector_payload()["content_hash"]
                    
                    if self.vector_store.check_content_exists(collection_name, content_hash):
                        impl_chunks_to_skip.append(impl_chunk)
                        if logger:
                            logger.debug(f"⚡ Skipping unchanged implementation: {impl_chunk.entity_name}")
                    else:
                        impl_chunks_to_embed.append(impl_chunk)
                        if logger:
                            pass  # logger.debug(f"🔄 Processing changed implementation: {impl_chunk.entity_name}")
                
                # Log efficiency gains
                if impl_chunks_to_skip:
                    logger.info(f"⚡ Git+Meta Implementation: Skipped {len(impl_chunks_to_skip)} unchanged implementations (saved {len(impl_chunks_to_skip)} embeddings)")
                
                # Embed only new/changed implementation chunks
                if impl_chunks_to_embed:
                    implementation_texts = [chunk.content for chunk in impl_chunks_to_embed]
                    if logger:
                        logger.debug(f"🔤 Generating embeddings for {len(implementation_texts)} new/changed implementation texts")
                        
                    embedding_results = self.embedder.embed_batch(implementation_texts)
                    if logger:
                        logger.debug(f"✅ Implementation embeddings completed: {sum(1 for r in embedding_results if r.success)}/{len(embedding_results)} successful")
                    
                    # Process embedding results for implementation chunks
                    cost_data = self._collect_embedding_cost_data(embedding_results)
                    total_tokens += cost_data['tokens']
                    total_cost += cost_data['cost']
                    total_requests += cost_data['requests']
                    
                    failed_implementations = 0
                    for chunk, embedding_result in zip(impl_chunks_to_embed, embedding_results):
                        if embedding_result.success:
                            point = self.vector_store.create_chunk_point(
                                chunk, embedding_result.embedding, collection_name
                            )
                            all_points.append(point)
                        else:
                            failed_implementations += 1
                            if logger:
                                logger.warning(f"❌ Implementation embedding failed: {chunk.entity_name} - {getattr(embedding_result, 'error', 'Unknown error')}")
                    
                    if failed_implementations > 0 and logger:
                        logger.warning(f"⚠️ {failed_implementations} implementation embeddings failed and were skipped")
            
            # Store cost tracking data for result reporting
            if not hasattr(self, '_session_cost_data'):
                self._session_cost_data = {'tokens': 0, 'cost': 0.0, 'requests': 0}
            
            self._session_cost_data['tokens'] += total_tokens
            self._session_cost_data['cost'] += total_cost
            self._session_cost_data['requests'] += total_requests
            
            # Batch store all points
            if all_points:
                if logger:
                    logger.debug(f"💾 === FINAL STORAGE SUMMARY ===")
                    logger.debug(f"   Collection: {collection_name}")
                    logger.debug(f"   Total points to store: {len(all_points)}")
                    
                    # Count different types of points
                    entity_points = sum(1 for p in all_points if p.payload.get('chunk_type') == 'metadata' and p.payload.get('entity_type') != 'relation')
                    relation_points = sum(1 for p in all_points if p.payload.get('chunk_type') == 'relation')
                    impl_points = sum(1 for p in all_points if p.payload.get('chunk_type') == 'implementation')
                    
                    logger.debug(f"   - Entity metadata: {entity_points}")
                    logger.debug(f"   - Relations: {relation_points}")
                    logger.debug(f"   - Implementations: {impl_points}")
                    logger.debug(f"   API calls made: {total_requests}")
                    logger.debug(f"   Tokens used: {total_tokens:,}")
                
                result = self.vector_store.batch_upsert(collection_name, all_points)
                
                if logger:
                    if result.success:
                        logger.debug(f"✅ Successfully stored all {len(all_points)} points")
                    else:
                        logger.error(f"❌ Failed to store points: {getattr(result, 'errors', 'Unknown error')}")
                
                # Enhanced orphan cleanup after successful storage
                if result.success:
                    try:
                        from .storage.diff_layers import EnhancedOrphanCleanup
                        cleanup = EnhancedOrphanCleanup(self.vector_store.client)
                        orphaned_count = cleanup.cleanup_hash_orphaned_relations(collection_name)
                        if orphaned_count > 0 and logger:
                            logger.info(f"🧹 Cleaned {orphaned_count} orphaned relations after hash changes")
                    except Exception as cleanup_error:
                        if logger:
                            logger.warning(f"⚠️ Orphan cleanup failed but storage succeeded: {cleanup_error}")
                
                return result.success
            else:
                if logger:
                    logger.debug("ℹ️ No points to store")
                return True
            
        except Exception as e:
            logger.error(f"Error in _store_vectors: {e}")
            return False
    
    def _entity_to_text(self, entity: Entity) -> str:
        """Convert entity to text for embedding."""
        parts = [
            f"{entity.entity_type.value}: {entity.name}",
            " ".join(entity.observations)
        ]
        
        if entity.docstring:
            parts.append(f"Description: {entity.docstring}")
        
        if entity.signature:
            parts.append(f"Signature: {entity.signature}")
        
        return " | ".join(parts)
    
    def _relation_to_text(self, relation: Relation) -> str:
        """Convert relation to text for embedding."""
        text = f"Relation: {relation.from_entity} {relation.relation_type.value} {relation.to_entity}"
        
        if relation.context:
            text += f" | Context: {relation.context}"
        
        return text
    
    def _get_current_state(self, files: List[Path]) -> Dict[str, Dict[str, Any]]:
        """Get current state of files."""
        state = {}
        
        for file_path in files:
            try:
                relative_path = str(file_path.relative_to(self.project_path))
                file_hash = self._get_file_hash(file_path)
                
                state[relative_path] = {
                    "hash": file_hash,
                    "size": file_path.stat().st_size,
                    "mtime": file_path.stat().st_mtime
                }
            except Exception:
                continue
        
        return state
    
    def _get_file_hash(self, file_path: Path) -> str:
        """Get SHA256 hash of file contents."""
        try:
            with open(file_path, 'rb') as f:
                return hashlib.sha256(f.read()).hexdigest()
        except Exception:
            return ""
    
    def _load_state(self, collection_name: str) -> Dict[str, Dict[str, Any]]:
        """Load previous indexing state."""
        try:
            state_file = self._get_state_file(collection_name)
            if state_file.exists():
                with open(state_file) as f:
                    return json.load(f)
        except Exception:
            pass
        return {}
    
    def _load_previous_statistics(self, collection_name: str) -> Dict[str, int]:
        """Load previous run statistics from state file."""
        state = self._load_state(collection_name)
        return state.get('_statistics', {})
    
    def _save_statistics_to_state(self, collection_name: str, result: 'IndexingResult'):
        """Save current statistics to state file."""
        import time
        try:
            state = self._load_state(collection_name)
            state['_statistics'] = {
                'files_processed': result.files_processed,
                'entities_created': result.entities_created,
                'relations_created': result.relations_created,
                'implementation_chunks_created': result.implementation_chunks_created,
                'processing_time': result.processing_time,
                'timestamp': time.time()
            }
            
            # Save updated state
            state_file = self._get_state_file(collection_name)
            state_file.parent.mkdir(parents=True, exist_ok=True)
            
            temp_file = state_file.with_suffix('.tmp')
            with open(temp_file, 'w') as f:
                json.dump(state, f, indent=2)
            temp_file.rename(state_file)
            
        except Exception as e:
            logger.debug(f"Failed to save statistics to state: {e}")
    
    def _update_state(self, new_files: List[Path], collection_name: str, verbose: bool = False, full_rebuild: bool = False, deleted_files: List[str] = None):
        """Update state file by merging new files with existing state, or do full rebuild."""
        try:
            if full_rebuild:
                # Full rebuild: use only the new files as complete state
                final_state = self._get_current_state(new_files)
                operation_desc = "rebuilt"
                file_count_desc = f"{len(new_files)} files tracked"
            else:
                # Incremental update: merge new files with existing state
                existing_state = self._load_state(collection_name)
                new_state = self._get_current_state(new_files)
                final_state = existing_state.copy()
                final_state.update(new_state)
                operation_desc = "updated"
                file_count_desc = f"{len(new_files)} new files added, {len(final_state)} total files tracked"
            
            # Remove deleted files from final state
            if deleted_files:
                files_removed = 0
                for deleted_file in deleted_files:
                    logger.info(f"🗑️ DEBUG: About to check for deletion from JSON state: '{deleted_file}' (exists in state: {deleted_file in final_state})")
                    if deleted_file in final_state:
                        logger.info(f"🗑️ DEBUG: DELETING '{deleted_file}' from JSON state")
                        del final_state[deleted_file]
                        files_removed += 1
                        if verbose:
                            logger.debug(f"   Removed {deleted_file} from state")
                    else:
                        logger.info(f"⚠️ DEBUG: File '{deleted_file}' NOT FOUND in state for deletion")
                
                if files_removed > 0:
                    # Update description to reflect deletions
                    if operation_desc == "updated":
                        file_count_desc = f"{len(new_files)} new files added, {files_removed} files removed, {len(final_state)} total files tracked"
                    else:  # rebuilt
                        file_count_desc = f"{len(new_files)} files tracked, {files_removed} deleted files removed"
            
            # Save state atomically
            state_file = self._get_state_file(collection_name)
            state_file.parent.mkdir(parents=True, exist_ok=True)
            
            temp_file = state_file.with_suffix('.tmp')
            with open(temp_file, 'w') as f:
                json.dump(final_state, f, indent=2)
            
            # Atomic rename
            temp_file.rename(state_file)
            
            # Verify saved state
            with open(state_file) as f:
                saved_state = json.load(f)
            
            if full_rebuild:
                if len(saved_state) != len(new_files):
                    raise ValueError(f"State validation failed: expected {len(new_files)} files, got {len(saved_state)}")
            # Note: For incremental updates, we cannot validate the final count
            # because it depends on both additions and deletions
                
            if verbose:
                logger.info(f"✅ State {operation_desc}: {file_count_desc}")
                
        except Exception as e:
            error_msg = f"❌ Failed to {'rebuild' if full_rebuild else 'update'} state: {e}"
            logger.error(error_msg)
            import traceback
            traceback.print_exc()
            # For incremental updates, fallback to full rebuild if update fails
            if not full_rebuild:
                logger.warning("🔄 Falling back to full state rebuild...")
                self._update_state(self._find_all_files(include_tests=False), collection_name, verbose, full_rebuild=True, deleted_files=None)
    
    def _rebuild_full_state(self, collection_name: str, verbose: bool = False):
        """Rebuild full state file from all current files."""
        try:
            if verbose:
                logger.info("🔄 Rebuilding complete state from all project files...")
            
            # Get all current files
            all_files = self._find_all_files(include_tests=False)
            
            # Use unified _update_state method with full_rebuild=True
            self._update_state(all_files, collection_name, verbose, full_rebuild=True, deleted_files=None)
                
        except Exception as e:
            error_msg = f"❌ Failed to rebuild state: {e}"
            logger.error(error_msg)
            import traceback
            traceback.print_exc()
    
    def _collect_embedding_cost_data(self, embedding_results: List[Any]) -> Dict[str, Union[int, float]]:
        """Collect cost data from embedding results."""
        total_tokens = 0
        total_cost = 0.0
        total_requests = 0
        
        # Collect cost data from embedding results
        for embedding_result in embedding_results:
            if hasattr(embedding_result, 'token_count') and embedding_result.token_count:
                total_tokens += embedding_result.token_count
            if hasattr(embedding_result, 'cost_estimate') and embedding_result.cost_estimate:
                total_cost += embedding_result.cost_estimate
        
        # Count successful requests
        if hasattr(self.embedder, 'get_usage_stats'):
            stats_before = getattr(self, '_last_usage_stats', {'total_requests': 0})
            current_stats = self.embedder.get_usage_stats()
            total_requests += max(0, current_stats.get('total_requests', 0) - stats_before.get('total_requests', 0))
            self._last_usage_stats = current_stats
        
        return {'tokens': total_tokens, 'cost': total_cost, 'requests': total_requests}
    
    def _cleanup_temp_file(self, temp_file: Optional[Path]):
        """Safely clean up temporary file with exception handling."""
        if temp_file and temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass  # Ignore cleanup errors
    
    def _handle_deleted_files(self, collection_name: str, deleted_files: Union[str, List[str]], verbose: bool = False):
        """Handle deleted files by removing their entities and orphaned relations."""
        # Convert single path to list for unified handling
        if isinstance(deleted_files, str):
            deleted_files = [deleted_files]
            
        if not deleted_files:
            return
        
        total_entities_deleted = 0
        
        try:
            for deleted_file in deleted_files:
                logger.info(f"🗑️ Handling deleted file: {deleted_file}")
                
                # State file always stores relative paths, construct the full path
                # Note: deleted_file is always relative from state file (see _get_current_state)
                # Don't use .resolve() as it adds /private on macOS, but entities are stored without it
                full_path = str(self.project_path / deleted_file)
                
                if verbose:
                    logger.debug(f"   📁 Resolved to: {full_path}")
                
                # Use the vector store's find_entities_for_file method
                logger.debug(f"   🔍 Finding ALL entities for file: {full_path}")
                
                point_ids = []
                try:
                    # Use the elegant single-query method
                    found_entities = self.vector_store.find_entities_for_file(collection_name, full_path)
                    
                    if found_entities:
                        logger.debug(f"   ✅ Found {len(found_entities)} entities for file")
                        for entity in found_entities:
                            entity_name = entity.get('name', 'Unknown')
                            entity_type = entity.get('type', 'unknown')
                            entity_id = entity.get('id')
                            logger.debug(f"      🆔 ID: {entity_id}, name: '{entity_name}', type: {entity_type}")
                        
                        # Extract point IDs for deletion
                        point_ids = [entity['id'] for entity in found_entities]
                    else:
                        logger.debug(f"   ⚠️ No entities found for {deleted_file}")
                        
                except Exception as e:
                    logger.error(f"   ❌ Error finding entities: {e}")
                    point_ids = []
                
                # Remove duplicates and delete all found points
                point_ids = list(set(point_ids))
                logger.info(f"   🎯 Total unique point IDs to delete: {len(point_ids)}")
                if point_ids and verbose:
                    logger.debug(f"      🆔 Point IDs: {point_ids}")
                    
                if point_ids:
                    # Delete the points
                    logger.info(f"🗑️ DEBUG: About to DELETE from Qdrant - file: '{deleted_file}' resolved to: '{full_path}' with {len(point_ids)} points")
                    logger.info(f"   🗑️ Attempting to delete {len(point_ids)} points...")
                    delete_result = self.vector_store.delete_points(collection_name, point_ids)
                    
                    if delete_result.success:
                        entities_deleted = len(point_ids)
                        total_entities_deleted += entities_deleted
                        logger.info(f"   ✅ Successfully removed {entities_deleted} entities from {deleted_file}")
                    else:
                        logger.error(f"   ❌ Failed to remove entities from {deleted_file}: {delete_result.errors}")
                else:
                    logger.warning(f"   ⚠️ No entities found for {deleted_file} - nothing to delete")
            
            # NEW: Clean up orphaned relations after entity deletion
            if total_entities_deleted > 0:
                if verbose:
                    logger.info(f"🔍 Starting orphan cleanup after deleting {total_entities_deleted} entities from {len(deleted_files)} files:")
                    for df in deleted_files:
                        logger.info(f"   📁 {df}")
                
                orphaned_deleted = self.vector_store._cleanup_orphaned_relations(collection_name, verbose)
                if verbose and orphaned_deleted > 0:
                    logger.info(f"✅ Cleanup complete: {total_entities_deleted} entities, {orphaned_deleted} orphaned relations removed")
                elif verbose:
                    logger.info(f"✅ Cleanup complete: {total_entities_deleted} entities removed, no orphaned relations found")
                        
        except Exception as e:
            logger.error(f"Error handling deleted files: {e}")
    
    
    

    
    def _is_test_file(self, file_path: Path) -> bool:
        """Check if a file is a test file - DISABLED."""
        return False