"""File system event handler for automatic indexing."""

import asyncio
from pathlib import Path
from typing import Dict, Any, Optional, Set
from .debounce import FileChangeCoalescer
from ..indexer_logging import get_logger

try:
    from watchdog.events import FileSystemEventHandler
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False
    # Mock class for when watchdog is not available
    class FileSystemEventHandler:
        pass


class IndexingEventHandler(FileSystemEventHandler):
    """File system event handler with debouncing and batch processing."""
    
    def __init__(self, project_path: str, collection_name: str, 
                 debounce_seconds: float = 2.0, settings: Optional[Dict[str, Any]] = None, verbose: bool = False):
        
        if not WATCHDOG_AVAILABLE:
            raise ImportError("Watchdog not available. Install with: pip install watchdog")
        
        super().__init__()
        
        self.project_path = Path(project_path)
        self.collection_name = collection_name
        self.debounce_seconds = debounce_seconds
        self.settings = settings or {}
        self.verbose = verbose
        
        # File filtering - use patterns from settings or fallback to defaults
        self.watch_patterns = self.settings.get("watch_patterns", ["*.py", "*.md"])
        self.ignore_patterns = self.settings.get("ignore_patterns", [
            "*.pyc", "__pycache__", ".git", ".venv", "node_modules"
        ])
        self.max_file_size = self.settings.get("max_file_size", 1048576)  # 1MB
        
        # Change tracking
        self.coalescer = FileChangeCoalescer(delay=debounce_seconds)
        self.processed_files: Set[str] = set()
        
        # Stats
        self.events_received = 0
        self.events_processed = 0
        self.events_ignored = 0
    
    def on_modified(self, event):
        """Handle file modification events."""
        if not event.is_directory:
            self._handle_file_event(event.src_path, "modified")
    
    def on_created(self, event):
        """Handle file creation events."""
        if not event.is_directory:
            self._handle_file_event(event.src_path, "created")
    
    def on_deleted(self, event):
        """Handle file deletion events."""
        if not event.is_directory:
            self._handle_file_event(event.src_path, "deleted")
    
    def on_moved(self, event):
        """Handle file move events."""
        if not event.is_directory:
            # Treat as delete + create
            self._handle_file_event(event.src_path, "deleted")
            self._handle_file_event(event.dest_path, "created")
    
    def _handle_file_event(self, file_path: str, event_type: str):
        """Process a file system event."""
        self.events_received += 1
        
        try:
            path = Path(file_path)
            
            # Check if we should process this file
            if not self._should_process_file(path):
                self.events_ignored += 1
                return
            
            # Use coalescer to debounce rapid changes
            if event_type == "deleted":
                # Process deletions immediately using shared deletion logic
                self._process_file_deletion(path)
                self.events_processed += 1
            else:
                # Debounce modifications and creations
                if self.coalescer.add_change(file_path):
                    self._process_file_change(path, event_type)
                    self.events_processed += 1
        
        except Exception as e:
            logger = get_logger()
            logger.error(f"❌ Error handling file event {file_path}: {e}")
    
    def _should_process_file(self, path: Path) -> bool:
        """Check if a file should be processed."""
        from .file_utils import should_process_file
        return should_process_file(
            path, self.project_path, self.watch_patterns, 
            self.ignore_patterns, self.max_file_size
        )
    
    def _process_file_change(self, path: Path, event_type: str):
        """Process a file change or creation with Git+Meta deduplication."""
        try:
            relative_path = path.relative_to(self.project_path)
            logger = get_logger()
            logger.info(f"🔄 Auto-indexing ({event_type}): {relative_path}")
            
            # Use Git+Meta optimized single-file indexing instead of batch processing
            # This provides content deduplication and skips unchanged files
            if not hasattr(self, '_indexer'):
                self._indexer = self._create_indexer()
            
            # Use the same optimized path as CLI single-file indexing
            result = self._indexer.index_single_file(path, self.collection_name)
            
            if result.success:
                self.processed_files.add(str(path))
                
                # Log Git+Meta efficiency information
                if result.embedding_requests == 0:
                    logger.info(f"⚡ Git+Meta: Skipped {relative_path} (unchanged content)")
                else:
                    logger.info(f"✅ Indexed: {relative_path} ({result.entities_created} entities, {result.embedding_requests} requests)")
                logger.info("-----------------------------------------")
            else:
                logger.error(f"❌ Failed to index: {relative_path} - {result.errors}")
        
        except Exception as e:
            logger.error(f"❌ Error processing file change {path}: {e}")
    
    def _create_indexer(self):
        """Create a CoreIndexer instance for Git+Meta optimized processing."""
        from ..config.config_loader import ConfigLoader
        from ..indexer import CoreIndexer
        from ..embeddings.voyage import VoyageEmbedder
        from ..embeddings.openai import OpenAIEmbedder
        from ..storage.qdrant import QdrantStore
        from ..storage.base import CachingVectorStore
        
        # Load configuration
        config = ConfigLoader().load()
        
        # Create embedder based on provider
        provider = config.embedding_provider
        if provider == "voyage":
            embedder = VoyageEmbedder(
                api_key=config.voyage_api_key,
                model=config.voyage_model
            )
        else:
            embedder = OpenAIEmbedder(
                api_key=config.openai_api_key,
                model="text-embedding-3-small"
            )
        
        # Create vector store with caching
        vector_store = QdrantStore(
            url=config.qdrant_url,
            api_key=config.qdrant_api_key
        )
        cached_store = CachingVectorStore(vector_store)
        
        # Create indexer
        return CoreIndexer(config, embedder, cached_store, self.project_path)
    
    def _process_file_deletion(self, path: Path):
        """Process a file deletion using shared deletion logic."""
        # FIX: Add file existence check to prevent phantom deletions
        if path.exists():
            logger = get_logger()
            logger.info(f"🛡️  File still exists at {path.relative_to(self.project_path)}, treating as modification")
            # Treat phantom deletion as file modification instead of aborting
            self._process_file_change(path, "modified")
            return
        
        try:
            relative_path = path.relative_to(self.project_path)
            logger = get_logger()
            logger.info(f"🗑️  File deleted: {relative_path}")
            
            # Remove from processed files
            self.processed_files.discard(str(path))
            
            # Use shared deletion function that calls the same core logic as incremental
            from ..main import run_indexing_with_shared_deletion
            
            success = run_indexing_with_shared_deletion(
                project_path=str(self.project_path),
                collection_name=self.collection_name,
                deleted_file_path=str(path),
                quiet=not self.verbose,
                verbose=self.verbose
            )
            
            if success:
                logger.info(f"✅ Cleanup completed for deleted file: {relative_path}")
            else:
                logger.warning(f"❌ Cleanup may have failed for deleted file: {relative_path}")
            
        except Exception as e:
            logger.error(f"❌ Error processing file deletion {path}: {e}")
    
    
    def get_stats(self) -> Dict[str, Any]:
        """Get event handler statistics."""
        return {
            "project_path": str(self.project_path),
            "collection_name": self.collection_name,
            "events_received": self.events_received,
            "events_processed": self.events_processed,
            "events_ignored": self.events_ignored,
            "processed_files": len(self.processed_files),
            "debounce_seconds": self.debounce_seconds,
            "coalescer_stats": self.coalescer.get_stats() if hasattr(self.coalescer, 'get_stats') else {}
        }
    
    def cleanup(self):
        """Clean up resources and old entries."""
        # Clean up old coalescer entries
        self.coalescer.cleanup_old_entries()
        
        # Clean up processed files set if it gets too large
        if len(self.processed_files) > 10000:
            # Keep only the most recent 5000
            self.processed_files = set(list(self.processed_files)[-5000:])




