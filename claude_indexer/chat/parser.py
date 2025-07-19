"""Parser for Claude Code JSONL conversation files."""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class ChatMessage:
    """Single message in a Claude Code conversation."""

    role: str  # 'user' or 'assistant'
    content: str
    timestamp: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def word_count(self) -> int:
        """Get word count of the message."""
        return len(self.content.split())

    @property
    def is_code_heavy(self) -> bool:
        """Check if message contains significant code content."""
        code_indicators = ["```", "def ", "class ", "import ", "function", "{", "}"]
        return any(indicator in self.content for indicator in code_indicators)


@dataclass
class ChatMetadata:
    """Metadata extracted from a chat conversation."""

    project_path: str
    session_id: str
    start_time: datetime
    end_time: datetime
    message_count: int
    total_words: int
    has_code: bool
    primary_language: str | None = None
    topics: list[str] = field(default_factory=list)

    @property
    def duration_minutes(self) -> float:
        """Get conversation duration in minutes."""
        return (self.end_time - self.start_time).total_seconds() / 60

    @property
    def is_inactive(self, threshold_hours: float = 1.0) -> bool:
        """Check if conversation has been inactive for threshold hours."""
        time_since_last = datetime.now() - self.end_time
        return time_since_last.total_seconds() / 3600 > threshold_hours


@dataclass
class ChatConversation:
    """Complete conversation from Claude Code."""

    messages: list[ChatMessage]
    metadata: ChatMetadata
    file_path: Path

    @property
    def session_hash(self) -> str:
        """Get unique hash for this conversation session."""
        content = f"{self.metadata.session_id}:{self.metadata.start_time}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    @property
    def summary_key(self) -> str:
        """Get key for deduplication in vector store."""
        return f"chat_history:{self.metadata.project_path}:{self.session_hash}"


