"""Async debouncing for file change events."""

import asyncio
import time
from typing import Dict, Any, Callable, Awaitable, Set, List
from pathlib import Path


class AsyncDebouncer:
    """Async debouncer with coalescing for file system events."""
    
    def __init__(self, delay: float = 2.0, max_batch_size: int = 100):
        self.delay = delay
        self.max_batch_size = max_batch_size
        
        # Track pending operations
        self._pending_files: Dict[str, float] = {}  # file_path -> last_update_time
        self._deleted_files: Set[str] = set()
        self._task: asyncio.Task = None
        self._callback: Callable[[Dict[str, Any]], Awaitable[None]] = None
        
        # Event loop management
        self._running = False
        self._queue = asyncio.Queue()
    
    def set_callback(self, callback: Callable[[Dict[str, Any]], Awaitable[None]]):
        """Set the callback function for processed events."""
        self._callback = callback
    
    async def start(self):
        """Start the debouncer task."""
        if self._running:
            return
        
        self._running = True
        self._task = asyncio.create_task(self._process_events())
    
    async def stop(self):
        """Stop the debouncer and process remaining events."""
        self._running = False
        
        if self._task:
            # Process any remaining events
            await self._flush_pending()
            
            # Cancel the task
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
    
    async def add_file_event(self, file_path: str, event_type: str):
        """Add a file change event to the debounce queue."""
        await self._queue.put({
            "file_path": file_path,
            "event_type": event_type,
            "timestamp": time.time()
        })
    
    async def _process_events(self):
        """Main event processing loop."""
        try:
            while self._running:
                try:
                    # Wait for events with timeout
                    event = await asyncio.wait_for(self._queue.get(), timeout=self.delay)
                    await self._handle_event(event)
                    
                    # Process batch if we have enough pending
                    if len(self._pending_files) >= self.max_batch_size:
                        await self._flush_pending()
                        
                except asyncio.TimeoutError:
                    # Timeout occurred, process pending events
                    if self._pending_files or self._deleted_files:
                        await self._flush_pending()
                
        except asyncio.CancelledError:
            # Process remaining events before stopping
            await self._flush_pending()
            raise
        except Exception as e:
            print(f"Error in debouncer: {e}")
    
    async def _handle_event(self, event: Dict[str, Any]):
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
    
    async def _flush_pending(self):
        """Process all pending events."""
        if not self._callback:
            return
        
        current_time = time.time()
        
        # Filter files that have been stable for the delay period
        stable_files = {
            path: timestamp for path, timestamp in self._pending_files.items()
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
                "timestamp": current_time
            }
            
            try:
                await self._callback(batch_event)
            except Exception as e:
                print(f"Error in debouncer callback: {e}")
            
            # Clear processed deletions
            self._deleted_files.clear()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get debouncer statistics."""
        return {
            "running": self._running,
            "pending_files": len(self._pending_files),
            "pending_deletions": len(self._deleted_files),
            "queue_size": self._queue.qsize(),
            "delay": self.delay,
            "max_batch_size": self.max_batch_size
        }


class FileChangeCoalescer:
    """Simple file change coalescer for synchronous use with batch processing support."""
    
    def __init__(self, delay: float = 2.0):
        self.delay = delay
        self._pending: Dict[str, float] = {}
        self._last_batch_check: float = 0
    
    def add_change(self, file_path: str) -> bool:
        """Add a file change. Returns True if batch should be checked (for backwards compatibility)."""
        current_time = time.time()
        
        # Always update the timestamp for this file
        self._pending[file_path] = current_time
        
        # Check if enough time has passed since last batch check
        # This prevents checking batch on every file but allows periodic checks
        if current_time - self._last_batch_check >= self.delay:
            self._last_batch_check = current_time
            return True
        
        return False
    
    def get_ready_batch(self) -> List[str]:
        """Get all files that are ready for processing and remove them from pending."""
        current_time = time.time()
        ready_files = []
        
        # Find all files that have passed the debounce timeout
        files_to_remove = []
        for file_path, timestamp in self._pending.items():
            if current_time - timestamp >= self.delay:
                ready_files.append(file_path)
                files_to_remove.append(file_path)
        
        # Remove processed files from pending
        for file_path in files_to_remove:
            del self._pending[file_path]
        
        return ready_files
    
    def has_pending_files(self) -> bool:
        """Check if there are any pending files."""
        return len(self._pending) > 0
    
    def force_batch(self) -> List[str]:
        """Force return all pending files regardless of timeout (for cleanup)."""
        ready_files = list(self._pending.keys())
        self._pending.clear()
        return ready_files
    
    def should_process(self, file_path: str) -> bool:
        """Check if a file should be processed now."""
        current_time = time.time()
        last_change = self._pending.get(file_path, 0)
        return current_time - last_change >= self.delay
    
    def cleanup_old_entries(self, max_age: float = 300.0):
        """Remove old entries to prevent memory leaks."""
        current_time = time.time()
        cutoff_time = current_time - max_age
        
        self._pending = {
            path: timestamp for path, timestamp in self._pending.items()
            if timestamp >= cutoff_time
        }