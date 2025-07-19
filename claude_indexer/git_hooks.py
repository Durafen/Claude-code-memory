"""Git hooks management for automatic indexing."""

import os
import stat
from pathlib import Path


class GitHooksManager:
    """Manage git hooks for automatic indexing on commits."""

    def __init__(self, project_path: str, collection_name: str):
        self.project_path = Path(project_path)
        self.collection_name = collection_name
        self.git_dir = self.project_path / ".git"
        self.hooks_dir = self.git_dir / "hooks"
        self.pre_commit_path = self.hooks_dir / "pre-commit"

    def is_git_repository(self) -> bool:
        """Check if the project is a git repository."""
        return self.git_dir.exists() and self.git_dir.is_dir()

    def is_hook_installed(self) -> bool:
        """Check if the pre-commit hook is installed."""
        if not self.pre_commit_path.exists():
            return False

        try:
            with open(self.pre_commit_path) as f:
                content = f.read()
            return "Claude Code Memory" in content and self.collection_name in content
        except Exception:
            return False

    def install_pre_commit_hook(
        self, indexer_path: str | None = None, quiet: bool = False
    ) -> bool:
        """Install pre-commit hook for automatic indexing."""
        if not self.is_git_repository():
            if not quiet:
                print("❌ Not a git repository - cannot install git hooks")
            return False

        try:
            # Create hooks directory if it doesn't exist
            self.hooks_dir.mkdir(exist_ok=True)

            # Determine indexer path
            if not indexer_path:
                # Try common locations
                for cmd in [
                    "claude-indexer",
                    "python -m claude_indexer",
                    "./indexer.py",
                ]:
                    if self._command_exists(cmd.split()[0]):
                        indexer_path = cmd
                        break
                else:
                    indexer_path = "claude-indexer"  # Default fallback

            # Create or update pre-commit hook
            success = self._create_hook_script(indexer_path)

            if success and not quiet:
                print(
                    f"✅ Installed pre-commit hook for collection '{self.collection_name}'"
                )
                print(
                    f'   Hook will run: {indexer_path} --project "{self.project_path}" --collection "{self.collection_name}" --quiet'
                )

            return success

        except Exception as e:
            if not quiet:
                print(f"❌ Failed to install pre-commit hook: {e}")
            return False

    def _command_exists(self, command: str) -> bool:
        """Check if a command exists in PATH."""
        try:
            import shutil

            return shutil.which(command) is not None
        except Exception:
            return False

    def _create_hook_script(self, indexer_path: str) -> bool:
        """Create the pre-commit hook script."""
        hook_content = f"""#!/bin/bash
# Claude Code Memory - Pre-commit Hook
# Automatically index changed Python files before commit
# Collection: {self.collection_name}

echo "🔄 Running Claude Code indexing..."

# Run automatic indexing (auto-detects incremental mode)
{indexer_path} --project "{self.project_path}" --collection "{self.collection_name}" --quiet

# Check if indexing succeeded
if [ $? -eq 0 ]; then
    echo "✅ Indexing complete"
else
    echo "⚠️  Indexing failed - proceeding with commit"
fi

# Always allow commit to proceed
exit 0
"""

        try:
            # Handle existing pre-commit hook
            if self.pre_commit_path.exists():
                # Check if it's our hook
                with open(self.pre_commit_path) as f:
                    existing_content = f.read()

                if "Claude Code Memory" in existing_content:
                    # Replace our hook
                    with open(self.pre_commit_path, "w") as f:
                        f.write(hook_content)
                else:
                    # Backup existing hook and append ours
                    backup_path = self.pre_commit_path.with_suffix(".bak")
                    if not backup_path.exists():
                        self.pre_commit_path.rename(backup_path)

                    combined_content = existing_content.rstrip() + "\n\n" + hook_content
                    with open(self.pre_commit_path, "w") as f:
                        f.write(combined_content)
            else:
                # Create new hook
                with open(self.pre_commit_path, "w") as f:
                    f.write(hook_content)

            # Make hook executable
            os.chmod(self.pre_commit_path, stat.S_IRWXU | stat.S_IRGRP | stat.S_IROTH)

            return True

        except Exception as e:
            raise RuntimeError(f"Failed to create hook script: {e}")

    def uninstall_pre_commit_hook(self, quiet: bool = False) -> bool:
        """Remove the pre-commit hook."""
        if not self.pre_commit_path.exists():
            if not quiet:
                print("ℹ️  No pre-commit hook found")
            return True

        try:
            with open(self.pre_commit_path) as f:
                content = f.read()

            if "Claude Code Memory" not in content:
                if not quiet:
                    print(
                        "ℹ️  Pre-commit hook exists but is not managed by Claude Code Memory"
                    )
                return True

            # Check if hook is only ours
            lines = content.split("\n")
            non_memory_lines = [
                line
                for line in lines
                if not any(
                    marker in line
                    for marker in [
                        "Claude Code Memory",
                        "claude-indexer",
                        "Running Claude Code indexing",
                        "Indexing complete",
                        "Indexing failed",
                    ]
                )
            ]

            if (
                len([line for line in non_memory_lines if line.strip()]) <= 2
            ):  # Just shebang and exit
                # Remove entire hook
                self.pre_commit_path.unlink()
                if not quiet:
                    print("✅ Removed pre-commit hook")
            else:
                # Restore backup if available
                backup_path = self.pre_commit_path.with_suffix(".bak")
                if backup_path.exists():
                    backup_path.replace(self.pre_commit_path)
                    if not quiet:
                        print("✅ Restored original pre-commit hook from backup")
                else:
                    # Remove our section manually (simplified)
                    filtered_lines = [
                        line
                        for line in lines
                        if not any(
                            marker in line
                            for marker in [
                                "Claude Code Memory",
                                "claude-indexer",
                                "Running Claude Code indexing",
                            ]
                        )
                    ]

                    with open(self.pre_commit_path, "w") as f:
                        f.write("\n".join(filtered_lines))

                    if not quiet:
                        print(
                            "✅ Removed Claude Code Memory section from pre-commit hook"
                        )

            return True

        except Exception as e:
            if not quiet:
                print(f"❌ Failed to uninstall pre-commit hook: {e}")
            return False

    def get_hook_status(self) -> dict:
        """Get detailed status of git hooks."""
        status = {
            "is_git_repo": self.is_git_repository(),
            "hooks_dir_exists": self.hooks_dir.exists(),
            "pre_commit_exists": self.pre_commit_path.exists(),
            "hook_installed": False,
            "hook_executable": False,
            "collection_name": self.collection_name,
            "project_path": str(self.project_path),
        }

        if self.pre_commit_path.exists():
            try:
                # Check if hook is executable
                stat_info = os.stat(self.pre_commit_path)
                status["hook_executable"] = bool(stat_info.st_mode & stat.S_IXUSR)

                # Check if it's our hook
                with open(self.pre_commit_path) as f:
                    content = f.read()

                status["hook_installed"] = (
                    "Claude Code Memory" in content and self.collection_name in content
                )

                # Extract indexer command if present
                for line in content.split("\n"):
                    if "claude-indexer" in line and "--project" in line:
                        status["indexer_command"] = line.strip()
                        break

            except Exception as e:
                status["error"] = str(e)

        return status

    def test_hook(self, dry_run: bool = True) -> bool:
        """Test the pre-commit hook (without actually committing)."""
        if not self.is_hook_installed():
            print("❌ Pre-commit hook is not installed")
            return False

        try:
            import subprocess

            if dry_run:
                print("🧪 Testing pre-commit hook (dry run)...")
                # Just verify the hook script is valid
                result = subprocess.run(
                    ["bash", "-n", str(self.pre_commit_path)],
                    capture_output=True,
                    text=True,
                )

                if result.returncode == 0:
                    print("✅ Pre-commit hook syntax is valid")
                    return True
                else:
                    print(f"❌ Pre-commit hook has syntax errors: {result.stderr}")
                    return False
            else:
                print("🧪 Executing pre-commit hook...")
                result = subprocess.run(
                    [str(self.pre_commit_path)],
                    cwd=str(self.project_path),
                    capture_output=True,
                    text=True,
                )

                print(f"Exit code: {result.returncode}")
                if result.stdout:
                    print(f"Output: {result.stdout}")
                if result.stderr:
                    print(f"Errors: {result.stderr}")

                return result.returncode == 0

        except Exception as e:
            print(f"❌ Failed to test hook: {e}")
            return False