class ChatParser:
    """Parser for Claude Code JSONL conversation files."""

    def __init__(self, claude_projects_dir: Path | None = None):
        """Initialize parser with Claude projects directory."""
        if claude_projects_dir is None:
            claude_projects_dir = Path.home() / ".claude" / "projects"
        self.claude_projects_dir = Path(claude_projects_dir)

    def get_project_chat_directory(self, project_path: Path) -> Path:
        """Map project path to Claude chat directory."""
        # Encode project path by replacing slashes with hyphens
        encoded_path = str(project_path).replace("/", "-")
        if encoded_path.startswith("-"):
            encoded_path = encoded_path[1:]

        return self.claude_projects_dir / encoded_path

    def get_chat_files(self, project_path: Path) -> list[Path]:
        """Get all JSONL files for a project."""
        chat_dir = self.get_project_chat_directory(project_path)

        if not chat_dir.exists():
            return []

        # Get all .jsonl files, sorted by modification time (newest first)
        jsonl_files = sorted(
            chat_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
        )

        return jsonl_files

    def parse_jsonl(self, file_path: Path) -> ChatConversation | None:
        """Parse a single JSONL file into a conversation."""
        try:
            messages = []
            session_id = file_path.stem  # Use filename as session ID

            with open(file_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        data = json.loads(line)
                        message = self._parse_message(data)
                        if message:
                            messages.append(message)
                    except json.JSONDecodeError as e:
                        print(f"Skipping malformed JSON line in {file_path}: {e}")
                        continue

            if not messages:
                return None

            # Extract metadata
            metadata = self._extract_metadata(messages, file_path, session_id)

            return ChatConversation(
                messages=messages, metadata=metadata, file_path=file_path
            )

        except Exception as e:
            print(f"Error parsing {file_path}: {e}")
            return None

    def _parse_message(self, data: dict[str, Any]) -> ChatMessage | None:
        """Parse individual message from JSONL data."""
        # Handle different possible JSONL formats

        # Claude Code format with nested message
        if "message" in data and isinstance(data["message"], dict):
            message_data = data["message"]
            if "role" in message_data and "content" in message_data:
                # Handle content as string or array
                content = message_data["content"]
                if isinstance(content, list):
                    # Extract text from content array (Claude format)
                    text_parts = []
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            text_parts.append(item.get("text", ""))
                    content = "\n".join(text_parts)
                elif not isinstance(content, str):
                    content = str(content)

                return ChatMessage(
                    role=message_data["role"],
                    content=content,
                    timestamp=self._parse_timestamp(data.get("timestamp")),
                    metadata=data,
                )

        # Standard format
        if "role" in data and "content" in data:
            return ChatMessage(
                role=data["role"],
                content=data["content"],
                timestamp=self._parse_timestamp(data.get("timestamp")),
                metadata=data.get("metadata", {}),
            )
        elif "type" in data:
            # Alternative format with type field
            role = "assistant" if data["type"] == "response" else "user"
            content = data.get("text", data.get("content", ""))
            return ChatMessage(
                role=role,
                content=content,
                timestamp=self._parse_timestamp(data.get("timestamp")),
                metadata=data,
            )

        return None

    def _parse_timestamp(self, timestamp: Any) -> datetime | None:
        """Parse timestamp from various formats."""
        if timestamp is None:
            return None

        if isinstance(timestamp, (int, float)):
            # Unix timestamp - return as naive datetime
            return datetime.fromtimestamp(timestamp)
        elif isinstance(timestamp, str):
            # ISO format
            try:
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                # Convert to naive datetime for consistent comparison
                if dt.tzinfo is not None:
                    # Convert to UTC then remove timezone info
                    dt = dt.replace(tzinfo=None)
                return dt
            except:
                return None

        return None

    def _extract_metadata(
        self, messages: list[ChatMessage], file_path: Path, session_id: str
    ) -> ChatMetadata:
        """Extract metadata from messages."""
        # Get project path from file location
        project_dir_name = file_path.parent.name
        # Decode project path by replacing hyphens with slashes
        project_path = "/" + project_dir_name.replace("-", "/")

        # Calculate timestamps
        timestamps = [msg.timestamp for msg in messages if msg.timestamp]
        if timestamps:
            start_time = min(timestamps)
            end_time = max(timestamps)
        else:
            # Fall back to file times
            stat = file_path.stat()
            start_time = datetime.fromtimestamp(stat.st_ctime)
            end_time = datetime.fromtimestamp(stat.st_mtime)

        # Calculate statistics
        total_words = sum(msg.word_count for msg in messages)
        has_code = any(msg.is_code_heavy for msg in messages)

        # Detect primary language from code blocks
        primary_language = self._detect_primary_language(messages)

        return ChatMetadata(
            project_path=project_path,
            session_id=session_id,
            start_time=start_time,
            end_time=end_time,
            message_count=len(messages),
            total_words=total_words,
            has_code=has_code,
            primary_language=primary_language,
        )

    def _detect_primary_language(self, messages: list[ChatMessage]) -> str | None:
        """Detect primary programming language from code blocks."""
        language_counts: dict[str, int] = {}

        for msg in messages:
            # Look for code blocks with language specifiers
            import re

            code_blocks = re.findall(r"```(\w+)\n", msg.content)
            for lang in code_blocks:
                if lang.lower() not in ["bash", "shell", "text", "plaintext"]:
                    language_counts[lang.lower()] = (
                        language_counts.get(lang.lower(), 0) + 1
                    )

        if language_counts:
            return max(language_counts, key=lambda x: language_counts[x])
        return None

    def get_inactive_conversations(
        self, project_path: Path, threshold_hours: float = 1.0
    ) -> list[Path]:
        """Get chat files that have been inactive for threshold hours."""
        inactive_files = []
        chat_files = self.get_chat_files(project_path)

        for file_path in chat_files:
            # Check last modification time
            mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
            time_since_modified = datetime.now() - mtime

            if time_since_modified.total_seconds() / 3600 > threshold_hours:
                inactive_files.append(file_path)

        return inactive_files

    def parse_all_chats(
        self, project_path: Path, limit: int | None = None
    ) -> list[ChatConversation]:
        """Parse all chat files for a project."""
        chat_files = self.get_chat_files(project_path)

        if limit:
            chat_files = chat_files[:limit]

        conversations = []
        for file_path in chat_files:
            conversation = self.parse_jsonl(file_path)
            if conversation:
                conversations.append(conversation)

        return conversations
