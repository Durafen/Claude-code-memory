#!/usr/bin/env python3
"""
Memory Guard - Comprehensive Code Quality Gate for Claude Code
Prevents duplication, ensures logic completeness, maintains flow integrity, and preserves features.

Claude Code Hook Response Schema:
{
  "continue": "boolean (optional)",
  "suppressOutput": "boolean (optional)",
  "stopReason": "string (optional)",
  "decision": "\"approve\" | \"block\" (optional)",
  "reason": "string (optional)"
}
"""

import json
import os
import re
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from utils.code_analyzer import CodeAnalyzer
except ImportError:
    # Fallback for when run directly
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from utils.code_analyzer import CodeAnalyzer

# Configuration
DEBUG_ENABLED = os.getenv("MEMORY_GUARD_DEBUG", "true").lower() == "true"


class BypassManager:
    """Manages Memory Guard bypass state with simple on/off commands."""

    def __init__(self, project_root: Path | None = None):
        self.project_root = project_root or Path.cwd()
        self.state_file = self.project_root / ".claude" / "guard_state.json"
        self.lock = threading.Lock()

        # Ensure .claude directory exists
        self.state_file.parent.mkdir(exist_ok=True)


    def set_global_state(self, disabled: bool) -> str:
        """Enable or disable Memory Guard globally."""
        try:
            with self.lock:
                state = {"global_disabled": disabled}
                self.state_file.write_text(json.dumps(state, indent=2))

                if disabled:
                    return "🔴 Memory Guard disabled globally"
                else:
                    return "🟢 Memory Guard enabled globally"
        except Exception as e:
            return f"❌ Error setting global guard state: {str(e)}"

    def get_global_status(self) -> str:
        """Get current global Memory Guard status."""
        try:
            is_disabled = self.is_global_disabled()
            if is_disabled:
                return "📊 Memory Guard Status: 🔴 DISABLED GLOBALLY (use 'dups on' to enable)"
            else:
                return "📊 Memory Guard Status: 🟢 ENABLED GLOBALLY (use 'dups off' to disable)"
        except Exception:
            return "📊 Memory Guard Status: 🟢 ENABLED (default)"

    def is_global_disabled(self) -> bool:
        """Check if Memory Guard is disabled globally."""
        try:
            if not self.state_file.exists():
                return False

            with self.lock:
                state = json.loads(self.state_file.read_text())
                return state.get("global_disabled", False)
        except Exception:
            return False


class EntityExtractor:
    """Extract entities from code content."""

    def extract_entities_from_operation(
        self, tool_name: str, tool_input: dict[str, Any]
    ) -> list[str]:
        """Extract entity names from Write/Edit operations."""
        entities = []

        if tool_name == "Write":
            content = tool_input.get("content", "")
            entities.extend(self._extract_python_entities(content))

        elif tool_name == "Edit":
            new_string = tool_input.get("new_string", "")
            entities.extend(self._extract_python_entities(new_string))

        elif tool_name == "MultiEdit":
            edits = tool_input.get("edits", [])
            for edit in edits:
                new_string = edit.get("new_string", "")
                entities.extend(self._extract_python_entities(new_string))

        return entities

    def _extract_python_entities(self, content: str) -> list[str]:
        """Extract Python function and class names."""
        entities = []

        # Function patterns
        func_pattern = r"^def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\("
        for match in re.finditer(func_pattern, content, re.MULTILINE):
            entities.append(match.group(1))

        # Class patterns
        class_pattern = r"^class\s+([a-zA-Z_][a-zA-Z0-9_]*)"
        for match in re.finditer(class_pattern, content, re.MULTILINE):
            entities.append(match.group(1))

        return entities


