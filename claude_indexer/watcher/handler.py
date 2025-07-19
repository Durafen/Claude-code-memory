"""File system event handler for automatic indexing."""

import asyncio
import time
from pathlib import Path
from typing import Any

from ..indexer_logging import get_logger
from .debounce import FileChangeCoalescer

try:
    from watchdog.events import FileSystemEventHandler

    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False

    # Mock class for when watchdog is not available
    class FileSystemEventHandler:  # type: ignore[no-redef]
        pass


class IndexingEventHandler(FileSystemEventHandler):
    """File system event handler with debouncing and batch processing."""

    def __init__(
        self,
        project_path: str,
        collection_name: str,
        debounce_seconds: float = 2.0,
        settings: dict[str, Any] | None = None,
        verbose: bool = False,
    ):
        if not WATCHDOG_AVAILABLE:
            raise ImportError(
                "Watchdog not available. Install with: pip install watchdog"
            )

        super().__init__()

        self.project_path = Path(project_path).resolve()
        self.collection_name = collection_name
        self.debounce_seconds = debounce_seconds
        self.settings = settings or {}
        self.verbose = verbose

        # File filtering - use patterns from settings or fallback to defaults
        self.watch_patterns = self.settings.get("watch_patterns", ["*.py", "*.md"])
        self.ignore_patterns = self.settings.get(
            "ignore_patterns", ["*.pyc", "__pycache__", ".git", ".venv", "node_modules"]
        )
        self.max_file_size = self.settings.get("max_file_size", 1048576)  # 1MB

        # Change tracking
        self.coalescer = FileChangeCoalescer(
            delay=debounce_seconds, callback=self._process_batch_from_coalescer
        )
        self.processed_files: set[str] = set()

        # Stats
        self.events_received = 0
        self.events_processed = 0
        self.events_ignored = 0

    def on_modified(self, event: Any) -> None:
        """Handle file modification events."""
        if not event.is_directory:
            self._handle_file_event(event.src_path, "modified")

    def on_created(self, event: Any) -> None:
        """Handle file creation events."""
        if not event.is_directory:
            self._handle_file_event(event.src_path, "created")

    def on_deleted(self, event: Any) -> None:
        """Handle file deletion events."""
        if not event.is_directory:
            self._handle_file_event(event.src_path, "deleted")

    def on_moved(self, event: Any) -> None:
        """Handle file move events."""
        if not event.is_directory:
            # Treat as delete + create
            self._handle_file_event(event.src_path, "deleted")
            self._handle_file_event(event.dest_path, "created")

    def _handle_file_event(self, file_path: str, event_type: str) -> None:
        """Process a file system event by adding it to the coalescer."""
        self.events_received += 1

        try:
            path = Path(file_path)

            # Check if we should process this file
            if not self._should_process_file(path):
                self.events_ignored += 1
                return

            # Add the change to the coalescer. The callback will handle processing.
            self.coalescer.add_change(file_path)

        except Exception as e:
            logger = get_logger()
            logger.error(f"❌ Error handling file event {file_path}: {e}")
            import traceback

            logger.error(f"❌ Traceback: {traceback.format_exc()}")

    def _process_batch_from_coalescer(self, ready_files: list[str]):
        """Callback for the coalescer to process a batch of files."""
        if ready_files:
            ready_paths = [Path(fp) for fp in ready_files]
            self._process_file_batch(ready_paths)
            self.events_processed += len(ready_files)

    def _should_process_file(self, path: Path) -> bool:
        """Check if a file should be processed."""
        from claude_indexer.watcher.file_utils import should_process_file

        return should_process_file(
            path,
            self.project_path,
            self.watch_patterns,
            self.ignore_patterns,
            self.max_file_size,
        )

    def _process_file_change(self, path: Path, event_type: str):
        """Process a file change or creation with Git+Meta deduplication."""
        try:
            relative_path = path.relative_to(self.project_path)
            logger = get_logger()
            logger.info(f"🔄 Auto-indexing ({event_type}): {relative_path}")

            # Use Git+Meta optimized single-file indexing instead of batch processing
            # This provides content deduplication and skips unchanged files
            if not hasattr(self, "_indexer"):
                self._indexer = self._create_indexer()

            # Use main.py session summary display (same as CLI runs)
            from ..main import run_indexing_with_specific_files

            success = run_indexing_with_specific_files(
                str(self.project_path),
                self.collection_name,
                [path],
                quiet=not self.verbose,
                verbose=self.verbose,
            )

            if success:
                self.processed_files.add(str(path))

        except Exception as e:
            logger.error(f"❌ Error processing file change {path}: {e}")

    def _process_file_batch(self, paths: list[Path]):
        """Process a batch of file changes with phantom deletion detection."""
        try:
            if not paths:
                return

            # Separate existing files from potential deletions
            existing_files = []
            potential_deletions = []

            for path in paths:
                if path.exists():
                    existing_files.append(path)
                else:
                    # It might be a temporary deletion (atomic save), so check again
                    potential_deletions.append(path)

            # Re-check potential deletions after a short delay to handle atomic saves
            if potential_deletions:
                time.sleep(self.debounce_seconds)  # Use configured debounce delay

                real_deletions = []
                for path in potential_deletions:
                    if path.exists():
                        # File reappeared, so it was a modification
                        existing_files.append(path)
                    else:
                        # File is still gone, so it's a real deletion
                        real_deletions.append(path)
            else:
                real_deletions = []

            logger = get_logger()

            # Process existing files (includes phantom deletions treated as modifications)
            if existing_files:
                # Use a set to handle duplicates if a file was in both lists
                unique_existing_paths = sorted(
                    list(set(existing_files)), key=lambda p: str(p)
                )

                relative_paths = [
                    p.relative_to(self.project_path) for p in unique_existing_paths
                ]
                logger.info(
                    f"🔄 Auto-indexing batch ({len(unique_existing_paths)} files): {', '.join(str(rp) for rp in relative_paths)}"
                )

                from ..main import run_indexing_with_specific_files

                success = run_indexing_with_specific_files(
                    str(self.project_path),
                    self.collection_name,
                    unique_existing_paths,
                    quiet=not self.verbose,
                    verbose=self.verbose,
                )

                if success:
                    for path in unique_existing_paths:
                        self.processed_files.add(str(path))
                else:
                    logger.error(
                        f"❌ Batch indexing failed for {len(unique_existing_paths)} files"
                    )

            # Process real deletions
            if real_deletions:
                for path in real_deletions:
                    self._process_file_deletion(path)

        except Exception as e:
            logger = get_logger()
            logger.error(f"❌ Error processing file batch: {e}")
            import traceback

            logger.error(f"❌ Traceback: {traceback.format_exc()}")

    def _create_indexer(self):
        """Create a CoreIndexer instance for Git+Meta optimized processing."""
        from ..config.config_loader import ConfigLoader
        from ..embeddings.openai import OpenAIEmbedder
        from ..embeddings.voyage import VoyageEmbedder
        from ..indexer import CoreIndexer
        from ..storage.base import CachingVectorStore
        from ..storage.qdrant import QdrantStore

        # Load configuration
        config = ConfigLoader().load()

        # Create embedder based on provider
        provider = config.embedding_provider
        if provider == "voyage":
            embedder = VoyageEmbedder(
                api_key=config.voyage_api_key, model=config.voyage_model
            )
        else:
            embedder = OpenAIEmbedder(
                api_key=config.openai_api_key, model="text-embedding-3-small"
            )

        # Create vector store with caching
        vector_store = QdrantStore(url=config.qdrant_url, api_key=config.qdrant_api_key)
        cached_store = CachingVectorStore(vector_store)

        # Create indexer
        return CoreIndexer(config, embedder, cached_store, self.project_path)

    def _process_file_deletion(self, path: Path):
        """Process a file deletion using shared deletion logic."""
        # FIX: Add file existence check to prevent phantom deletions
        if path.exists():
            logger = get_logger()
            logger.warning(
                f"🛡️  Detected a phantom deletion event for a file that still exists: {path.relative_to(self.project_path)}. Ignoring the event."
            )
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
                verbose=self.verbose,
            )

            if success:
                logger.info(f"✅ Cleanup completed for deleted file: {relative_path}")
            else:
                logger.warning(
                    f"❌ Cleanup may have failed for deleted file: {relative_path}"
                )

        except Exception as e:
            logger.error(f"❌ Error processing file deletion {path}: {e}")

    def get_stats(self) -> dict[str, Any]:
        """Get event handler statistics."""
        return {
            "project_path": str(self.project_path),
            "collection_name": self.collection_name,
            "events_received": self.events_received,
            "events_processed": self.events_processed,
            "events_ignored": self.events_ignored,
            "processed_files": len(self.processed_files),
            "debounce_seconds": self.debounce_seconds,
            "coalescer_stats": self.coalescer.get_stats()
            if hasattr(self.coalescer, "get_stats")
            else {},
        }

    def cleanup(self):
        """Clean up resources and old entries."""
        # Process any remaining pending files before cleanup
        if (
            hasattr(self.coalescer, "has_pending_files")
            and self.coalescer.has_pending_files()
        ):
            remaining_files = self.coalescer.force_batch()
            if remaining_files:
                ready_paths = [Path(fp) for fp in remaining_files]
                self._process_file_batch(ready_paths)
                self.events_processed += len(remaining_files)

        # Clean up old coalescer entries
        self.coalescer.cleanup_old_entries()

        # Clean up processed files set if it gets too large
        if len(self.processed_files) > 10000:
            # Keep only the most recent 5000
            self.processed_files = set(list(self.processed_files)[-5000:])


