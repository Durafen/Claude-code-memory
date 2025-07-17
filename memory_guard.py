#!/usr/bin/env python3
"""
Memory Guard - Code Duplication Prevention Hook for Claude Code
With entity extraction functionality.
"""

import json
import sys
import re
import subprocess
import os
from typing import List, Dict, Any, Optional


class EntityExtractor:
    """Extract entities from code content."""
    
    def extract_entities_from_operation(self, tool_name: str, tool_input: Dict[str, Any]) -> List[str]:
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
    
    def _extract_python_entities(self, content: str) -> List[str]:
        """Extract Python function and class names."""
        entities = []
        
        # Function patterns
        func_pattern = r'^def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\('
        for match in re.finditer(func_pattern, content, re.MULTILINE):
            entities.append(match.group(1))
        
        # Class patterns  
        class_pattern = r'^class\s+([a-zA-Z_][a-zA-Z0-9_]*)'
        for match in re.finditer(class_pattern, content, re.MULTILINE):
            entities.append(match.group(1))
        
        return entities


def main():
    """Main entry point for the hook."""
    try:
        # Read hook data from stdin
        hook_data = json.loads(sys.stdin.read())
        
        tool_name = hook_data.get("tool_name", "")
        tool_input = hook_data.get("tool_input", {})
        hook_event = hook_data.get("hook_event_name", "")
        
        # Only process PreToolUse events for Write/Edit operations
        if hook_event != "PreToolUse" or tool_name not in ["Write", "Edit", "MultiEdit"]:
            result = {
                "decision": "approve",
                "reason": "Not a relevant operation"
            }
            print(json.dumps(result))
            return
        
        # Only check duplicates within the Memory project directory
        file_path = tool_input.get("file_path", "")
        if not file_path or "/Python-Projects/memory/" not in file_path:
            result = {
                "decision": "approve",
                "reason": "Outside Memory project - no duplicate checking"
            }
            print(json.dumps(result))
            return
        
        # Extract entities
        extractor = EntityExtractor()
        entities = extractor.extract_entities_from_operation(tool_name, tool_input)
        
        # Send actual code content to Claude CLI for duplicate detection
        try:
            # Get the actual code content being changed
            code_info = ""
            if tool_name == "Write":
                content = tool_input.get("content", "")
                lines = content.split('\n')
                code_info = f"NEW FILE CONTENT ({len(lines)} lines):\n```\n{content}\n```"
            elif tool_name == "Edit":
                old_string = tool_input.get("old_string", "")
                new_string = tool_input.get("new_string", "")
                old_lines = len(old_string.split('\n'))
                new_lines = len(new_string.split('\n'))
                code_info = f"EDIT OPERATION:\nREMOVING ({old_lines} lines):\n```\n{old_string}\n```\nADDING ({new_lines} lines):\n```\n{new_string}\n```"
            elif tool_name == "MultiEdit":
                edits = tool_input.get("edits", [])
                edit_details = []
                for i, edit in enumerate(edits):
                    old_string = edit.get("old_string", "")
                    new_string = edit.get("new_string", "")
                    old_lines = len(old_string.split('\n'))
                    new_lines = len(new_string.split('\n'))
                    edit_details.append(f"EDIT {i+1}:\nREMOVING ({old_lines} lines):\n```\n{old_string}\n```\nADDING ({new_lines} lines):\n```\n{new_string}\n```")
                code_info = "MULTIEDIT OPERATION:\n" + "\n\n".join(edit_details)
            
            if code_info:
                memory_search_prompt = f"""You are a code duplication detector with access to MCP memory tools.

OPERATION CONTEXT:
- File: {tool_input.get('file_path', 'unknown')}
- Operation: {tool_name}
- Code changes:
{code_info}

SEARCH PROTOCOL:
1. Use MCP service: mcp__claude-memory-memory__search_similar
2. Search for similar code functionality in the codebase
3. Check if the NEW code being added duplicates existing implementations
4. If duplicates found, suggest using existing code instead

RESPONSE FORMAT (JSON only):
{{
  "hasDuplicates": true/false,
  "decision": "block" or "approve", 
  "reason": "Search results summary and suggestion to use existing code if duplicate found",
  "debug": "2 sentences: What you found in search + Why you made this decision",
  "turns_used": "number of turns it took to complete this analysis"
}}

Check if this NEW code duplicates existing implementations and suggest alternatives."""

                try:
                    result = subprocess.run([
                        '/Users/Duracula 1/.claude/local/claude', '--max-turns', '10'
                    ], input=memory_search_prompt, capture_output=True, text=True, timeout=60)
                    
                    if result.returncode == 0:
                        # Debug: Always write Claude's response to file
                        try:
                            with open('memory_guard_debug.json', 'w') as f:
                                f.write(f"RAW STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}\n")
                        except:
                            pass
                        
                        # Try to parse Claude's JSON response
                        try:
                            claude_response = json.loads(result.stdout)
                            decision = claude_response.get('decision', 'approve')
                            reason = claude_response.get('reason', 'Claude CLI memory search completed')
                        except json.JSONDecodeError:
                            decision = "approve"
                            reason = f"Claude CLI non-JSON response: {result.stdout[:300]}"
                    else:
                        decision = "approve"
                        reason = "Claude CLI failed"
                except Exception as e:
                    decision = "approve"
                    reason = f"Claude CLI error: {str(e)}"
            else:
                decision = "approve"
                reason = "No code content to check"
        except Exception as e:
            decision = "approve"
            reason = f"Error processing code: {str(e)}"
        
        result = {
            "decision": decision,
            "reason": reason
        }
        
        print(json.dumps(result))
        
    except Exception as e:
        # Graceful degradation
        result = {
            "decision": "approve", 
            "reason": f"Error in memory guard: {e}"
        }
        print(json.dumps(result))


if __name__ == "__main__":
    main()