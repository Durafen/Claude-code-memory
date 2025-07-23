#!/usr/bin/env python3
"""
Generate a list of all Claude Code project chats with full metadata.
Outputs to chats.md with checkbox format, incremental updates supported.
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Import after sys.path modification
from claude_indexer.chat.parser import ChatConversation, ChatParser  # noqa: E402


def load_existing_chats(chats_file: Path) -> set[str]:
    """Load session IDs of existing chats from chats.md."""
    existing_ids = set()

    if not chats_file.exists():
        return existing_ids

    with open(chats_file, encoding="utf-8") as f:
        for line in f:
            # Extract session ID from markdown format
            # Format: - [ ] **Session ID**: abc123...
            if (line.strip().startswith("- [ ]") or line.strip().startswith("- [x]")) and "**Session ID**:" in line:
                    parts = line.split("**Session ID**:", 1)
                    if len(parts) > 1:
                        session_id = parts[1].strip().split()[0]
                        existing_ids.add(session_id)

    return existing_ids


def format_chat_entry(conv: ChatConversation, compact: bool = False) -> str:
    """Format a chat conversation as a markdown entry."""
    lines = []

    if compact:
        # Compact format - minimal info
        duration = conv.metadata.end_time - conv.metadata.start_time
        hours = int(duration.total_seconds() // 3600)
        minutes = int((duration.total_seconds() % 3600) // 60)

        line = f"- [ ] `{conv.file_path.name}` | "
        line += f"{hours}h{minutes}m | "
        line += f"{conv.metadata.message_count} msgs | "
        line += f"{conv.metadata.total_words:,} words"

        lines.append(line)
    else:
        # Full format (original)
        # Checkbox and session ID
        lines.append(f"- [ ] **Session ID**: {conv.metadata.session_id}")

        # Metadata section
        lines.append(f"  - **Project**: `{conv.metadata.project_path}`")
        lines.append(f"  - **File**: `{conv.file_path.name}`")
        lines.append(
            f"  - **Start Time**: {conv.metadata.start_time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        lines.append(
            f"  - **End Time**: {conv.metadata.end_time.strftime('%Y-%m-%d %H:%M:%S')}"
        )

        # Calculate duration
        duration = conv.metadata.end_time - conv.metadata.start_time
        hours = int(duration.total_seconds() // 3600)
        minutes = int((duration.total_seconds() % 3600) // 60)
        lines.append(f"  - **Duration**: {hours}h {minutes}m")

        # Statistics
        lines.append(f"  - **Messages**: {conv.metadata.message_count}")
        lines.append(f"  - **Total Words**: {conv.metadata.total_words:,}")

        if conv.metadata.has_code:
            lines.append("  - **Has Code**: Yes")
            if conv.metadata.primary_language:
                lines.append(
                    f"  - **Primary Language**: {conv.metadata.primary_language}"
                )

        # Summary of conversation (first user message)
        first_user_msg = next(
            (msg for msg in conv.messages if msg.role == "user"), None
        )
        if first_user_msg:
            preview = first_user_msg.content[:100].replace("\n", " ")
            if len(first_user_msg.content) > 100:
                preview += "..."
            lines.append(f'  - **First Request**: "{preview}"')

        lines.append("")  # Empty line between entries

    return "\n".join(lines)


def get_all_project_directories(claude_projects_dir: Path) -> list[Path]:
    """Get all project directories from Claude projects directory."""
    if not claude_projects_dir.exists():
        return []

    # Get all directories that look like encoded project paths
    project_dirs = []
    for item in claude_projects_dir.iterdir():
        if item.is_dir() and not item.name.startswith("."):
            project_dirs.append(item)

    return sorted(project_dirs)


def decode_project_path(encoded_name: str) -> str:
    """Decode project directory name back to original path."""
    # This is a best-effort decode - we can't perfectly reverse the encoding
    # since both spaces and slashes become hyphens
    # Replace hyphens with slashes
    decoded = encoded_name.replace("-", "/")
    # Add leading slash if not present
    if not decoded.startswith("/"):
        decoded = "/" + decoded
    return decoded


def encode_project_path(project_path: str) -> str:
    """Encode project path to match Claude's directory naming."""
    # Claude's encoding: keep leading slash as hyphen, replace other slashes and spaces
    # /Users/Duracula 1/Python-Projects/memory -> -Users-Duracula-1-Python-Projects-memory
    encoded = project_path.replace("/", "-").replace(" ", "-")
    return encoded


