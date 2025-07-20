"""Chat summarization using OpenAI API for Claude Code conversations."""

import time
from dataclasses import dataclass, field
from typing import Any

import openai

from ..config import load_config
from .parser import ChatConversation


@dataclass
class SummaryResult:
    """Result of chat conversation summarization."""

    summary: str
    key_insights: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    category: str | None = None
    code_patterns: list[str] = field(default_factory=list)
    debugging_info: dict[str, Any] = field(default_factory=dict)
    token_count: int = 0

    @property
    def entity_type(self) -> str:
        """Get entity type for storage."""
        return "chat_history"

    def to_observations(self) -> list[str]:
        """Convert to observations for entity storage."""
        observations = [
            f"SUMMARY: {self.summary}",
        ]

        if self.key_insights:
            observations.append(f"KEY_INSIGHTS: {'; '.join(self.key_insights)}")

        if self.topics:
            observations.append(f"TOPICS: {', '.join(self.topics)}")

        if self.category:
            observations.append(f"CATEGORY: {self.category}")

        if self.code_patterns:
            observations.append(f"CODE_PATTERNS: {'; '.join(self.code_patterns)}")

        if self.debugging_info:
            for key, value in self.debugging_info.items():
                observations.append(f"DEBUG_{key.upper()}: {value}")

        return observations


