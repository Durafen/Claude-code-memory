import os
import sys
from pathlib import Path

# Add parent directory to path for imports when run as standalone script
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from utils.memory_guard import BypassManager
except ImportError:
    # Fallback for when run as standalone script
    from memory_guard import BypassManager


class PromptHandler:
    def __init__(self):
        project_root = self._detect_project_root() or Path.cwd()
        self.bypass_manager = BypassManager(project_root)
    
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

    def detect_bypass_command(self, prompt: str):
        # Minimal implementation to pass the first test
        prompt_lower = prompt.lower().strip()
        if "dups off" in prompt_lower:
            return {"action": "disable", "command": "dups off"}
        elif "dups on" in prompt_lower:
            return {"action": "enable", "command": "dups on"}
        elif "dups status" in prompt_lower:
            return {"action": "status", "command": "dups status"}
        return None

    def process_hook(self, hook_data):
        # Check for bypass commands and return notification
        prompt = hook_data.get("prompt", "")
        command_info = self.detect_bypass_command(prompt)

        if command_info:
            action = command_info.get("action")
            hook_data.get("session_id", "default")

            if action == "disable":
                message = self.bypass_manager.set_global_state(True)
                return {"continue": True, "notification": message}
            elif action == "enable":
                message = self.bypass_manager.set_global_state(False)
                return {"continue": True, "notification": message}
            elif action == "status":
                message = self.bypass_manager.get_global_status()
                return {"continue": True, "notification": message}

        return {"continue": True}


if __name__ == "__main__":
    import json
    import sys

    try:
        # Read hook data from stdin
        hook_data = json.loads(sys.stdin.read())

        # Debug log the received data
        with open("/Users/Duracula 1/Python-Projects/memory/debug/hook_debug.log", "a") as f:
            f.write(f"HOOK RECEIVED: {json.dumps(hook_data)}\n")

        # Initialize handler
        handler = PromptHandler()

        # Process hook
        result = handler.process_hook(hook_data)

        # Debug log the result
        with open("/Users/Duracula 1/Python-Projects/memory/debug/hook_debug.log", "a") as f:
            f.write(f"HOOK RESULT: {json.dumps(result)}\n")

        # Output result
        print(json.dumps(result))

        # Display notification in UI using stderr + exit code 2
        if "notification" in result:
            print(result["notification"], file=sys.stderr)
            sys.exit(2)

    except Exception as e:
        # Fallback error handling
        result = {
            "continue": True,
            "notification": f"Error in prompt handler: {str(e)}"
        }
        print(json.dumps(result))
        print(result["notification"], file=sys.stderr)
        sys.exit(2)