def main():
    parser = argparse.ArgumentParser(
        description="Generate list of all Claude Code chats"
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="chats.md",
        help="Output file path (default: chats.md)",
    )
    parser.add_argument(
        "--claude-dir",
        type=str,
        default=None,
        help="Override Claude projects directory",
    )
    parser.add_argument(
        "--project",
        "-p",
        type=str,
        default=None,
        help="Filter to specific project path",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Limit number of chats per project"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose output"
    )

    args = parser.parse_args()

    # Initialize chat parser
    chat_parser = ChatParser(claude_projects_dir=args.claude_dir)

    # Load existing chats for incremental update
    output_file = Path(args.output)
    existing_ids = load_existing_chats(output_file)

    if args.verbose:
        print(f"Found {len(existing_ids)} existing chat entries")

    # Collect all chats
    all_chats: list[ChatConversation] = []
    new_chats: list[ChatConversation] = []

    # Get all project directories
    if args.project:
        # Filter to specific project
        encoded_path = encode_project_path(args.project)

        specific_dir = chat_parser.claude_projects_dir / encoded_path
        if specific_dir.exists():
            project_dirs = [specific_dir]
        else:
            print(f"Warning: No chat directory found for project: {args.project}")
            project_dirs = []
    else:
        project_dirs = get_all_project_directories(chat_parser.claude_projects_dir)

    if args.verbose:
        print(f"Found {len(project_dirs)} project directories")

    for project_dir in project_dirs:
        # Decode project path
        project_path = args.project or decode_project_path(project_dir.name)

        if args.verbose:
            print(f"\nProcessing project: {project_path}")

        # The actual JSONL files are in the encoded directory, not the decoded path
        # So we need to use the project_dir directly
        chat_files = list(project_dir.glob("*.jsonl"))

        if args.verbose and chat_files:
            print(f"  Found {len(chat_files)} chat files")

        # Parse chats for this project
        conversations = []
        try:
            for chat_file in sorted(
                chat_files, key=lambda p: p.stat().st_mtime, reverse=True
            ):
                if args.limit and len(conversations) >= args.limit:
                    break

                conv = chat_parser.parse_jsonl(chat_file)
                if conv:
                    # Override the metadata project path with the decoded one
                    conv.metadata.project_path = project_path
                    conversations.append(conv)

            for conv in conversations:
                all_chats.append(conv)

                # Check if this is a new chat
                if conv.metadata.session_id not in existing_ids:
                    new_chats.append(conv)
                    if args.verbose:
                        print(f"  New chat: {conv.metadata.session_id}")

        except Exception as e:
            print(f"Error processing {project_path}: {e}")
            continue

    # Sort all chats by start time (oldest first)
    all_chats.sort(key=lambda c: c.metadata.start_time)

    if args.verbose:
        print(f"\nTotal chats found: {len(all_chats)}")
        print(f"New chats to add: {len(new_chats)}")

    # Generate output
    if output_file.exists() and new_chats:
        # Incremental update - prepend new chats
        with open(output_file, encoding="utf-8") as f:
            existing_content = f.read()

        # Find where to insert new chats (after header)
        lines = existing_content.split("\n")
        insert_index = 0

        # Skip header and metadata section
        for i, line in enumerate(lines):
            if line.strip() and not line.startswith("#") and not line.startswith("*"):
                insert_index = i
                break

        # Build new content in compact format
        new_entries = []
        for conv in sorted(new_chats, key=lambda c: c.metadata.start_time):
            new_entries.append(format_chat_entry(conv, compact=True))

        # Combine content
        if insert_index > 0:
            header_lines = lines[:insert_index]
            body_lines = lines[insert_index:]

            output_lines = header_lines + [""] + new_entries + body_lines
            output_content = "\n".join(output_lines)
        else:
            output_content = "\n".join(new_entries) + "\n" + existing_content

    else:
        # Full regeneration
        output_lines = [
            "# Claude Code Chat History",
            "",
            f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
            f"*Total Chats: {len(all_chats)}*",
            "",
            "---",
            "",
        ]

        # Add project path at top for compact format
        if len(all_chats) > 0:
            # Get the project path from first chat
            project_path = all_chats[0].metadata.project_path
            output_lines.insert(1, f"*All sessions from: {project_path}*")
            output_lines.insert(2, "")

        # Add all chats in compact format
        for conv in all_chats:
            output_lines.append(format_chat_entry(conv, compact=True))

        output_content = "\n".join(output_lines)

    # Write output
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(output_content)

    print(f"Successfully wrote {len(all_chats)} chats to {output_file}")
    if new_chats:
        print(f"Added {len(new_chats)} new chats")


if __name__ == "__main__":
    main()
