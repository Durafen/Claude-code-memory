"""Main entry point for the Claude Code indexer."""

import sys
from pathlib import Path
from typing import Optional

from .config import load_config
from .indexer import CoreIndexer
from .embeddings.registry import create_embedder_from_config
from .storage.registry import create_store_from_config
from .indexer_logging import setup_logging


def run_indexing_with_shared_deletion(project_path: str, collection_name: str,
                                    deleted_file_path: str, quiet: bool = False, 
                                    verbose: bool = False, config_file: Optional[str] = None) -> bool:
    """Run deletion handling with shared deletion logic for a single file."""
    try:
        # Validate project path first
        project = Path(project_path).resolve()
        if not project.exists():
            print(f"❌ Project path does not exist: {project}")
            return False
        
        # Setup logging with project-specific file logging
        logger = setup_logging(quiet=quiet, verbose=verbose, collection_name=collection_name, project_path=project)
        
        # Load configuration
        config_path = Path(config_file) if config_file else None
        config = load_config(config_path)
        
        # Create components using direct Qdrant integration
        # Get the appropriate API key based on provider
        provider = config.embedding_provider
        api_key = getattr(config, f'{provider}_api_key', None)
        model = config.voyage_model if provider == "voyage" else "text-embedding-3-small"
        
        embedder = create_embedder_from_config({
            "provider": provider,
            "api_key": api_key,
            "model": model,
            "enable_caching": True
        })
        
        vector_store = create_store_from_config({
            "backend": "qdrant",
            "url": config.qdrant_url,
            "api_key": config.qdrant_api_key,
            "enable_caching": True
        })
        
        # Create indexer
        indexer = CoreIndexer(config, embedder, vector_store, project)
        
        # Convert absolute path to relative path for state consistency
        deleted_path = Path(deleted_file_path)
        relative_path = str(deleted_path.relative_to(project))
        
        # Use consolidated deletion function
        indexer._handle_deleted_files(collection_name, relative_path, verbose)
        
        # Update state file to remove deleted file entry
        indexer._update_state([], collection_name, verbose, full_rebuild=False, deleted_files=[relative_path])
        
        return True
        
    except Exception as e:
        if not quiet:
            logger.error(f"❌ Error in shared deletion: {e}")
        return False