class Watcher:
    """Unified watcher class that bridges sync file events to async processing."""
    
    def __init__(self, repo_path: str, config, embedder, store, debounce_seconds: float = 2.0):
        """Initialize the watcher with required dependencies.
        
        Args:
            repo_path: Path to the repository to watch
            config: IndexerConfig object with settings
            embedder: Embedder instance for creating embeddings
            store: VectorStore instance for storage operations
            debounce_seconds: Debounce delay in seconds for file changes
        """
        if not WATCHDOG_AVAILABLE:
            raise ImportError("Watchdog not available. Install with: pip install watchdog")
        
        from watchdog.observers import Observer
        import os
        
        # Normalize path to handle symlinks (critical for macOS /var -> /private/var)
        self.repo_path = Path(os.path.realpath(repo_path))
        self.config = config
        self.embedder = embedder
        self.store = store
        
        # Extract settings from config for compatibility
        self.collection_name = getattr(config, 'collection_name', 'default')
        self.debounce_seconds = debounce_seconds
        
        # File filtering - load from project config first, then config, then defaults
        try:
            from claude_indexer.config.project_config import ProjectConfigManager
            project_manager = ProjectConfigManager(self.repo_path)
            self.include_patterns = project_manager.get_include_patterns()
            self.exclude_patterns = project_manager.get_exclude_patterns()
            print(f"✅ Watcher using PROJECT CONFIG patterns:")
            print(f"   Include: {self.include_patterns}")
            print(f"   Exclude: {self.exclude_patterns[:5]}...")  # Show first 5
        except Exception as e:
            # Fallback to config or defaults if project config fails
            import traceback
            print(f"🐛 ProjectConfig error: {type(e).__name__}: {e}")
            print(f"🐛 Traceback: {traceback.format_exc()}")
            self.include_patterns = getattr(config, 'include_patterns', [
                '*.py', '*.pyi', '*.js', '*.jsx', '*.ts', '*.tsx', '*.mjs', '*.cjs',
                '*.html', '*.htm', '*.css', '*.json', '*.yaml', '*.yml', '*.md', '*.txt'
            ])
            self.exclude_patterns = getattr(config, 'exclude_patterns', [
                '*.pyc', '__pycache__', '.git', '.venv', 'node_modules', 'qdrant_storage'
            ])
            print(f"⚠️  Watcher using FALLBACK patterns:")
            print(f"   Include: {self.include_patterns}")
            print(f"   Exclude: {self.exclude_patterns}")
        
        # Observer and async handler
        self.observer = Observer()
        self.async_handler = None
        self._bridge_handler = None
        self._running = False
        
    async def start(self):
        """Start file watching with async processing."""
        if self._running:
            return
            
        try:
            # Run initial indexing to ensure collection exists
            await self._run_initial_indexing()
            
            # Create async handler
            self.async_handler = AsyncWatcherHandler(
                repo_path=self.repo_path,
                config=self.config,
                embedder=self.embedder,
                store=self.store,
                debounce_seconds=self.debounce_seconds
            )
            
            # Create bridge handler for watchdog -> async
            self._bridge_handler = WatcherBridgeHandler(
                repo_path=self.repo_path,
                async_handler=self.async_handler,
                include_patterns=self.include_patterns,
                exclude_patterns=self.exclude_patterns,
                loop=asyncio.get_running_loop()
            )
            
            # Start async processing
            await self.async_handler.start()
            
            # Start file system watching
            self.observer.schedule(self._bridge_handler, str(self.repo_path), recursive=True)
            self.observer.start()
            
            self._running = True
            
        except Exception as e:
            print(f"❌ Failed to start watcher: {e}")
            await self.stop()
            raise
    
    async def _run_initial_indexing(self):
        """Run initial indexing to ensure collection exists and project is indexed."""
        try:
            # Check if state file exists to determine if initial indexing is needed
            loop = asyncio.get_running_loop()
            
            def check_and_run_indexing():
                from ..indexer import CoreIndexer
                indexer = CoreIndexer(
                    config=self.config,
                    embedder=self.embedder,
                    vector_store=self.store,
                    project_path=self.repo_path
                )
                
                # Check if state file exists for this collection
                state_file = indexer._get_state_file(self.collection_name)
                should_be_incremental = state_file.exists()
                
                if should_be_incremental:
                    print(f"📋 State file exists for {self.collection_name}, using incremental indexing")
                else:
                    print(f"🔄 No state file found for {self.collection_name}, running full initial indexing")
                
                return indexer.index_project(
                    collection_name=self.collection_name
                )
            
            result = await loop.run_in_executor(None, check_and_run_indexing)
            if result.success:
                print(f"✅ Initial indexing completed for {self.collection_name}")
            else:
                print(f"⚠️ Initial indexing had issues: {result.errors}")
                
        except Exception as e:
            print(f"❌ Initial indexing failed: {e}")
    
    async def stop(self):
        """Stop file watching and cleanup."""
        if not self._running:
            return
            
        self._running = False
        
        try:
            # Stop file system observer
            if self.observer and self.observer.is_alive():
                self.observer.stop()
                self.observer.join(timeout=5.0)
            
            # Stop async handler
            if self.async_handler:
                await self.async_handler.stop()
                
        except Exception as e:
            print(f"❌ Error stopping watcher: {e}")


