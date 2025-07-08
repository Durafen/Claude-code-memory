"""Shared file filtering utilities for watcher components."""

import fnmatch
from pathlib import Path
from typing import List


def should_process_file(file_path: Path, project_path: Path, 
                       include_patterns: List[str], exclude_patterns: List[str],
                       max_file_size: int = 1048576) -> bool:
    """Check if a file should be processed based on patterns and constraints.
    
    Args:
        file_path: Path to the file to check
        project_path: Root project path
        include_patterns: List of glob patterns to include (e.g., ['*.py', '*.md'])
        exclude_patterns: List of glob patterns to exclude (e.g., ['*.pyc', '.git'])
        max_file_size: Maximum file size in bytes (default: 1MB)
        
    Returns:
        True if file should be processed, False otherwise
    """
    try:
        # Check if file is within project
        try:
            file_path.relative_to(project_path)
        except ValueError:
            return False
        
        # Check include patterns
        if not matches_patterns(file_path.name, include_patterns):
            return False
        
        # Check exclude patterns
        if matches_patterns(str(file_path), exclude_patterns):
            return False
        
        # Check file size (for existing files)
        if file_path.exists() and file_path.is_file():
            if file_path.stat().st_size > max_file_size:
                return False
        
        return True
        
    except Exception:
        return False


def matches_patterns(text: str, patterns: List[str]) -> bool:
    """Check if text matches any pattern in the list.
    
    Args:
        text: Text to check against patterns
        patterns: List of glob patterns
        
    Returns:
        True if text matches any pattern, False otherwise
    """
    for pattern in patterns:
        if fnmatch.fnmatch(text, pattern) or pattern in text:
            return True
    return False