# COMPREHENSIVE TEST PROTOCOL

## EXECUTION NOTE
**CRITICAL:** Follow each stage exactly as written in sequence. Do NOT create separate todo systems or task breakdowns. Execute the protocol step-by-step as documented. The failure to follow documented procedures leads to incomplete testing and missed verification steps.

## 0. Clear the directory and database:
```bash
rm -rf debug/isolated_test/*
claude-indexer index -p debug/isolated_test -c parser-test --clear-all
```

## 1. Create comprehensive test files with cross-file relations:

### Main test file (MODIFIED during testing):
```python
# debug/isolated_test/test_complex.py
import os
import json
from pathlib import Path

class UserManager:
    """Manages user authentication and sessions."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.sessions = {}

    def authenticate_user(self, username: str, password: str):
        """Authenticate user with credentials."""
        if self.validate_credentials(username, password):
            return self.create_session(username)
        return None

    def validate_credentials(self, username: str, password: str) -> bool:
        """Validate user credentials against database."""
        return load_user_data(username, self.db_path) is not None

def load_user_data(username: str, db_path: str):
    """Load user data from database file."""
    try:
        with open(db_path, 'r') as f:
            data = json.load(f)
        return data.get(username)
    except FileNotFoundError:
        return None

def create_session(username: str):
    """Create user session after authentication."""
    return {"user": username, "token": "abc123"}

# Global variable
DEFAULT_DB_PATH = "/tmp/users.db"
```

### Stable reference file (UNCHANGED throughout all test stages):
```python
# debug/isolated_test/utils.py
import hashlib
import time
from test_complex import UserManager, DEFAULT_DB_PATH

def hash_password(password: str) -> str:
    """Hash password with salt for secure storage."""
    salt = "secure_salt_2024"
    return hashlib.sha256((password + salt).encode()).hexdigest()

def validate_session_token(token: str) -> bool:
    """Validate session token format and expiry."""
    if not token or len(token) < 10:
        return False
    return token.startswith(("abc", "xyz", "legacy"))

def create_user_manager(db_path: str = None) -> UserManager:
    """Factory function to create UserManager instances."""
    path = db_path or DEFAULT_DB_PATH
    return UserManager(path, "DEBUG")

class SessionStore:
    """Persistent session storage handler."""

    def __init__(self, manager: UserManager):
        self.manager = manager
        self.active_sessions = {}
        self.created_at = time.time()

    def store_session(self, session_data: dict) -> str:
        """Store session and return session ID."""
        session_id = f"session_{len(self.active_sessions)}_{int(time.time())}"
        self.active_sessions[session_id] = {
            **session_data,
            "stored_at": time.time(),
            "manager_id": id(self.manager)
        }
        return session_id

    def cleanup_expired(self, max_age: int = 3600) -> int:
        """Remove expired sessions, return count cleaned."""
        current_time = time.time()
        expired = [
            sid for sid, data in self.active_sessions.items()
            if current_time - data["stored_at"] > max_age
        ]
        for sid in expired:
            del self.active_sessions[sid]
        return len(expired)

# Module constants
MAX_PASSWORD_LENGTH = 128
DEFAULT_SESSION_TIMEOUT = 1800
SUPPORTED_HASH_ALGORITHMS = ["sha256", "bcrypt", "argon2"]
```

## 2. Initial index:
```bash
claude-indexer index -p debug/isolated_test -c parser-test --verbose
```

## 3. Verify initial state using MCP (both files indexed):
```python
# Verify ALL entities from both files (must match source code exactly)
mcp__parser-test-memory__search_similar("UserManager authenticate_user validate_credentials load_user_data create_session hash_password validate_session_token create_user_manager SessionStore", entityTypes=["function", "class", "implementation", "metadata"])

# CRITICAL: Per-file verification (catch missing file entities)
mcp__parser-test-memory__search_similar("test_complex.py UserManager authenticate_user validate_credentials", entityTypes=["implementation", "metadata"])
mcp__parser-test-memory__search_similar("utils.py hash_password SessionStore create_user_manager", entityTypes=["implementation", "metadata"])

# Verify ALL relations (must match actual imports/calls in source)
mcp__parser-test-memory__search_similar("from test_complex import UserManager DEFAULT_DB_PATH import os json pathlib hashlib time", entityTypes=["relation"])

# MANDATORY: Verify each implementation chunk matches source line-by-line
# MANDATORY: Verify each relation exists in actual source imports/calls - no orphans
# CRITICAL: Compare EVERY chunk character-by-character vs source - 100% match required - NO STALE DATA
# CRITICAL: Verify metadata reflects current source signatures exactly - NO STALE DATA
# CRITICAL: Every relation must exist in actual source code - verify imports/calls line-by-line - NO STALE DATA
```