class WatcherBridgeHandler(FileSystemEventHandler):
    """Bridge handler that connects watchdog events to async processing."""
    
    def __init__(self, repo_path: Path, async_handler, include_patterns: list, exclude_patterns: list, loop=None):
        super().__init__()
        self.repo_path = repo_path
        self.async_handler = async_handler
        self.include_patterns = include_patterns or ['*.py', '*.md']
        self.exclude_patterns = exclude_patterns or []
        self.loop = loop  # Event loop reference
        
    def on_modified(self, event):
        """Handle file modification events."""
        if not event.is_directory:
            self._schedule_event(event.src_path, "modified")
    
    def on_created(self, event):
        """Handle file creation events."""
        if not event.is_directory:
            self._schedule_event(event.src_path, "created")
    
    def on_deleted(self, event):
        """Handle file deletion events."""
        if not event.is_directory:
            self._schedule_event(event.src_path, "deleted")
    
    def on_moved(self, event):
        """Handle file move events."""
        if not event.is_directory:
            # Treat as delete + create
            self._schedule_event(event.src_path, "deleted")
            self._schedule_event(event.dest_path, "created")
    
    def _schedule_event(self, file_path: str, event_type: str):
        """Schedule an event to be processed in the main thread."""
        try:
            if self.loop and not self.loop.is_closed():
                # Use call_soon_threadsafe to schedule the coroutine from any thread
                asyncio.run_coroutine_threadsafe(
                    self._handle_event(file_path, event_type), 
                    self.loop
                )
        except Exception as e:
            print(f"❌ Error scheduling event {file_path}: {e}")
    
    async def _handle_event(self, file_path: str, event_type: str):
        """Process a file system event asynchronously."""
        try:
            path = Path(file_path)
            
            # Check if we should process this file
            if not self._should_process_file(path):
                return
            
            # Forward to async handler
            await self.async_handler.handle_file_event(file_path, event_type)
            
        except Exception as e:
            print(f"❌ Error in bridge handler: {e}")
    
    def _should_process_file(self, path: Path) -> bool:
        """Check if a file should be processed based on patterns."""
        from .file_utils import should_process_file
        return should_process_file(
            path, self.repo_path, self.include_patterns, 
            self.exclude_patterns, max_file_size=1048576
        )


