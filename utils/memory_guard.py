#!/usr/bin/env python3
"""
Memory Guard - Code Duplication Prevention Hook for Claude Code
With entity extraction functionality.

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
DEBUG_ENABLED = True
DEBUG_LOG_FILE = "memory_guard_debug.txt"


class BypassManager:
    """Manages Memory Guard bypass state with simple on/off commands."""

    def __init__(self, project_root: Path | None = None):
        self.project_root = project_root or Path.cwd()
        self.state_file = self.project_root / ".claude" / "guard_state.json"
        self.lock = threading.Lock()

        # Ensure .claude directory exists
        self.state_file.parent.mkdir(exist_ok=True)

    def is_disabled(self, session_id: str) -> bool:
        """Check if Memory Guard is disabled for the session."""
        try:
            if not self.state_file.exists():
                return False

            with self.lock:
                state = json.loads(self.state_file.read_text())
                return state.get(session_id, {}).get("disabled", False)
        except:
            return False

    def set_state(self, session_id: str, disabled: bool) -> str:
        """Enable or disable Memory Guard for the session."""
        try:
            with self.lock:
                state = {}
                if self.state_file.exists():
                    state = json.loads(self.state_file.read_text())

                state[session_id] = {
                    "disabled": disabled,
                    "timestamp": datetime.now().isoformat(),
                }

                self.state_file.write_text(json.dumps(state, indent=2))

                if disabled:
                    return "🔴 Memory Guard disabled for this session"
                else:
                    return "🟢 Memory Guard enabled for this session"
        except Exception as e:
            return f"❌ Error setting guard state: {str(e)}"

    def get_status(self, session_id: str) -> str:
        """Get current Memory Guard status for the session."""
        try:
            is_disabled = self.is_disabled(session_id)
            if is_disabled:
                return "📊 Memory Guard Status: 🔴 DISABLED (use 'dups on' to enable)"
            else:
                return "📊 Memory Guard Status: 🟢 ENABLED (use 'dups off' to disable)"
        except:
            return "📊 Memory Guard Status: 🟢 ENABLED (default)"


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
    """Main Memory Guard functionality."""

    def __init__(self):
        self.extractor = EntityExtractor()
        self.project_root = self._detect_project_root()
        self.project_name = self.project_root.name if self.project_root else "unknown"
        self.mcp_collection = self._detect_mcp_collection()
        self.bypass_manager = BypassManager(self.project_root)
        self.code_analyzer = CodeAnalyzer()

    def _detect_project_root(self) -> Path | None:
        """Detect the project root directory."""
        try:
            # Start from current working directory
            current = Path.cwd()

            # Look for common project markers
            markers = [
                ".git",
                "pyproject.toml",
                "setup.py",
                "package.json",
                "Cargo.toml",
                "go.mod",
                ".claude",
                "CLAUDE.md",
            ]

            while current != current.parent:
                for marker in markers:
                    if (current / marker).exists():
                        return current
                current = current.parent

            return Path.cwd()  # Default to cwd if no markers found
        except:
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
                except:
                    pass

            # Default to project name based collection
            safe_name = re.sub(r"[^a-zA-Z0-9-]", "-", self.project_name.lower())
            return f"mcp__{safe_name}-memory__"

        return "mcp__project-memory__"

    def save_debug_info(
        self, content: str, mode: str = "a", timestamp: bool = False
    ) -> None:
        """Save debug information to file in project directory, optionally with timestamp."""
        if not DEBUG_ENABLED:
            return
        try:
            if timestamp:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                content = f"[{ts}] {content}"

            # Save in project root directory
            debug_file = (
                self.project_root / DEBUG_LOG_FILE
                if self.project_root
                else Path(DEBUG_LOG_FILE)
            )
            with open(debug_file, mode) as f:
                f.write(content)
        except:
            pass

    def should_process(self, hook_data: dict[str, Any]) -> tuple[bool, str | None]:
        """Determine if this hook event should be processed."""
        # Check bypass state first
        session_id = hook_data.get("session_id", "")
        if self.bypass_manager.is_disabled(session_id):
            return False, "🔴 Memory Guard bypass active (use 'dups on' to re-enable)"

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
                ".html",
                ".css",
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
        except:
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
                except:
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
        """Build the prompt for Claude to check duplicates."""
        return f"""You are a code duplication detector with access to MCP memory tools.

OPERATION CONTEXT:
- Project: {self.project_name}
- File: {file_path}
- Operation: {tool_name}
- Code changes:
{code_info}

CRITICAL DISTINCTION - WHAT TO BLOCK vs ALLOW:

❌ BLOCK ONLY:
1. NEW FUNCTION/CLASS DEFINITIONS that duplicate existing functionality
2. DUPLICATE CODE LOGIC/ALGORITHMS (same patterns, different locations)
3. COPY-PASTE CODE with minor variations
4. REDUNDANT IMPLEMENTATIONS of existing utility functions
5. SIMILAR VALIDATION/PROCESSING patterns already in codebase
   Examples: "def existing_function():", "class ExistingClass:", implementing same logic again

