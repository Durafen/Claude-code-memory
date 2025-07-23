#!/usr/bin/env python3
"""
Smart watcher integration for test suite.
Replaces fixed delays with intelligent log monitoring.
"""
import re
import time
from pathlib import Path
from typing import Optional, List, Dict, Union

class SmartWatcherWait:
    """Intelligent watcher completion detection via log analysis."""
    
    def __init__(self, log_file: Union[str, Path], timeout: int = 60):
        self.log_file = Path(log_file)
        self.timeout = timeout
        self.completion_patterns = [
            r"Indexed \d+ files",
            r"Processing complete",
            r"Batch processed: \d+ files",
            r"Successfully processed.*files?", 
            r"Index update complete",
            r"Files processed successfully: \d+",
            r"Processing \d+ files.*complete",
            r"Watcher.*indexed.*files",
            r"File processing finished",
            r"All files processed",
        ]
        self.error_patterns = [
            r"ERROR",
            r"CRITICAL", 
            r"Failed to",
            r"Exception:",
            r"Traceback",
        ]
    
    def wait_for_watcher_completion(self, expected_files: Union[int, List[str]] = None, grace_period: int = 2) -> Dict:
        """
        Smart wait for watcher to complete processing files.
        
        Args:
            expected_files: List of filenames or number of files expected to be processed
            grace_period: Extra seconds after completion signal
            
        Returns:
            Dict with completion status and timing info
        """
        start_time = time.time()
        # Start reading from end of current log to avoid old completion signals
        last_position = self.log_file.stat().st_size if self.log_file.exists() else 0
        files_processed = 0
        completion_found = False
        errors_found = []
        activity_detected = False
        files_found = set()
        
        # Handle both list of filenames and number
        if isinstance(expected_files, list):
            expected_file_names = set(expected_files)
            expected_count = len(expected_files)
            print(f"🕐 Smart wait: Monitoring {self.log_file} for specific files...")
            print(f"   Expected files: {expected_files}")
        else:
            expected_file_names = None
            expected_count = expected_files
            print(f"🕐 Smart wait: Monitoring {self.log_file} for completion signals...")
            print(f"   Expected files: {expected_files if expected_files else 'any'}")
        print(f"   Timeout: {self.timeout}s, Grace period: {grace_period}s")
        
        while time.time() - start_time < self.timeout:
            if not self.log_file.exists():
                if time.time() - start_time > 5:  # Give more time for log file creation
                    print(f"⏰ Log file {self.log_file} not found after 5s, continuing to wait...")
                time.sleep(0.5)
                continue
                
            # Read new log content since last check
            try:
                with open(self.log_file, 'r') as f:
                    f.seek(last_position)
                    new_content = f.read()
                    last_position = f.tell()
                    
                if new_content:
                    activity_detected = True
                    lines = new_content.split('\n')
                    for line in lines:
                        if not line.strip():
                            continue
                            
                        # Check for batch processing patterns first
                        batch_match = re.search(r'🔄 Auto-indexing batch \((\d+) files\): (.+)', line)
                        if batch_match:
                            file_count = int(batch_match.group(1))
                            file_list = batch_match.group(2)
                            print(f"📦 Batch detected: {file_count} files - {file_list}")
                            
                            # Extract actual filenames from batch
                            batch_files = [f.strip() for f in file_list.split(',')]
                            for filename in batch_files:
                                files_found.add(filename)
                                print(f"📁 File found: {filename}")
                        
                        # Check for completion patterns
                        for pattern in self.completion_patterns:
                            match = re.search(pattern, line, re.IGNORECASE)
                            if match:
                                print(f"✅ Completion signal detected: {line.strip()}")
                                completion_found = True
                                
                                # Extract file count if mentioned
                                file_count_match = re.search(r'(\d+)\s+files?', line, re.IGNORECASE)
                                if file_count_match:
                                    files_processed = int(file_count_match.group(1))
                                    print(f"📊 Files processed: {files_processed}")
                        
                        # Also look for individual file processing
                        file_processing_match = re.search(r'(?:Processing|Indexing).*?([a-zA-Z_][a-zA-Z0-9_]*\.py)', line, re.IGNORECASE)
                        if file_processing_match:
                            filename = file_processing_match.group(1)
                            files_found.add(filename)
                            print(f"📁 File activity: {filename}")
                        
                        # Check for errors
                        for pattern in self.error_patterns:
                            if re.search(pattern, line, re.IGNORECASE):
                                errors_found.append(line.strip())
                                print(f"⚠️ Error detected: {line.strip()}")
                
                # Check completion conditions
                if completion_found:
                    if expected_file_names:
                        # Check if all expected files were found
                        if expected_file_names.issubset(files_found):
                            print(f"🎯 All expected files processed! Files: {files_found}, Grace period: {grace_period}s")
                            time.sleep(grace_period)
                            break
                        else:
                            missing = expected_file_names - files_found
                            print(f"⏳ Waiting for files: {missing}")
                            completion_found = False  # Reset for continued waiting
                    elif expected_count is None or files_processed >= expected_count:
                        print(f"🎯 Processing complete! Files: {files_processed}, Grace period: {grace_period}s")
                        time.sleep(grace_period)
                        break
                    else:
                        print(f"⏳ Partial completion: {files_processed}/{expected_count} files")
                        completion_found = False  # Reset for continued waiting
                
            except Exception as e:
                print(f"⚠️ Error reading log: {e}")
            
            time.sleep(0.3)  # Check every 300ms
        
        elapsed = time.time() - start_time
        
        result = {
            'completed': completion_found,
            'files_processed': files_processed,
            'files_found': list(files_found),
            'elapsed_time': elapsed,
            'errors': errors_found,
            'timed_out': elapsed >= self.timeout,
            'activity_detected': activity_detected
        }
        
        if result['timed_out']:
            print(f"⏰ Timeout after {elapsed:.1f}s (activity: {activity_detected})")
        else:
            print(f"✅ Smart wait completed in {elapsed:.1f}s")
            
        return result