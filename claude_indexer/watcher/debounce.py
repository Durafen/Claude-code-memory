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
    """Simple file change coalescer with background timer for automatic processing."""
    
    def __init__(self, delay: float = 2.0):
        import threading
        import time
        
        self.delay = delay
        self._pending: Dict[str, float] = {}
        self._ready_batches: List[List[str]] = []
        self._lock = threading.Lock()
        self._timer_thread = None
        self._stop_event = threading.Event()
        self._start_timer()
    
    def _start_timer(self):
        """Start background timer thread to automatically process files."""
        import threading
        if self._timer_thread is None or not self._timer_thread.is_alive():
            self._timer_thread = threading.Thread(target=self._timer_loop, daemon=True)
            self._timer_thread.start()
    
    def _timer_loop(self):
        """Background timer that periodically checks for ready files."""
        import time
        
        while not self._stop_event.is_set():
            try:
                time.sleep(self.delay)
                self._check_and_move_ready_files()
            except Exception as e:
                print(f"❌ Timer error: {e}")
    
    def _check_and_move_ready_files(self):
        """Check pending files and move ready ones to batch queue."""
        import time
        current_time = time.time()
        
        with self._lock:
            ready_files = []
            files_to_remove = []
            
            # Find files ready for processing
            for file_path, timestamp in self._pending.items():
                if current_time - timestamp >= self.delay:
                    ready_files.append(file_path)
                    files_to_remove.append(file_path)
            
            # Move ready files to batch queue
            if ready_files:
                self._ready_batches.append(ready_files)
            
            # Remove from pending
            for file_path in files_to_remove:
                del self._pending[file_path]
    
    def add_change(self, file_path: str) -> bool:
        """Add a file change. Returns True if batch should be checked."""
        import time
        current_time = time.time()
        
        with self._lock:
            self._pending[file_path] = current_time
            # Return True if we have ready batches waiting
            return len(self._ready_batches) > 0
    
    def get_ready_batch(self) -> List[str]:
        """Get next ready batch from timer-processed queue."""
        with self._lock:
            if self._ready_batches:
                return self._ready_batches.pop(0)
        return []
    
    def has_pending_files(self) -> bool:
        """Check if there are pending files or ready batches."""
        with self._lock:
            return len(self._pending) > 0 or len(self._ready_batches) > 0
    
    def force_batch(self) -> List[str]:
        """Force return all pending files for cleanup."""
        with self._lock:
            all_files = list(self._pending.keys())
            
            # Add files from ready batches
            for batch in self._ready_batches:
                all_files.extend(batch)
            
            # Clear everything
            self._pending.clear()
            self._ready_batches.clear()
            
        return all_files
    
    def should_process(self, file_path: str) -> bool:
        """Check if a file should be processed now."""
        import time
        current_time = time.time()
        
        with self._lock:
            last_change = self._pending.get(file_path, 0)
            return current_time - last_change >= self.delay
    
    def cleanup_old_entries(self, max_age: float = 300.0):
        """Remove old entries to prevent memory leaks."""
        import time
        current_time = time.time()
        cutoff_time = current_time - max_age
        
        with self._lock:
            self._pending = {
                path: timestamp for path, timestamp in self._pending.items()
                if timestamp >= cutoff_time
            }
    
    def stop(self):
        """Stop the timer thread."""
        self._stop_event.set()
        if self._timer_thread and self._timer_thread.is_alive():
            self._timer_thread.join(timeout=2.0)