### Expected Results Stage 3:
- **Expected:** All entities from BOTH files found with correct metadata, implementation chunks match source code exactly, cross-file relationships properly established
- **Cross-file Relations:** `utils.py` imports `UserManager` and `DEFAULT_DB_PATH` from `test_complex.py`, `create_user_manager` calls `UserManager`, `SessionStore.__init__` takes `UserManager` parameter
- **AI Verification:** Compare retrieved implementation chunks with original source files to ensure 100% accuracy, verify cross-file imports indexed correctly, confirm all expected entities/relations exist from both files
- **Summary:** Baseline verification complete - all entities indexed correctly from both files with accurate code content, cross-file dependencies established, no duplicates or leftovers

#### Systematic Verification Checklist Stage 3:
□ **Manual count verification**: Count source functions/classes vs DB metadata/implementation/relations - numbers must match exactly
□ **Metadata verification**: All expected entities (8 functions, 2 classes, 3 variables) found with correct names/types
□ **Implementation verification**: Code chunks match source files exactly character-by-character
□ **Relations verification**: Cross-file imports and internal calls properly indexed
□ **Duplicate detection**: Only one version of each entity exists (CRITICAL)
□ **Data integrity**: Complete initial baseline established, no missing or corrupted entries

## 4. Modify functions (add type annotations, change logic, add new method):
```python
# debug/isolated_test/test_complex.py
import os
import json
import logging
from pathlib import Path

class UserManager:
    """Enhanced user authentication and session management."""

    def __init__(self, db_path: str, log_level: str = "INFO"):
        self.db_path = db_path
        self.sessions = {}
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(log_level)

    def authenticate_user(self, username: str, password: str) -> dict | None:
        """Authenticate user with enhanced logging."""
        self.logger.info(f"Authentication attempt for {username}")
        if self.validate_credentials(username, password):
            return self.create_session(username)
        return None

    def validate_credentials(self, username: str, password: str) -> bool:
        """Enhanced validation with caching."""
        # Simplified validation - removed load_user_data dependency
        if username and password:
            self.logger.info(f"Valid credentials for {username}")
            return True
        return False

    def create_session(self, username: str) -> dict:
        """Create enhanced user session."""
        session_id = f"sess_{len(self.sessions)}_{username}"
        session = {
            "user": username,
            "token": "xyz789",
            "session_id": session_id,
            "created_at": 1234567890
        }
        self.sessions[session_id] = session
        return session

def load_user_data(username: str, db_path: str) -> dict | None:
    """Enhanced user data loading with error handling."""
    try:
        path = Path(db_path)
        if not path.exists():
            return None
        with open(path, 'r') as f:
            data = json.load(f)
        return data.get(username)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logging.error(f"Error loading user data: {e}")
        return None

def create_session(username: str) -> dict:
    """Legacy session creation function."""
    return {"user": username, "token": "legacy_token"}

# Updated global variables
DEFAULT_DB_PATH = "/var/lib/users.db"
MAX_SESSIONS = 100
```

## 5. Re-index (incremental):
```bash
claude-indexer index -p debug/isolated_test -c parser-test --verbose
```

### Expected Results Stage 5:
- **Expected:** Incremental update detects changes, REPLACES existing entities with updated versions, removes old versions completely
- **Changes:** Added logging import, enhanced methods with type annotations, new variables, modified function logic
- **AI Verification:** CRITICAL BUG CHECK - Verify ONLY current code versions exist, NO OLD VERSIONS should remain. Search for duplicate function implementations - if found, incremental update is BROKEN
- **Summary:** **CRITICAL:** Only current code versions should exist. Any duplicate old/new versions indicates incremental update failure requiring immediate fix

## 6. Comprehensive verification using MCP:

### Check all implementation chunks:
```python
# Verify ALL implementation chunks match current source exactly
mcp__parser-test-memory__search_similar("implementation", entityTypes=["implementation"])

# CRITICAL: Duplicate detection - search for main functions individually
mcp__parser-test-memory__search_similar("UserManager authenticate_user validate_credentials", entityTypes=["function", "implementation", "metadata"])

# Verify ALL relations match actual imports/calls in updated source
mcp__parser-test-memory__search_similar("import logging calls UserManager", entityTypes=["relation"])

# Verify entity counts match source files exactly
mcp__parser-test-memory__read_graph(entityTypes=["function", "class"], limit=200)

# MANDATORY: Compare each result vs source - implementation chunks line-by-line, relations vs imports - no orphans
# CRITICAL: Every implementation chunk must match source 100% character-by-character - NO STALE DATA
# CRITICAL: Every metadata entry must reflect current source function signatures exactly - NO STALE DATA
# CRITICAL: Every relation must correspond to actual import/call in current source - NO STALE DATA
```

### Expected Results Stage 6:
- **Expected:** All implementation chunks reflect ONLY updated code, metadata shows enhanced functionality, new logging relations established
- **AI Verification:** **CRITICAL BUG DETECTION:** Cross-reference each retrieved implementation chunk against current source file for exact code match, scan for any duplicate function/class entries, verify old outdated chunks COMPLETELY REMOVED
- **Summary:** **CRITICAL:** Comprehensive verification must confirm NO DUPLICATE VERSIONS exist. Any old versions remaining indicates incremental update system is fundamentally broken

