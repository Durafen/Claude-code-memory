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
        self.bypass_manager = BypassManager(Path.cwd())

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