✅ ALWAYS ALLOW: FUNCTION CALLS, imports, variable assignments, using existing code
   Examples: "result = existing_function()", "from module import function", "obj.method()"

SEARCH PROTOCOL:
1. Use MCP service: {self.mcp_collection}search_similar
2. Search for similar IMPLEMENTATIONS AND LOGIC PATTERNS in the codebase
3. Check if the NEW code is DEFINING duplicate functions/classes/logic
4. IGNORE function calls, imports, or usage of existing code (these are GOOD)

IMPORTANT: Only block if someone is creating a NEW implementation of existing functionality.
Using/calling existing code is exactly what we want - NEVER block function calls.

ANALYSIS STRATEGY:
- Use entityTypes filters for faster, more relevant results
- Start with: entityTypes=["metadata", "function", "class"]
- For detailed code: entityTypes=["implementation"]
- Look for NEW function definitions (def keyword) or class definitions (class keyword)
- Ignore function calls, method calls, imports, variable assignments

RESPONSE FORMAT (JSON only):
For BLOCKING (NEW duplicate implementation found): {{
  "hasDuplicates": true,
  "reason": "Found existing implementation: [name] at [location]. Consider using existing function instead of creating new one.",
  "debug": "2 sentences: What duplicate IMPLEMENTATION you found + Why this is creating new duplicate logic",
  "turns_used": "number of turns it took to complete this analysis"
}}

For APPROVING (no duplicate implementations OR just using existing code): {{
  "hasDuplicates": false,
  "decision": "approve",
  "reason": "No duplicate implementations detected - code is using existing functionality appropriately",
  "debug": "2 sentences: What you found in search + Why this is acceptable (function call/import/usage vs new implementation)",
  "turns_used": "number of turns it took to complete this analysis"
}}

Only block NEW function/class definitions OR duplicate logic patterns that replicate existing implementations.
NEVER block function calls, imports, or usage of existing code.

IMPORTANT: Return ONLY the JSON object, no explanatory text."""

    def call_claude_cli(self, prompt: str) -> tuple[bool, str, dict[str, Any]]:
        """Call Claude CLI to check for duplicates with project mode."""
        try:
            # Use .claude directory for isolated sessions
            claude_dir = (
                self.project_root / ".claude"
                if self.project_root
                else Path.cwd() / ".claude"
            )

            # Allow specific MCP memory tools
            allowed_tools = f"{self.mcp_collection}search_similar,{self.mcp_collection}read_graph,{self.mcp_collection}get_implementation"

            result = subprocess.run(
                [
                    "claude",
                    "-p",
                    "--output-format",
                    "json",
                    "--max-turns",
                    "10",
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
                return False, "Claude CLI failed", {}

            # Append debug info
            debug_content = f"\n{'=' * 60}\nQUERY SENT TO CLAUDE:\n{prompt}\n\n"
            debug_content += (
                f"RAW STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}\n"
            )
            self.save_debug_info(debug_content)  # Default mode='a'

            # Parse response
            return self.parse_claude_response(result.stdout)

        except subprocess.TimeoutExpired:
            return False, "Claude CLI timeout", {}
        except Exception as e:
            return False, f"Claude CLI error: {str(e)}", {}

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

            # Process response
            has_duplicates = response.get("hasDuplicates", False)
            if has_duplicates:
                reason = f"⚠️  POTENTIAL CODE DUPLICATION DETECTED:\n{response.get('reason', 'Similar code found in memory search')}"
                return True, reason, response
            else:
                return (
                    False,
                    response.get("reason", "No code duplication detected"),
                    response,
                )

        except json.JSONDecodeError:
            return False, f"Claude CLI non-JSON response: {stdout[:300]}", {}
        except Exception as e:
            return False, f"Error parsing response: {str(e)}", {}

    def process_hook(self, hook_data: dict[str, Any]) -> dict[str, Any]:
        """Process the hook and return the result."""
        # Default result
        result = {"suppressOutput": False}

        try:
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
            tool_input = hook_data.get("tool_input", {})

            # Extract entities
            entities = self.extractor.extract_entities_from_operation(
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
            decision_info += f"- Claude Response: {claude_response}\n"
            self.save_debug_info(decision_info)

        except Exception as e:
            # Graceful degradation - always approve on errors
            result["reason"] = f"Error in memory guard: {str(e)}"

            # Log error
            error_info = f"\n{'=' * 60}\nERROR IN PROCESS:\n{str(e)}\n"
            self.save_debug_info(error_info)

        return result


def main():
    """Main entry point for the hook."""
    try:
        # Read hook data from stdin
        hook_data = json.loads(sys.stdin.read())

        # Initialize guard
        guard = MemoryGuard()

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