class AsyncWatcherHandler:
    """Async handler that processes file events and triggers indexing."""
    
    def __init__(self, repo_path: Path, config, embedder, store, debounce_seconds: float = 2.0):
        self.repo_path = repo_path
        self.config = config
        self.embedder = embedder
        self.store = store
        
        # Import here to avoid circular imports
        from .debounce import AsyncDebouncer
        
        self.debouncer = AsyncDebouncer(
            delay=debounce_seconds,
            max_batch_size=50
        )
        self.debouncer.set_callback(self._process_batch)
        
        # Stats
        self.files_processed = 0
        self.batches_processed = 0
    
    async def start(self):
        """Start async processing."""
        await self.debouncer.start()
    
    async def stop(self):
        """Stop async processing."""
        await self.debouncer.stop()
    
    async def handle_file_event(self, file_path: str, event_type: str):
        """Handle a file system event."""
        await self.debouncer.add_file_event(file_path, event_type)
    
    async def _process_batch(self, batch_event: Dict[str, Any]):
        """Process a batch of file changes using the provided embedder and store."""
        try:
            modified_files = batch_event.get("modified_files", [])
            deleted_files = batch_event.get("deleted_files", [])
            
            if modified_files:
                success = await self._index_files(modified_files)
                if success:
                    self.files_processed += len(modified_files)
            
            if deleted_files:
                await self._handle_deletions(deleted_files)
            
            self.batches_processed += 1
            
        except Exception as e:
            print(f"❌ Error processing batch: {e}")
    
    async def _index_files(self, file_paths: list) -> bool:
        """Index the given files using the provided embedder and store."""
        try:
            # Run indexing in executor to avoid blocking
            loop = asyncio.get_running_loop()
            
            def run_indexing():
                # Import here to avoid circular imports
                from ..main import run_indexing_with_specific_files
                
                # Process only the specific files that triggered the debounce
                success = run_indexing_with_specific_files(
                    project_path=str(self.repo_path),
                    collection_name=getattr(self.config, 'collection_name', 'default'),
                    file_paths=file_paths,  # Process only specified files
                    quiet=True,
                    verbose=False
                )
                
                return success
            
            return await loop.run_in_executor(None, run_indexing)
            
        except Exception as e:
            print(f"❌ File indexing failed: {e}")
            return False
    
    async def _handle_deletions(self, file_paths: list):
        """Handle file deletions by removing related entities."""
        try:
            print(f"🗑️  Processing {len(file_paths)} deleted files...")
            
            # Trigger incremental indexing to handle deletions and cleanup orphaned relations
            # This uses the existing state-based deletion detection which will:
            # 1. Detect the deleted files via SHA256 state comparison
            # 2. Call _handle_deleted_files() which removes entities
            # 3. Automatically clean up orphaned relations via _cleanup_orphaned_relations()
            success = await self._index_files([])  # Empty list triggers incremental indexing
            
            if success:
                print(f"✅ Cleanup completed for {len(file_paths)} deleted files")
            else:
                print(f"❌ Cleanup may have failed for {len(file_paths)} deleted files")
            
        except Exception as e:
            print(f"❌ Error handling deletions: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get handler statistics."""
        return {
            "files_processed": self.files_processed,
            "batches_processed": self.batches_processed,
            "debouncer_stats": self.debouncer.get_stats()
        }