class ChatSummarizer:
    """Summarizes Claude Code conversations using OpenAI API."""

    # 9-category mapping based on CLAUDE.md instructions
    CATEGORY_PATTERNS = {
        "debugging_pattern": [
            "error",
            "exception",
            "bug",
            "fix",
            "debug",
            "traceback",
            "stack trace",
            "memory leak",
            "crash",
            "failure",
            "issue",
            "problem",
            "troubleshoot",
        ],
        "implementation_pattern": [
            "class",
            "function",
            "method",
            "algorithm",
            "pattern",
            "best practice",
            "code",
            "solution",
            "implement",
            "create",
            "build",
            "develop",
        ],
        "integration_pattern": [
            "API",
            "service",
            "integration",
            "database",
            "authentication",
            "pipeline",
            "external",
            "third-party",
            "connect",
            "interface",
            "endpoint",
        ],
        "configuration_pattern": [
            "config",
            "environment",
            "deploy",
            "setup",
            "docker",
            "CI/CD",
            "install",
            "settings",
            "parameters",
            "variables",
            "build",
            "deployment",
        ],
        "architecture_pattern": [
            "architecture",
            "design",
            "structure",
            "component",
            "system",
            "module",
            "organization",
            "framework",
            "pattern",
            "design pattern",
        ],
        "performance_pattern": [
            "performance",
            "optimization",
            "scalability",
            "memory",
            "speed",
            "bottleneck",
            "cache",
            "efficient",
            "fast",
            "slow",
            "optimize",
            "scale",
        ],
        "knowledge_insight": [
            "research",
            "learning",
            "methodology",
            "strategy",
            "analysis",
            "insight",
            "findings",
            "discovery",
            "understanding",
            "lesson",
        ],
        "active_issue": [
            "active",
            "issue",
            "bug",
            "problem",
            "todo",
            "fixme",
            "hack",
            "workaround",
            "blocked",
            "investigate",
            "urgent",
            "critical",
            "blocker",
            "regression",
        ],
        "ideas": [
            "idea",
            "feature",
            "suggestion",
            "enhancement",
            "brainstorm",
            "concept",
            "proposal",
            "future",
            "roadmap",
            "vision",
            "inspiration",
            "innovation",
        ],
    }

    def __init__(self, config: dict[Any, Any] | None = None):
        """Initialize summarizer with OpenAI configuration."""
        if config is None:
            loaded_config = load_config()
            # Convert IndexerConfig to dict for compatibility
            config = loaded_config.__dict__ if hasattr(loaded_config, '__dict__') else {}

        self.config = config

        # Initialize OpenAI client
        api_key = self.config.get('openai_api_key') if isinstance(self.config, dict) else getattr(self.config, 'openai_api_key', None)
        if not api_key:
            raise ValueError("OpenAI API key not found in configuration")

        openai.api_key = api_key
        self.client = openai.OpenAI(api_key=api_key)

        # Rate limiting settings
        self.max_retries = 3
        self.base_delay = 1.0
        self.max_tokens = 4000

    def summarize_conversation(self, conversation: ChatConversation) -> SummaryResult:
        """Summarize a complete conversation."""
        try:
            # Prepare conversation text for summarization
            conversation_text = self._prepare_conversation_text(conversation)

            # Generate summary using OpenAI
            summary_response = self._call_openai_with_retry(
                self._create_summary_prompt(conversation_text)
            )

            # Parse and categorize the summary
            return self._parse_summary_response(summary_response, conversation)

        except Exception as e:
            # Return minimal summary on error
            return SummaryResult(
                summary=f"Error summarizing conversation: {str(e)}",
                topics=self._extract_basic_topics(conversation),
                debugging_info={"error": str(e)},
            )

    def _prepare_conversation_text(self, conversation: ChatConversation) -> str:
        """Prepare conversation text for OpenAI processing."""
        # GPT-4.1-mini supports 1M tokens - no need to truncate conversations
        # Use all messages for complete context
        messages = conversation.messages

        formatted_messages = []
        for msg in messages:
            role = msg.role.upper()
            # Keep full message content - 1M token context can handle it
            formatted_messages.append(f"{role}: {msg.content}")

        return "\n\n".join(formatted_messages)

    def _create_summary_prompt(self, conversation_text: str) -> str:
        """Create prompt for OpenAI summarization."""
        return f"""Analyze this Claude Code conversation and provide:

1. A concise summary (2-3 sentences) of what was accomplished
2. Key insights or solutions discovered
3. Main topics discussed
4. Code patterns or techniques used
5. Any debugging information or error resolution

Conversation:
{conversation_text}

Respond in JSON format:
{{
    "summary": "Brief summary of the conversation",
    "key_insights": ["insight1", "insight2"],
    "topics": ["topic1", "topic2"],
    "code_patterns": ["pattern1", "pattern2"],
    "debugging_info": {{"issue": "description", "solution": "fix"}}
}}"""

    def _call_openai_with_retry(self, prompt: str) -> dict[str, Any]:
        """Call OpenAI API with exponential backoff retry."""
        for attempt in range(self.max_retries):
            try:
                response = self.client.chat.completions.create(
                    model="gpt-4.1-mini",
                    messages=[
                        {
                            "role": "system",
                            "content": "You are a helpful assistant that analyzes coding conversations.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=self.max_tokens,
                    temperature=0.3,
                )

                content = response.choices[0].message.content

                if not content:
                    return {"summary": "No content received", "key_insights": []}

                # Try to parse as JSON
                import json

                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    # Fallback: extract text summary
                    return {
                        "summary": content[:500],
                        "key_insights": [],
                        "topics": [],
                        "code_patterns": [],
                        "debugging_info": {},
                    }

            except Exception as e:
                if attempt == self.max_retries - 1:
                    raise e

                # Exponential backoff
                delay = self.base_delay * (2**attempt)
                time.sleep(delay)

        raise Exception("Max retries exceeded")

    def _parse_summary_response(
        self, response: dict[str, Any], conversation: ChatConversation
    ) -> SummaryResult:
        """Parse OpenAI response into SummaryResult."""
        summary = response.get("summary", "Conversation summary unavailable")
        key_insights = response.get("key_insights", [])
        topics = response.get("topics", [])
        code_patterns = response.get("code_patterns", [])
        debugging_info = response.get("debugging_info", {})

        # Categorize based on content analysis
        category = self._categorize_conversation(summary, topics, key_insights)

        return SummaryResult(
            summary=summary,
            key_insights=key_insights,
            topics=topics,
            category=category,
            code_patterns=code_patterns,
            debugging_info=debugging_info,
            token_count=len(summary.split()),
        )

    def _categorize_conversation(
        self, summary: str, topics: list[str], insights: list[str]
    ) -> str:
        """Categorize conversation based on content analysis."""
        # Combine all text for analysis
        text_content = f"{summary} {' '.join(topics)} {' '.join(insights)}".lower()

        # Score each category based on keyword matches
        category_scores = {}
        for category, patterns in self.CATEGORY_PATTERNS.items():
            score = sum(1 for pattern in patterns if pattern in text_content)
            if score > 0:
                category_scores[category] = score

        # Return category with highest score, default to implementation_pattern
        if category_scores:
            return max(category_scores, key=lambda k: category_scores[k])

        return "implementation_pattern"

    def _extract_basic_topics(self, conversation: ChatConversation) -> list[str]:
        """Extract basic topics when OpenAI fails."""
        topics = []

        # Use metadata if available
        if (
            hasattr(conversation.metadata, "primary_language")
            and conversation.metadata.primary_language
        ):
            topics.append(conversation.metadata.primary_language)

        # Extract from first few messages
        for msg in conversation.messages[:5]:
            if any(
                keyword in msg.content.lower() for keyword in ["error", "debug", "fix"]
            ):
                topics.append("debugging")
                break

        if any(msg.is_code_heavy for msg in conversation.messages[:10]):
            topics.append("coding")

        return topics or ["general"]

    def batch_summarize(
        self, conversations: list[ChatConversation]
    ) -> list[SummaryResult]:
        """Summarize multiple conversations with rate limiting."""
        results = []

        for i, conversation in enumerate(conversations):
            try:
                result = self.summarize_conversation(conversation)
                results.append(result)

                # Rate limiting: small delay between calls
                if i < len(conversations) - 1:
                    time.sleep(0.5)

            except Exception as e:
                # Continue with other conversations on individual failures
                results.append(
                    SummaryResult(
                        summary=f"Failed to summarize: {str(e)}",
                        debugging_info={"error": str(e)},
                    )
                )

        return results