class MemoryGuard:
    """Comprehensive code quality gate - checks duplication, logic, flow integrity, and feature preservation."""

    def __init__(self, hook_data: dict[str, Any] | None = None):
        self.extractor = EntityExtractor()
        self.code_analyzer = CodeAnalyzer()
        
        # Early project detection using hook data or current directory
        self.project_root = None
        self.project_name = "unknown"
        self.mcp_collection = "mcp__project-memory__"
        self.bypass_manager = None
        self.current_debug_log = None  # Selected once per hook execution for proper rotation
        
        # Attempt early project detection
        self._early_project_detection(hook_data)
        
        # Ensure all three debug log files exist (only if debug is enabled)
        if DEBUG_ENABLED:
            self._ensure_debug_files_exist()

    def _early_project_detection(self, hook_data: dict[str, Any] | None = None) -> None:
        """Attempt early project detection from hook data or current directory."""
        try:
            file_path = None
            
            # Try to get file path from hook data
            if hook_data:
                tool_input = hook_data.get("tool_input", {})
                file_path = tool_input.get("file_path", "")
                
                # Also try working directory from hook data
                if not file_path:
                    file_path = hook_data.get("cwd", "")
            
            # Detect project root
            detected_root = self._detect_project_root(file_path if file_path else None)
            
            if detected_root:
                self.project_root = detected_root
                self.project_name = detected_root.name
                self.mcp_collection = self._detect_mcp_collection()
                
                # Initialize bypass manager early
                self.bypass_manager = BypassManager(self.project_root)
            else:
                # Fallback: try to detect from current working directory
                self.project_root = self._detect_project_root()
                if self.project_root:
                    self.project_name = self.project_root.name
                    self.mcp_collection = self._detect_mcp_collection()
                    self.bypass_manager = BypassManager(self.project_root)
                    
        except Exception:
            # If detection fails, we'll retry later in process_hook
            pass

    def _detect_project_root(self, file_path: str | None = None) -> Path | None:
        """Detect the project root directory using Claude-first weighted scoring."""
        try:
            marker_weights = {
                "CLAUDE.md": 100,      # Strongest: Claude project marker
                ".claude": 90,         # Second: Claude config directory  
                ".git": 80,            # Third: Git repository
                "pyproject.toml": 70,  # Python project
                "package.json": 60,    # Node.js project
                "setup.py": 50,        # Legacy Python
                "Cargo.toml": 40,      # Rust project
                "go.mod": 30,          # Go project
            }
            
            # Start from target file's directory if provided, otherwise current working directory
            if file_path:
                current = Path(file_path).resolve().parent
            else:
                current = Path.cwd()
            
            best_score = 0
            best_path = None
            
            # Traverse upward, score each directory
            while current != current.parent:
                score = sum(weight for marker, weight in marker_weights.items() 
                           if (current / marker).exists())
                
                if score > best_score:
                    best_score = score
                    best_path = current
                    
                current = current.parent
            
            return best_path or Path.cwd()
            
        except Exception:
            return None

    def _detect_mcp_collection(self) -> str:
        """Detect the MCP collection name for this project."""
        if self.project_root:
            # Check for CLAUDE.md file with MCP instructions
            claude_md = self.project_root / "CLAUDE.md"
            if claude_md.exists():
                try:
                    content = claude_md.read_text()
                    # Look for MCP collection pattern (captures collection names with underscores and hyphens)
                    match = re.search(r"mcp__(.+?)-memory__", content)
                    if match:
                        return f"mcp__{match.group(1)}-memory__"
                except Exception:
                    pass

            # Default to project name based collection
            safe_name = re.sub(r"[^a-zA-Z0-9-]", "-", self.project_name.lower())
            return f"mcp__{safe_name}-memory__"

        return "mcp__project-memory__"

    def save_debug_info(
        self, content: str, mode: str = "a", timestamp: bool = False
    ) -> None:
        """Save debug information to last updated log file (keeps 3 files)."""
        # EMERGENCY DEBUG - always write to tmp regardless of DEBUG_ENABLED
        # try:
        #     with open("/tmp/memory_guard_debug.log", "a") as f:
        #         f.write(f"SAVE_DEBUG_INFO CALLED: mode={mode}, content_len={len(content)}, project_root={self.project_root}\n")
        # except:
        #     pass
            
        if not DEBUG_ENABLED:
            return
        try:
            if timestamp:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                content = f"[{ts}] {content}"

            # Select log file once per hook execution for proper rotation
            base_dir = self.project_root if self.project_root else Path.cwd()
            if self.current_debug_log is None:
                # First call in this hook execution - select oldest file for rotation
                self.current_debug_log = self._get_current_debug_log(base_dir, True)
            current_log = self.current_debug_log

            with open(current_log, mode) as f:
                f.write(content)
        except Exception as e:
            # Write error to fallback file we can check
            try:
                # Try project logs first, then /tmp
                error_log = base_dir / "logs" / "memory_guard_error.log"
                error_log.parent.mkdir(exist_ok=True)
                with open(error_log, "a") as f:
                    f.write(f"ERROR: {e}\nPATH: {current_log}\nROOT: {base_dir}\n\n")
            except:
                try:
                    with open("/tmp/memory_guard_error.log", "a") as f:
                        f.write(f"ERROR: {e}\nPATH: {current_log}\nROOT: {base_dir}\n\n")
                except:
                    pass

    def _get_current_debug_log(self, base_dir: Path, is_new_run: bool) -> Path:
        """Get the current debug log file to use."""
        try:
            log_files = [
                base_dir / "logs" / "memory_guard_1.log",
                base_dir / "logs" / "memory_guard_2.log",
                base_dir / "logs" / "memory_guard_3.log",
            ]

            if is_new_run:
                # For new runs, find least recently updated file
                existing_files = [f for f in log_files if f.exists()]
                if not existing_files:
                    return log_files[0]  # Use first file if none exist

                # Find oldest file by modification time
                oldest = min(existing_files, key=lambda f: f.stat().st_mtime)
                return oldest
            else:
                # For same run, find most recently updated file
                existing_files = [f for f in log_files if f.exists()]
                if not existing_files:
                    return log_files[0]  # Use first file if none exist

                # Find newest file by modification time
                newest = max(existing_files, key=lambda f: f.stat().st_mtime)
                return newest

        except Exception:
            return base_dir / "logs" / "memory_guard_1.log"  # Fallback

    def _ensure_debug_files_exist(self) -> None:
        """Create all three debug log files if they don't exist."""
        try:
            base_dir = self.project_root if self.project_root else Path.cwd()
            log_files = [
                base_dir / "logs" / "memory_guard_1.log",
                base_dir / "logs" / "memory_guard_2.log",
                base_dir / "logs" / "memory_guard_3.log",
            ]

            # Ensure logs directory exists
            logs_dir = base_dir / "logs"
            logs_dir.mkdir(exist_ok=True)

            for log_file in log_files:
                if not log_file.exists():
                    # Create the file with a header
                    log_file.touch()
                    with open(log_file, "w") as f:
                        f.write(f"# Memory Guard Log - {log_file.name}\n")
                        f.write(
                            f"# Created: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                        )
                        f.write("# This file logs Memory Guard analysis results\n\n")
        except Exception:
            # Silently fail if we can't create files (permissions issue, etc.)
            pass

    def should_process(self, hook_data: dict[str, Any]) -> tuple[bool, str | None]:
        """Determine if this hook event should be processed."""
        # Check global bypass state first
        if self.bypass_manager.is_global_disabled():
            return False, "🔴 Memory Guard bypass active globally (use 'dups on' to re-enable)"

        tool_name = hook_data.get("tool_name", "")
        hook_event = hook_data.get("hook_event_name", "")
        tool_input = hook_data.get("tool_input", {})
        file_path = tool_input.get("file_path", "")

        # Check event type and tool
        if hook_event != "PreToolUse" or tool_name not in [
            "Write",
            "Edit",
            "MultiEdit",
        ]:
            return False, "Not a relevant operation"

        # Skip documentation and config files
        if file_path:
            file_ext = Path(file_path).suffix.lower()
            skip_extensions = {
                ".md",
                ".txt",
                ".json",
                ".yml",
                ".yaml",
                ".rst",
                ".xml",
            }
            if file_ext in skip_extensions:
                return (
                    False,
                    f"Skipping {file_ext} file - no duplicate checking for documentation/config",
                )

        # Check if within project directory
        if not file_path or not self.project_root:
            return False, f"Outside {self.project_name} project - no duplicate checking"

        try:
            # Check if file is within project root
            file_path_obj = Path(file_path).resolve()
            if not file_path_obj.is_relative_to(self.project_root):
                return (
                    False,
                    f"Outside {self.project_name} project - no duplicate checking",
                )
        except Exception:
            return False, "Invalid file path"

        return True, None

    def check_for_override_comments(self, code_content: str) -> tuple[bool, str]:
        """Check if code contains override comments to allow duplicates."""
        override_patterns = [
            r"#\s*@allow-duplicate(?:\s*:\s*(.+))?",  # Python: # @allow-duplicate: reason
            r"//\s*@allow-duplicate(?:\s*:\s*(.+))?",  # JS/TS/Java: // @allow-duplicate: reason
            r"/\*\s*@allow-duplicate(?:\s*:\s*(.+))?\s*\*/",  # Block: /* @allow-duplicate: reason */
            r"#\s*MEMORY_GUARD_ALLOW(?:\s*:\s*(.+))?",  # Alternative: # MEMORY_GUARD_ALLOW: reason
            r"//\s*MEMORY_GUARD_ALLOW(?:\s*:\s*(.+))?",  # Alternative: // MEMORY_GUARD_ALLOW: reason
        ]

        for pattern in override_patterns:
            match = re.search(pattern, code_content, re.IGNORECASE | re.MULTILINE)
            if match:
                reason = (
                    match.group(1) if match.group(1) else "Override comment detected"
                )
                return True, reason.strip()

        return False, ""

    def is_trivial_operation(self, code_info: str) -> tuple[bool, str]:
        """Check if operation is trivial and should skip guard analysis."""
        analysis = self.code_analyzer.analyze_code(code_info)
        return analysis["is_trivial"], analysis["reason"]

    def has_new_definitions(self, code_info: str) -> bool:
        """Check if code contains NEW function or class definitions."""
        analysis = self.code_analyzer.analyze_code(code_info)
        return analysis["has_definitions"]

    def get_code_info(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        """Extract code information from the operation."""
        if tool_name == "Write":
            content = tool_input.get("content", "")
            lines = content.split("\n")
            return f"NEW FILE CONTENT ({len(lines)} lines):\n```\n{content}\n```"

        elif tool_name == "Edit":
            old_string = tool_input.get("old_string", "")
            new_string = tool_input.get("new_string", "")
            old_lines = len(old_string.split("\n"))
            new_lines = len(new_string.split("\n"))

            # Add line number context for better AI analysis
            line_info = ""
            add_line_info = ""
            file_path = tool_input.get("file_path", "")
            if file_path and Path(file_path).exists():
                try:
                    with open(file_path) as f:
                        content = f.read()
                    if old_string in content:
                        lines_before = content[: content.find(old_string)].count("\n")
                        line_info = f", line {lines_before + 1}"
                        add_line_info = f", starting line {lines_before + 1}"
                except Exception:
                    pass

            return f"EDIT OPERATION:\nREMOVING ({old_lines} lines{line_info}):\n```\n{old_string}\n```\nADDING ({new_lines} lines{add_line_info}):\n```\n{new_string}\n```"

        elif tool_name == "MultiEdit":
            edits = tool_input.get("edits", [])
            edit_details = []
            for i, edit in enumerate(edits):
                old_string = edit.get("old_string", "")
                new_string = edit.get("new_string", "")
                old_lines = len(old_string.split("\n"))
                new_lines = len(new_string.split("\n"))
                edit_details.append(
                    f"EDIT {i + 1}:\nREMOVING ({old_lines} lines):\n```\n{old_string}\n```\nADDING ({new_lines} lines):\n```\n{new_string}\n```"
                )
            return "MULTIEDIT OPERATION:\n" + "\n\n".join(edit_details)

        return ""

    def build_memory_search_prompt(
        self, file_path: str, tool_name: str, code_info: str
    ) -> str:
        """Build the prompt for comprehensive code quality analysis."""
        
        return f"""You are a comprehensive code quality gate with access to MCP memory tools.

🚨 ERROR REPORTING: If you cannot access MCP memory tools ({self.mcp_collection}*), report in detail:
- Which exact MCP tool you tried to call (e.g., "{self.mcp_collection}search_similar")
- What parameters you used (query, entityTypes, limit) 
- What error message you received (timeout, not found, access denied, etc.)
- Include this in your debug field with prefix "MCP_ERROR:"

OPERATION CONTEXT:
- Project: {self.project_name}
- File: {file_path}
- Operation: {tool_name}
- Code changes:
{code_info}

🔍 COMPREHENSIVE QUALITY ANALYSIS - CHECK ALL AREAS:

❌ BLOCK FOR ANY OF THESE ISSUES:

🔄 1. CODE DUPLICATION:
- NEW function/class definitions that duplicate existing functionality
- Copy-paste code with minor variations
- Redundant implementations of existing utility functions
- Similar validation/processing patterns already in codebase

🧠 2. LOGIC COMPLETENESS:
- Missing critical error handling for expected failures
- Incomplete input validation (missing edge cases, type checks)
- Missing null/undefined/empty checks where needed
- Incomplete transaction handling (missing rollback, cleanup)
- Missing security validations (auth, permissions, sanitization)

🔗 3. FLOW INTEGRITY:
- Breaking existing API contracts or interfaces
- Removing required parameters without backward compatibility
- Changing function signatures that other code depends on
- Disrupting established data flow patterns
- Breaking existing error handling chains

⚙️ 4. FEATURE PRESERVATION:
- Disabling or removing existing functionality without replacement
- Breaking existing workflows or user journeys
- Removing configuration options that others depend on
- Breaking existing integrations or dependencies
- Removing accessibility features or degrading UX

✅ ALWAYS ALLOW:
- Function calls, imports, variable assignments, using existing code
- Proper refactoring that maintains functionality
- Adding new features without breaking existing ones
- Improvements that enhance without removing capabilities

⛔ CRITICAL: MANUAL ENTRIES ARE TOXIC TO ANALYSIS
- Manual entries contain OUTDATED ASSUMPTIONS about features that may not exist
- They reference "fixes" and "optimizations" that could be FALSE
- ONLY trust actual code implementations, NEVER trust human descriptions
- If search returns commit references, feature descriptions, or fix notes → CONTAMINATED, ignore

🔍 ANALYSIS PROTOCOL:
1. EXTRACT function/class names from new code being written
2. SEARCH memory for exact duplicates of same functionality using {self.mcp_collection}search_similar
3. SEARCH for related patterns, similar implementations, and existing functionality that could be reused
4. IF similar functions exist: SUGGEST using existing code instead of creating duplicates
5. Use MCP service: {self.mcp_collection}search_similar for related functionality analysis
6. Analyze completeness: missing error cases, validations, edge cases
7. Check integration: dependencies, API contracts, data flows
8. Verify flow integrity: function usage, dependency impact, breaking changes
9. Verify preservation: ensure existing features remain functional
10. EXCLUDE ALL MANUAL ENTRIES AND DOCUMENTATION:
   - IGNORE: documentation files (.md, .txt, .markdown, .rst)
   - IGNORE: manual entries (debugging_pattern, implementation_pattern, integration_pattern, configuration_pattern, architecture_pattern, performance_pattern, knowledge_insight, active_issue, ideas)
   - IGNORE: any human-created analysis, notes, or patterns
   - IGNORE: test/debug files (tests/, debug/, *test*, *debug*) - not production code
   - FOCUS ONLY ON: actual production code implementations (functions, classes, interfaces)

🎯 ANALYSIS STRATEGY:
- Use entityTypes filters: ["metadata", "function", "class"] for overview
- Use entityTypes=["implementation"] for detailed code analysis
- Use entityTypes=["relation"] for dependency analysis
- Search for related patterns: error handling, validation, similar flows
- Look for dependencies and integration points
- Check function usage with read_graph(entity="function_name", mode="relationships")
- Check for existing feature implementations

📋 RESPONSE FORMAT (JSON only):
⚠️ VALIDATION: If your reason mentions past commits, historical context, or specific feature implementations without showing actual code → you used manual entries! Re-analyze with proper filters.

For BLOCKING (quality issues found): {{
  "hasIssues": true,
  "issueType": "duplication|logic|flow|feature|dependency",
  "reason": "Specific issue description with location and impact",
  "suggestion": "Concrete recommendation to fix the issue",
  "debug": "2-3 sentences: What you found + Why it's problematic + What should be done",
  "turns_used": "number of turns for analysis",
  "steps_summary": ["search_similar(query='<query>', entityTypes=['<types>'], limit=<n>)", "read_graph(entity='<entity>', mode='<mode>')", "search_similar(query='<refinement>', entityTypes=['<types>'])"]
}}

For APPROVING (no quality issues): {{
  "hasIssues": false,
  "decision": "approve",
  "reason": "Your analysis of why this code is acceptable",
  "debug": "Your detailed analysis findings",
  "turns_used": "number of turns for analysis",
  "steps_summary": ["search_similar(query='<query>', entityTypes=['<types>'], limit=<n>)", "read_graph(entity='<entity>', mode='<mode>')", "search_similar(query='<refinement>', entityTypes=['<types>'])"]
}}

🚨 CRITICAL: Thoroughly analyze ALL four quality dimensions. Only approve if code passes ALL checks.
IMPORTANT: Return ONLY the JSON object, no explanatory text."""

    def call_claude_cli(self, prompt: str) -> tuple[bool, str, dict[str, Any]]:
        """Call Claude CLI for comprehensive code quality analysis."""
        try:
            # Use .claude directory for isolated sessions
            claude_dir = (
                self.project_root / ".claude"
                if self.project_root
                else Path.cwd() / ".claude"
            )

            # Allow specific MCP memory tools plus read-only analysis tools
            allowed_tools = f"Read,LS,Bash(ls:*),Glob,Grep,WebFetch,WebSearch,{self.mcp_collection}search_similar,{self.mcp_collection}read_graph,{self.mcp_collection}get_implementation,mcp__github__*"

            result = subprocess.run(
                [
                    "claude",
                    "-p",
                    "--output-format",
                    "json",
                    "--max-turns",
                    "20",
                    "--model",
                    "sonnet",
                    "--allowedTools",
                    allowed_tools,
                ],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(claude_dir),
            )

            if result.returncode != 0:
                error_msg = f"Claude CLI failed with return code {result.returncode}"
                crash_info = f"\n{'=' * 60}\nCRASH DETECTED - CLAUDE CLI FAILED:\n"
                crash_info += f"Return code: {result.returncode}\n"
                crash_info += f"STDERR: {result.stderr}\n"
                crash_info += f"STDOUT: {result.stdout}\n"
                crash_info += f"Error: {error_msg}\n"
                self.save_debug_info(crash_info)
                return False, error_msg, {"error": "cli_failed", "returncode": result.returncode, "stderr": result.stderr}

            # Log debug info IMMEDIATELY after successful CLI execution (before parsing)
            debug_content = f"\n{'=' * 60}\nQUERY SENT TO CLAUDE:\n{prompt}\n\n"
            debug_content += (
                f"RAW STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}\n"
            )
            self.save_debug_info(debug_content)  # Log before parsing to prevent loss

            # Parse response (may throw exception)
            try:
                return self.parse_claude_response(result.stdout)
            except Exception as parse_error:
                # Log parsing failure with details
                parse_crash_info = f"\n{'=' * 60}\nCRASH DETECTED - PARSE FAILURE:\n"
                parse_crash_info += f"Parse error: {type(parse_error).__name__}: {str(parse_error)}\n"
                parse_crash_info += f"Raw stdout length: {len(result.stdout)} chars\n"
                parse_crash_info += f"RESULT: Graceful degradation - approving operation\n"
                self.save_debug_info(parse_crash_info)
                raise  # Re-raise to be caught by outer exception handler

        except subprocess.TimeoutExpired as e:
            error_msg = f"Claude CLI timeout after {e.timeout}s"
            crash_info = f"\n{'=' * 60}\nCRASH DETECTED - TIMEOUT:\n"
            crash_info += f"Timeout: {e.timeout}s\n"
            crash_info += f"Command: {e.cmd}\n"
            crash_info += f"Error: {error_msg}\n"
            self.save_debug_info(crash_info)
            return False, error_msg, {"error": "timeout", "timeout": e.timeout}
        except Exception as e:
            error_msg = f"Claude CLI error: {str(e)}"
            crash_info = f"\n{'=' * 60}\nCRASH DETECTED - EXCEPTION:\n"
            crash_info += f"Exception type: {type(e).__name__}\n"
            crash_info += f"Exception message: {str(e)}\n"
            crash_info += f"Error: {error_msg}\n"
            import traceback
            crash_info += f"Traceback:\n{traceback.format_exc()}\n"
            self.save_debug_info(crash_info)
            return False, error_msg, {"error": "exception", "exception_type": type(e).__name__, "message": str(e)}

    def parse_claude_response(self, stdout: str) -> tuple[bool, str, dict[str, Any]]:
        """Parse Claude's response to extract duplicate detection result."""
        try:
            stdout = stdout.strip()

            # Handle CLI wrapper format
            if stdout.startswith('{"type":"result"'):
                cli_response = json.loads(stdout)

                # Check for any CLI errors first
                if cli_response.get("subtype") == "error_max_turns":
                    return (
                        True,
                        f"⚠️  MEMORY GUARD ERROR: Claude CLI hit max turns limit ({cli_response.get('num_turns', '?')} turns). Analysis incomplete.",
                        cli_response,
                    )
                elif cli_response.get("is_error"):
                    return (
                        True,
                        f"⚠️  MEMORY GUARD ERROR: Claude CLI error occurred: {cli_response}",
                        cli_response,
                    )

                result_content = cli_response.get("result", "")

                # Extract JSON from markdown if present
                if "```json" in result_content:
                    json_start = result_content.find("```json\n") + 8
                    json_end = result_content.find("\n```", json_start)
                    inner_json = result_content[json_start:json_end]
                else:
                    inner_json = result_content

                response = json.loads(inner_json)
            else:
                # Direct JSON response
                response = json.loads(stdout)

            # Process comprehensive quality analysis response
            has_issues = response.get("hasIssues", False)
            if has_issues:
                issue_type = response.get("issueType", "unknown")
                issue_icons = {
                    "duplication": "🔄",
                    "logic": "🧠",
                    "flow": "🔗",
                    "feature": "⚙️",
                }
                icon = issue_icons.get(issue_type, "⚠️")
                reason = f"{icon} CODE QUALITY ISSUE DETECTED ({issue_type.upper()}):\n{response.get('reason', '')}"

                # Add analysis steps if available
                if response.get("steps_summary"):
                    steps = response.get("steps_summary", [])
                    if steps:
                        reason += "\n\n🔍 ANALYSIS STEPS:\n"
                        for i, step in enumerate(steps, 1):
                            reason += f"   {i}. {step}\n"

                if response.get("suggestion"):
                    reason += f"\n💡 SUGGESTION: {response.get('suggestion')}"
                return True, reason, response
            else:
                return (
                    False,
                    response.get("reason", ""),
                    response,
                )

        except json.JSONDecodeError:
            return False, f"Claude CLI non-JSON response: {stdout[:300]}", {}
        except Exception as e:
            return False, f"Error parsing response: {str(e)}", {}

    def process_hook(self, hook_data: dict[str, Any]) -> dict[str, Any]:
        """Process the hook and return the result."""
        # EMERGENCY DEBUG - track all hook calls
        # try:
        #     with open("/tmp/memory_guard_debug.log", "a") as f:
        #         f.write(f"PROCESS_HOOK CALLED: project_root={self.project_root}, tool={hook_data.get('tool_name')}\n")
        # except:
        #     pass
            
        # Default result
        result = {"suppressOutput": False}

        try:
            # Get file path from tool input to detect correct project
            tool_input = hook_data.get("tool_input", {})
            file_path = tool_input.get("file_path", "")
            
            # Detect project root and MCP collection based on target file
            if file_path:
                self.project_root = self._detect_project_root(file_path)
                if self.project_root:
                    self.project_name = self.project_root.name
                    self.mcp_collection = self._detect_mcp_collection()
                    
                    # Initialize bypass manager for the detected project
                    if not self.bypass_manager:
                        self.bypass_manager = BypassManager(self.project_root)
                    
                    # Log the project detection (consolidated to prevent duplication)
                    project_info = f"\n🎯 PROJECT DETECTED:\n- Project: {self.project_name}\n- Root: {self.project_root}\n- MCP Collection: {self.mcp_collection}\n"
                    self.save_debug_info(project_info)

            # Check if we should process this hook
            should_process, skip_reason = self.should_process(hook_data)
            if not should_process:
                result["reason"] = skip_reason
                # Log skipped operation
                skip_info = f"\n{'=' * 60}\nOPERATION SKIPPED:\n"
                skip_info += f"- Reason: {skip_reason}\n"
                self.save_debug_info(skip_info)
                return result

            # Extract information
            tool_name = hook_data.get("tool_name", "")

            # Extract entities (not used but required for analysis flow)
            _ = self.extractor.extract_entities_from_operation(
                tool_name, tool_input
            )

            # Get code information
            code_info = self.get_code_info(tool_name, tool_input)
            if not code_info:
                result["reason"] = "No code content to check"
                return result

            # Check override comments before Claude CLI
            has_override, override_reason = self.check_for_override_comments(code_info)
            if has_override:
                result["reason"] = f"Duplicate allowed: {override_reason}"
                self.save_debug_info(f"\nOVERRIDE: {override_reason}\n")
                return result

            # Quick trivial operation check
            is_trivial, trivial_reason = self.is_trivial_operation(code_info)
            if is_trivial:
                result["reason"] = f"🟢 {trivial_reason}"
                self.save_debug_info(f"\nTRIVIAL OPERATION SKIPPED: {trivial_reason}\n")
                return result

            # Skip trivial operations only - test everything else
            # Removed the new definitions filter - we want to test all non-trivial code

            # Build prompt and check for duplicates
            file_path = tool_input.get("file_path", "unknown")
            prompt = self.build_memory_search_prompt(file_path, tool_name, code_info)

            # Call Claude CLI
            should_block, reason, claude_response = self.call_claude_cli(prompt)

            # Set result
            result["reason"] = reason
            if should_block:
                result["decision"] = "block"

            # Log final decision
            decision_info = f"\n{'=' * 60}\nFINAL DECISION:\n"
            decision_info += f"- Should Block: {should_block}\n"
            decision_info += f"- Decision: {result.get('decision', 'approve')}\n"
            decision_info += f"- Reason: {reason}\n"
            decision_info += f"- Claude Response:\n{json.dumps(claude_response, indent=2)}\n"
            self.save_debug_info(decision_info)

        except Exception as e:
            # Graceful degradation - always approve on errors
            result["reason"] = f"Error in memory guard: {str(e)}"

            # Log comprehensive crash info
            import traceback
            crash_info = f"\n{'=' * 60}\nCRASH DETECTED - PROCESS_HOOK FAILURE:\n"
            crash_info += f"Exception type: {type(e).__name__}\n"
            crash_info += f"Exception message: {str(e)}\n"
            crash_info += f"Project: {self.project_name}\n"
            crash_info += f"Tool: {hook_data.get('tool_name', 'unknown')}\n"
            crash_info += f"File: {hook_data.get('tool_input', {}).get('file_path', 'unknown')}\n"
            crash_info += f"Traceback:\n{traceback.format_exc()}\n"
            crash_info += f"Hook data: {json.dumps(hook_data, indent=2)}\n"
            crash_info += "RESULT: Graceful degradation - approving operation\n"
            self.save_debug_info(crash_info)

        return result


def main():
    """Main entry point for the hook."""
    try:
        # Read hook data from stdin
        hook_data = json.loads(sys.stdin.read())

        # Initialize guard with hook data for early project detection
        guard = MemoryGuard(hook_data)

        # Clear debug file at start and save initial info with timestamp
        debug_info = f"HOOK CALLED:\n{json.dumps(hook_data, indent=2)}\n\n"
        debug_info += "PROJECT INFO:\n"
        debug_info += f"- Root: {guard.project_root}\n"
        debug_info += f"- Name: {guard.project_name}\n"
        debug_info += f"- MCP Collection: {guard.mcp_collection}\n\n"
        guard.save_debug_info(
            debug_info, mode="w", timestamp=True
        )  # Clear file with timestamp

        # Process hook
        result = guard.process_hook(hook_data)

        # Output result
        print(json.dumps(result))

    except Exception as e:
        # Fallback error handling
        result = {
            "reason": f"Fatal error in memory guard: {str(e)}",
            "suppressOutput": False,
        }
        print(json.dumps(result))


if __name__ == "__main__":
    main()