class Watcher:
    """Unified watcher class using IndexingEventHandler for reliable file watching."""

    def __init__(
        self, repo_path: str, config, embedder, store, debounce_seconds: float = 2.0
    ):
        """Initialize the watcher with required dependencies.

        Args:
            repo_path: Path to the repository to watch
            config: IndexerConfig object with settings
            embedder: Embedder instance for creating embeddings
            store: VectorStore instance for storage operations
            debounce_seconds: Debounce delay in seconds for file changes
        """
        if not WATCHDOG_AVAILABLE:
            raise ImportError(
                "Watchdog not available. Install with: pip install watchdog"
            )

        import os

        from watchdog.observers import Observer

        # Normalize path to handle symlinks (critical for macOS /var -> /private/var)
        self.repo_path = Path(os.path.realpath(repo_path))
        self.config = config
        self.embedder = embedder
        self.store = store

        # Extract settings from config for compatibility
        self.collection_name = getattr(config, "collection_name", "default")
        self.debounce_seconds = debounce_seconds

        # File filtering - load from project config first, then config, then defaults
        try:
            from claude_indexer.config.project_config import ProjectConfigManager

            project_manager = ProjectConfigManager(self.repo_path)
            self.include_patterns = project_manager.get_include_patterns()
            self.exclude_patterns = project_manager.get_exclude_patterns()
            print("✅ Watcher using PROJECT CONFIG patterns:")
            print(f"   Include: {self.include_patterns}")
            print(f"   Exclude: {self.exclude_patterns[:5]}...")  # Show first 5
        except Exception as e:
            # Fallback to config or defaults if project config fails
            import traceback

            print(f"🐛 ProjectConfig error: {type(e).__name__}: {e}")
            print(f"🐛 Traceback: {traceback.format_exc()}")
            self.include_patterns = getattr(
                config,
                "include_patterns",
                [
                    "*.py",
                    "*.pyi",
                    "*.js",
                    "*.jsx",
                    "*.ts",
                    "*.tsx",
                    "*.mjs",
                    "*.cjs",
                    "*.html",
                    "*.htm",
                    "*.css",
                    "*.json",
                    "*.yaml",
                    "*.yml",
                    "*.md",
                    "*.txt",
                ],
            )
            self.exclude_patterns = getattr(
                config,
                "exclude_patterns",
                [
                    "*.pyc",
                    "__pycache__",
                    ".git",
                    ".venv",
                    "node_modules",
                    "qdrant_storage",
                ],
            )
            print("⚠️  Watcher using FALLBACK patterns:")
            print(f"   Include: {self.include_patterns}")
            print(f"   Exclude: {self.exclude_patterns}")

        # Observer and event handler
        self.observer = Observer()
        self.event_handler = None
        self._running = False

    async def start(self):
        """Start file watching using IndexingEventHandler."""
        if self._running:
            return

        try:
            # Run initial indexing to ensure collection exists
            await self._run_initial_indexing()

            # Create IndexingEventHandler (no async complexity needed)
            settings = {
                "watch_patterns": self.include_patterns,
                "ignore_patterns": self.exclude_patterns,
            }
            self.event_handler = IndexingEventHandler(
                project_path=str(self.repo_path),
                collection_name=self.collection_name,
                debounce_seconds=self.debounce_seconds,
                settings=settings,
                verbose=getattr(self.config, "verbose", False),
            )

            # Start file system watching
            self.observer.schedule(
                self.event_handler, str(self.repo_path), recursive=True
            )
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
                    project_path=self.repo_path,
                )

                # Check if state file exists for this collection
                state_file = indexer._get_state_file(self.collection_name)
                should_be_incremental = state_file.exists()

                if should_be_incremental:
                    print(
                        f"📋 State file exists for {self.collection_name}, using incremental indexing"
                    )
                else:
                    print(
                        f"🔄 No state file found for {self.collection_name}, running full initial indexing"
                    )

                return indexer.index_project(collection_name=self.collection_name)

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

            # Cleanup IndexingEventHandler coalescer
            if self.event_handler and hasattr(self.event_handler, "coalescer"):
                self.event_handler.coalescer.stop()

        except Exception as e:
            print(f"❌ Error stopping watcher: {e}")