#### Systematic Verification Checklist Stage 6:
□ **Manual count verification**: Count source functions/classes vs DB metadata/implementation/relations - numbers must match exactly
□ **Metadata verification**: Enhanced signatures/types updated, new logging features reflected
□ **Implementation verification**: Updated code chunks match modified source exactly
□ **Relations verification**: New import logging relation added, existing relations preserved
□ **Duplicate detection**: NO old/new versions coexist - ONLY current versions (CRITICAL)
□ **Data integrity**: Incremental update replaced entities cleanly, no orphaned old data

## 7. Entity deletion test - remove function and verify cleanup:
```python
# Remove load_user_data function entirely from debug/isolated_test/test_complex.py
# Keep everything else, just delete this function:
def load_user_data(username: str, db_path: str) -> dict | None:
    """Enhanced user data loading with error handling."""
    try:
        path = Path(db_path)
        if not path.exists():
            return None
        with open(path, 'r') as f:
            data = json.load(f)
        return data.get(username)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logging.error(f"Error loading user data: {e}")
        return None
```

## 8. Re-index after deletion:
```bash
claude-indexer index -p debug/isolated_test -c parser-test --verbose
```

### Expected Results Stage 8:
- **Expected:** Deleted function completely removed from database, orphaned relations cleaned up
- **Changes:** load_user_data function removed, references updated to remove broken calls, related CALLS relations should be deleted
- **AI Verification:** Confirm deleted entity cannot be retrieved and source file no longer contains the function or references to it, ensure no duplicate deletion entries or leftover metadata chunks, verify remaining entities/relations intact
- **Summary:** Entity deletion and orphan cleanup working correctly, no residual data or duplicates, no broken function references

## 9. Verify entity deletion and relation cleanup:
```python
# Verify load_user_data COMPLETELY ABSENT (must match source - function deleted)
mcp__parser-test-memory__search_similar("load_user_data calls load_user_data", entityTypes=["function", "implementation", "metadata", "relation"])

# CRITICAL: Individual entity survival verification (catch over-deletion)
mcp__parser-test-memory__search_similar("validate_credentials authenticate_user", entityTypes=["function", "implementation"])
mcp__parser-test-memory__search_similar("UserManager create_session", entityTypes=["function", "class", "implementation"])

# Verify ALL remaining entities match current source files exactly
mcp__parser-test-memory__search_similar("UserManager authenticate_user validate_credentials create_session", entityTypes=["function", "class", "metadata"])

# MANDATORY: Check each remaining function exists in source with correct implementation - no orphan relations
# CRITICAL: Every surviving chunk must match current source 100% - NO STALE DATA
# CRITICAL: Metadata must show current signatures - validate_credentials without load_user_data calls - NO STALE DATA
# CRITICAL: Every relation must exist in current source - no phantom imports/calls - NO STALE DATA
```

### Expected Results Stage 9:
- **Expected:** Zero results for load_user_data searches, no orphaned relations, remaining entities unchanged, validate_credentials shows updated implementation without load_user_data calls
- **AI Verification:** **CRITICAL** - Verify ONLY load_user_data deleted while ALL OTHER 5 functions (UserManager class, authenticate_user, validate_credentials, create_session methods) remain intact with their implementation chunks. Confirm validate_credentials implementation shows simplified logic without broken function calls. **MAJOR BUG** if all entities missing - deletion process over-deleting everything instead of target entity only.
- **Summary:** Complete deletion verification - entity removal and cleanup successful, data integrity maintained, no leftovers or duplicates detected, no broken function references in remaining code

#### Systematic Verification Checklist Stage 9:
□ **Manual count verification**: Count source functions/classes vs DB metadata/implementation/relations - numbers must match exactly
□ **Metadata verification**: load_user_data completely absent, other entities retain correct metadata
□ **Implementation verification**: load_user_data has zero results, remaining chunks match current source
□ **Relations verification**: load_user_data relations deleted, no orphaned relations to deleted entity
□ **Duplicate detection**: No duplicate deletion entries, only single current versions (CRITICAL)
□ **Data integrity**: Selective deletion succeeded, remaining entities intact and uncorrupted

## Test Coverage

This comprehensive test verifies:
- Function modification handling
- Class method updates
- Import statement changes
- Entity deletion and cleanup
- Metadata and implementation chunk management
- Relation preservation and updates
- Orphaned relation cleanup after entity removal
- **Version replacement:** Incremental updates must REPLACE old versions completely
- **Stale data cleanup:** NO old versions should remain after updates
- **Duplicate prevention:** Only ONE version of each function/class should exist
- **Data integrity:** Only current code versions stored, old versions properly cleaned