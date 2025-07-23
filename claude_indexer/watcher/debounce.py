"""Async debouncing for file change events."""

import asyncio
import contextlib
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any


class AsyncDebouncer:
    """Async debouncer with coalescing for file system events."""

    def __init__(self, delay: float = 2.0, max_batch_size: int = 100):
        self.delay = delay
        self.max_batch_size = max_batch_size

        # Track pending operations
        self._pending_files: dict[str, float] = {}  # file_path -> last_update_time
        self._deleted_files: set[str] = set()
        self._task: asyncio.Task[Any] | None = None
        self._callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None

        # Event loop management
        self._running = False
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    def set_callback(self, callback: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        """Set the callback function for processed events."""
        self._callback = callback

    async def start(self) -> None:
        """Start the debouncer task."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._process_events())

    async def stop(self) -> None:
        """Stop the debouncer and process remaining events."""
        self._running = False

        if self._task:
            # Process any remaining events
            await self._flush_pending()

            # Cancel the task
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def add_file_event(self, file_path: str, event_type: str) -> None:
        """Add a file change event to the debounce queue."""
        await self._queue.put(
            {"file_path": file_path, "event_type": event_type, "timestamp": time.time()}
        )

    async def _process_events(self) -> None:
        """Main event processing loop."""
        try:
            while self._running:
                try:
                    # Wait for events with timeout
                    event = await asyncio.wait_for(
                        self._queue.get(), timeout=self.delay
                    )
                    await self._handle_event(event)

                    # Process batch if we have enough pending
                    if len(self._pending_files) >= self.max_batch_size:
                        await self._flush_pending()

                except TimeoutError:
                    # Timeout occurred, process pending events
                    if self._pending_files or self._deleted_files:
                        await self._flush_pending()

        except asyncio.CancelledError:
            # Process remaining events before stopping
            await self._flush_pending()
            raise
        except Exception as e:
            print(f"Error in debouncer: {e}")

    async def _handle_event(self, event: dict[str, Any]) -> None:
        """Handle a single file system event."""
        file_path = event["file_path"]
        event_type = event["event_type"]
        timestamp = event["timestamp"]

        if event_type == "deleted":
            # Handle deleted files separately
            self._deleted_files.add(file_path)
            # Remove from pending if it was there
            self._pending_files.pop(file_path, None)
        else:
            # Handle created/modified files
            self._pending_files[file_path] = timestamp
            # Remove from deleted if it was marked for deletion
            self._deleted_files.discard(file_path)

    async def _flush_pending(self) -> None:
        """Process all pending events."""
        if self._callback is None:
            return

        current_time = time.time()

        # Filter files that have been stable for the delay period
        stable_files = {
            path: timestamp
            for path, timestamp in self._pending_files.items()
            if current_time - timestamp >= self.delay
        }

        # Remove processed files from pending
        for path in stable_files:
            del self._pending_files[path]

        # Process stable files and deletions
        if stable_files or self._deleted_files:
            batch_event = {
                "modified_files": list(stable_files.keys()),
                "deleted_files": list(self._deleted_files),
                "timestamp": current_time,
            }

            try:
                await self._callback(batch_event)
            except Exception as e:
                print(f"Error in debouncer callback: {e}")

            # Clear processed deletions
            self._deleted_files.clear()

    def get_stats(self) -> dict[str, Any]:
        """Get debouncer statistics."""
        return {
            "running": self._running,
            "pending_files": len(self._pending_files),
            "pending_deletions": len(self._deleted_files),
            "queue_size": self._queue.qsize(),
            "delay": self.delay,
            "max_batch_size": self.max_batch_size,
        }


class FileChangeCoalescer:
    """Simple file change coalescer with background timer for automatic processing."""

    def __init__(
        self, delay: float = 2.0, callback: Callable[[list[str]], None] | None = None
    ):

        self.delay = delay
        self.callback = callback
        self._pending: dict[str, float] = {}
        self._lock = threading.Lock()
        self._timer_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._start_timer()

    def _start_timer(self) -> None:
        """Start background timer thread to automatically process files."""

        if self._timer_thread is None or not self._timer_thread.is_alive():
            import threading
            self._timer_thread = threading.Thread(target=self._timer_loop, daemon=True)
            self._timer_thread.start()

    def _timer_loop(self) -> None:
        """Background timer that periodically checks for ready files."""
        import time

        while not self._stop_event.is_set():
            try:
                time.sleep(self.delay)
                self._check_and_process_ready_files()
            except Exception as e:
                print(f"❌ Timer error: {e}")

    def _check_and_process_ready_files(self) -> None:
        """Check pending files and process ready ones via callback."""
        import time

        current_time = time.time()

        ready_files = []
        with self._lock:
            files_to_remove = []

            # Find files ready for processing
            for file_path, timestamp in self._pending.items():
                if current_time - timestamp >= self.delay:
                    ready_files.append(file_path)
                    files_to_remove.append(file_path)

            # Remove from pending before calling callback
            for file_path in files_to_remove:
                if file_path in self._pending:
                    del self._pending[file_path]

        # Call callback with ready files
        if ready_files and self.callback:
            try:
                self.callback(ready_files)
            except Exception as e:
                print(f"❌ Error in coalescer callback: {e}")

    def add_change(self, file_path: str) -> None:
        """Add a file change."""
        import time

        current_time = time.time()

        with self._lock:
            self._pending[file_path] = current_time

    def has_pending_files(self) -> bool:
        """Check if there are pending files."""
        with self._lock:
            return bool(self._pending)

    def force_batch(self) -> list[str]:
        """Force return all pending files for cleanup."""
        with self._lock:
            all_files = list(self._pending.keys())
            self._pending.clear()
        return all_files

    def should_process(self, file_path: str) -> bool:
        """Check if a file should be processed now."""
        import time

        current_time = time.time()

        with self._lock:
            last_change = self._pending.get(file_path, 0)
            return current_time - last_change >= self.delay

    def cleanup_old_entries(self, max_age: float = 300.0) -> None:
        """Remove old entries to prevent memory leaks."""
        import time

        current_time = time.time()
        cutoff_time = current_time - max_age

        with self._lock:
            self._pending = {
                path: timestamp
                for path, timestamp in self._pending.items()
                if timestamp >= cutoff_time
            }

    def stop(self) -> None:
        """Stop the timer thread."""
        self._stop_event.set()
        if self._timer_thread and self._timer_thread.is_alive():
            self._timer_thread.join(timeout=2.0)
