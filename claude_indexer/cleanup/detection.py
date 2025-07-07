"""
Dynamic field-based detection for manual vs auto-indexed entries.

This module implements the core detection logic that distinguishes between
manual entries (user-created insights) and auto-indexed entries (from code parsing).
Uses field structure rather than entity types for future-proof detection.
"""

from typing import Dict, Any, Set


def is_manual_entry(payload: Dict[str, Any]) -> bool:
    """
    Dynamic detection based on field presence, not entity type.
    This approach automatically adapts to ANY new entity type.
    
    Auto-indexed entries ALWAYS contain automation fields that are
    injected by the indexing pipeline. Manual entries only contain
    basic structure fields.
    
    Args:
        payload: The entry payload to analyze
        
    Returns:
        True if the entry is manual (user-created), False if auto-indexed
        
    Examples:
        >>> # Auto-indexed entry (has automation fields)
        >>> auto_entry = {
        ...     "entityType": "function",
        ...     "name": "calculate_sum", 
        ...     "content": "def calculate_sum()...",
        ...     "file_path": "/src/math.py",  # ← Automation field!
        ...     "line_number": 15             # ← Automation field!
        ... }
        >>> is_manual_entry(auto_entry)
        False
        
        >>> # Manual entry (no automation fields)
        >>> manual_entry = {
        ...     "entityType": "debugging_pattern",
        ...     "name": "Auth Token Validation", 
        ...     "content": "Always check token expiry..."
        ... }
        >>> is_manual_entry(manual_entry)
        True
    """
    # Auto-indexed entries ALWAYS contain automation fields
    # These fields are injected by the indexing pipeline
    # Use same logic as qdrant_stats.py for consistency
    # Pattern 1: Auto entities have file_path field
    if 'file_path' in payload:
        return False
    
    # Pattern 2: Auto relations have entity_name/relation_target/relation_type structure  
    if all(field in payload for field in ['entity_name', 'relation_target', 'relation_type']):
        return False
    
    # Pattern 3: Auto entities have extended metadata fields
    automation_fields = {
        'line_number', 'ast_data', 'signature', 'docstring', 'full_name', 
        'ast_type', 'start_line', 'end_line', 'source_hash', 'parsed_at',
    }
    
    if any(field in payload for field in automation_fields):
        return False
    
    # True manual entries have minimal fields: entity_name/name, entity_type/entityType, observations
    has_name = 'entity_name' in payload or 'name' in payload
    has_type = 'entity_type' in payload or 'entityType' in payload
    
    if not (has_name and has_type):
        return False
    
    # Additional check: Manual entries typically have meaningful content
    observations = payload.get('observations', [])
    content = payload.get('content', '')
    
    has_content = (isinstance(observations, list) and len(observations) > 0) or \
                  (isinstance(content, str) and len(content.strip()) > 0)
    
    if not has_content:
        return False
    
    return True


def get_automation_fields(payload: Dict[str, Any]) -> Set[str]:
    """
    Get list of automation fields present in the payload.
    
    Useful for debugging and understanding why an entry
    was classified as auto-indexed.
    
    Args:
        payload: The entry payload to analyze
        
    Returns:
        Set of automation field names found in the payload
    """
    automation_fields = {
        'file_path', 'line_number', 'ast_data', 'signature', 'docstring',
        'full_name', 'ast_type', 'start_line', 'end_line', 'source_hash',
        'parsed_at', 'is_chunk', 'chunk_index', 'chunk_type', 'has_implementation',
        'collection', 'scope', 'complexity', 'dependencies', 'language',
        'extension', 'module_path', 'class_name', 'namespace', 'imports',
        'exports', 'type_hints', 'decorators', 'return_type', 'parameters',
        'is_async', 'is_generator', 'is_property', 'visibility', 'inheritance',
        'interfaces', 'annotations', 'metadata'
    }
    
    return {field for field in automation_fields if field in payload}


def validate_manual_entry_structure(payload: Dict[str, Any]) -> bool:
    """
    Validate that a manual entry has the expected basic structure.
    
    Manual entries should have at minimum:
    - entityType: Category of the entry
    - name: Identifier for the entry  
    - content/observations: The actual information
    
    Args:
        payload: The entry payload to validate
        
    Returns:
        True if the entry has valid manual structure, False otherwise
    """
    # Check for required fields (handle both naming conventions)
    has_entity_type = 'entityType' in payload or 'entity_type' in payload
    has_name = 'name' in payload or 'entity_name' in payload
    
    if not (has_entity_type and has_name):
        return False
        
    # Must have at least one content field
    content_fields = {'content', 'observations'}
    if not any(field in payload for field in content_fields):
        return False
        
    return True


def classify_entry_type(payload: Dict[str, Any]) -> str:
    """
    Classify an entry for cleanup action.
    
    IMPORTANT: The return values indicate CLEANUP ACTIONS, not content type:
    - 'preserve' = Keep this entry (documentation, auto-indexed code)
    - 'clean' = Remove this entry (manual patterns, insights)
    - 'invalid' = Malformed entry
    
    Args:
        payload: The entry payload to classify
        
    Returns:
        String classification: 'preserve', 'clean', or 'invalid'
    """
    if not isinstance(payload, dict):
        return 'invalid'
    
    # Preserve documentation entities (don't clean them)
    entity_type = payload.get('entityType') or payload.get('entity_type', '')
    if entity_type == 'documentation':
        return 'preserve'
        
    if is_manual_entry(payload):
        if validate_manual_entry_structure(payload):
            return 'clean'  # Clean manual entries (except documentation)
        else:
            return 'invalid'
    else:
        return 'preserve'  # Preserve auto-indexed code