def run_indexing_with_specific_files(project_path: str, collection_name: str,
                                    file_paths: list, quiet: bool = False, 
                                    verbose: bool = False,
                                    config_file: Optional[str] = None) -> bool:
    """Run indexing with specific file paths, bypassing file discovery.
    
    This function accepts specific files to process, eliminating the expensive
    file discovery step that scans the entire project.
    
    Args:
        project_path: Path to the project root
        collection_name: Name of the vector collection
        file_paths: List of Path objects to process
        quiet: Suppress non-error output
        verbose: Enable verbose output
        config_file: Optional configuration file path
        
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Validate project path first
        project = Path(project_path).resolve()
        if not project.exists():
            print(f"❌ Project path does not exist: {project}")
            return False
        
        # Setup logging with project-specific file logging
        logger = setup_logging(quiet=quiet, verbose=verbose, collection_name=collection_name, project_path=project)
        
        # Load configuration
        config_path = Path(config_file) if config_file else None
        config = load_config(config_path)
        
        # Create components using direct Qdrant integration
        # Get the appropriate API key based on provider
        provider = config.embedding_provider
        api_key = getattr(config, f'{provider}_api_key', None)
        model = config.voyage_model if provider == "voyage" else "text-embedding-3-small"
        
        if verbose:
            logger.debug(f"🔧 Config debug: provider='{provider}', model='{model}'")
            logger.debug(f"🔑 API key present: {api_key is not None}")
            logger.debug(f"⚙️  Voyage model: {getattr(config, 'voyage_model', 'NOT_SET')}")
        
        embedder = create_embedder_from_config({
            "provider": provider,
            "api_key": api_key,
            "model": model,
            "enable_caching": True
        })
        
        vector_store = create_store_from_config({
            "backend": "qdrant",
            "url": config.qdrant_url,
            "api_key": config.qdrant_api_key,
            "enable_caching": True
        })
        
        if not quiet and verbose:
            provider_name = provider.title() if provider else "OpenAI"
            logger.debug(f"⚡ Using Qdrant + {provider_name} (direct mode)")
        
        # Create indexer
        indexer = CoreIndexer(config, embedder, vector_store, project)
        
        # Convert file_paths to Path objects if needed
        paths_to_process = []
        for fp in file_paths:
            if isinstance(fp, str):
                paths_to_process.append(Path(fp))
            else:
                paths_to_process.append(fp)
        
        if not paths_to_process:
            if not quiet:
                logger.info("✅ No files to process")
            return True
        
        if not quiet and verbose:
            logger.info(f"🔄 Processing {len(paths_to_process)} specific files")
            logger.info(f"📦 Collection: {collection_name}")
        
        # Process files directly using batch processing
        entities, relations, implementation_chunks, errors = indexer._process_file_batch(paths_to_process, verbose)
        
        # Handle any processing errors
        if errors:
            if not quiet:
                logger.error(f"❌ Processing errors occurred:")
                for error in errors:
                    logger.error(f"   {error}")
        
        # Store vectors if we have entities or relations
        storage_success = True
        if entities or relations or implementation_chunks:
            storage_success = indexer._store_vectors(collection_name, entities, relations, implementation_chunks)
            if not storage_success:
                if not quiet:
                    logger.error("❌ Failed to store vectors in Qdrant")
                return False
        
        # Update state file with successfully processed files
        successfully_processed = [f for f in paths_to_process if str(f) not in errors]
        if successfully_processed:
            # Auto-detect incremental mode for state management
            state_file = indexer._get_state_file(collection_name)
            incremental = state_file.exists()
            
            # Use incremental update to merge with existing state
            indexer._update_state(successfully_processed, collection_name, verbose, 
                                full_rebuild=False, deleted_files=None)
            
            # Clean up orphaned relations after processing
            if incremental and successfully_processed:
                if verbose:
                    logger.info(f"🔍 Cleaning up orphaned relations after processing {len(successfully_processed)} files")
                orphaned_deleted = vector_store._cleanup_orphaned_relations(collection_name, verbose)
                if verbose and orphaned_deleted > 0:
                    logger.info(f"✅ Cleanup complete: {orphaned_deleted} orphaned relations removed")
                elif verbose:
                    logger.info("✅ No orphaned relations found")
        
        # Report results
        files_processed = len(successfully_processed)
        files_failed = len(errors)
        
        if not quiet:
            if verbose:
                logger.info(f"✅ Processing completed")
                logger.info(f"   Files processed: {files_processed}")
                logger.info(f"   Files failed: {files_failed}")
                logger.info(f"   Entities created: {len(entities)}")
                logger.info(f"   Relations created: {len(relations)}")
                
                # Show cost information if available
                if hasattr(indexer, '_session_cost_data'):
                    cost_data = indexer._session_cost_data
                    if cost_data.get('tokens', 0) > 0:
                        logger.info(f"   Tokens used: {cost_data['tokens']:,}")
                        logger.info(f"   Estimated cost: ${cost_data['cost']:.4f}")
                        logger.info(f"   API requests: {cost_data['requests']}")
            else:
                logger.info(f"✅ Processed {files_processed} files")
                if files_failed > 0:
                    logger.warning(f"⚠️  {files_failed} files failed")
        
        return storage_success and files_failed == 0
        
    except Exception as e:
        if not quiet:
            logger.error(f"❌ Error: {e}")
        return False


def run_indexing(project_path: str, collection_name: str, 
                quiet: bool = False, verbose: bool = False, 
                include_tests: bool = False,
                config_file: Optional[str] = None) -> bool:
    """Run indexing with the specified parameters.
    
    This function provides a programmatic interface for other modules.
    It discovers files and delegates to run_indexing_with_specific_files.
    """
    
    try:
        # Validate project path first
        project = Path(project_path).resolve()
        if not project.exists():
            print(f"❌ Project path does not exist: {project}")
            return False
        
        # Setup logging with project-specific file logging
        logger = setup_logging(quiet=quiet, verbose=verbose, collection_name=collection_name, project_path=project)
        
        # Load configuration to create indexer for file discovery
        config_path = Path(config_file) if config_file else None
        config = load_config(config_path)
        
        # Create components for file discovery
        # Get the appropriate API key based on provider
        provider = config.embedding_provider
        api_key = getattr(config, f'{provider}_api_key', None)
        model = config.voyage_model if provider == "voyage" else "text-embedding-3-small"
        
        embedder = create_embedder_from_config({
            "provider": provider,
            "api_key": api_key,
            "model": model,
            "enable_caching": True
        })
        
        vector_store = create_store_from_config({
            "backend": "qdrant",
            "url": config.qdrant_url,
            "api_key": config.qdrant_api_key,
            "enable_caching": True
        })
        
        # Create indexer for file discovery
        indexer = CoreIndexer(config, embedder, vector_store, project)
        
        # Auto-detect incremental mode
        state_file = indexer._get_state_file(collection_name)
        incremental = state_file.exists()
        
        if not quiet and verbose:
            logger.info(f"🔄 Indexing project: {project}")
            logger.info(f"📦 Collection: {collection_name}")
            logger.info(f"⚡ Mode: {'Incremental' if incremental else 'Full'} (auto-detected)")
        
        # Discover files to process
        if incremental:
            files_to_process, deleted_files = indexer._find_changed_files(include_tests, collection_name)
            
            # Handle deleted files first
            if deleted_files:
                indexer._handle_deleted_files(collection_name, deleted_files, verbose)
        else:
            files_to_process = indexer._find_all_files(include_tests)
            deleted_files = []
        
        if not files_to_process:
            if not quiet:
                logger.info("✅ No files to process")
            return True
        
        # Delegate to the specific files function
        return run_indexing_with_specific_files(
            project_path=project_path,
            collection_name=collection_name,
            file_paths=files_to_process,
            quiet=quiet,
            verbose=verbose,
            config_file=config_file
        )
        
    except Exception as e:
        if not quiet:
            logger.error(f"❌ Error: {e}")
        return False


def main():
    """Main entry point using Click CLI."""
    try:
        from .cli import cli
        cli()
    except ImportError:
        # Fallback to basic help if Click is not available - keep prints for CLI fallback
        print("Claude Code Memory Indexer")
        print()
        print("Click not available. Install with: pip install click")
        print("For basic indexing, use the run_indexing function directly.")
        sys.exit(1)


if __name__ == '__main__':
    main()