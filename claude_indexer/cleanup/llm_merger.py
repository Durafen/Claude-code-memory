"""
LLM-powered intelligent merging for memory cleanup.

Uses GPT-4.1-mini for semantic merging of compatible entries, replacing
simple string concatenation with intelligent content synthesis.
"""

import json
import logging
import asyncio
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from datetime import datetime, timezone
import openai
from openai import AsyncOpenAI

from .clustering import SimilarityCluster

logger = logging.getLogger(__name__)


@dataclass
class MergeResult:
    """Result of LLM-powered merge operation."""
    merged_content: str
    merged_name: str
    confidence: float  # LLM confidence in merge quality (0.0-1.0)
    reasoning: str = ""  # LLM explanation of merge decisions
    source_count: int = 0  # Number of entries merged
    tokens_used: int = 0  # API token usage for cost tracking


class LLMMerger:
    """
    LLM-powered intelligent merging using GPT-4.1-mini.
    
    Provides semantic understanding of content overlap and creates
    coherent, well-structured merged entries instead of simple concatenation.
    83% cost reduction compared to GPT-4o while maintaining high quality.
    """
    
    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-4.1-mini", enabled: bool = True):
        """
        Initialize the LLM merger.
        
        Args:
            api_key: OpenAI API key (if None, uses environment)
            model: Model to use for merging (default: gpt-4.1-mini)
            enabled: Whether to use LLM merging (False falls back to simple concatenation)
        """
        self.model = model
        self.enabled = enabled
        self.client = AsyncOpenAI(api_key=api_key) if enabled else None
        self.logger = logging.getLogger(__name__)
        
        # Cost tracking
        self.total_tokens_used = 0
        self.total_api_calls = 0
    
    async def merge_compatible_entries(self, cluster: SimilarityCluster) -> MergeResult:
        """
        Merge compatible entries using LLM intelligence or fallback to simple merge.
        
        Args:
            cluster: Cluster of similar entries to merge
            
        Returns:
            MergeResult with merged content and metadata
        """
        if not self.enabled or not self.client:
            return self._fallback_simple_merge(cluster)
        
        if cluster.size < 2:
            # Single entry, no merging needed
            entry = cluster.entries[0]
            payload = entry.get('payload', entry)
            return MergeResult(
                merged_content=self._extract_content(entry),
                merged_name=payload.get('name', 'Unnamed Entry'),
                confidence=1.0,
                reasoning="Single entry, no merging required",
                source_count=1
            )
        
        try:
            # Use LLM for intelligent merging
            return await self._llm_merge(cluster)
        except Exception as e:
            self.logger.error(f"LLM merge failed, falling back to simple merge: {e}")
            return self._fallback_simple_merge(cluster)
    
    async def _llm_merge(self, cluster: SimilarityCluster) -> MergeResult:
        """Perform LLM-powered intelligent merging."""
        # Extract content from all entries
        entries_data = []
        for i, entry in enumerate(cluster.entries):
            payload = entry.get('payload', entry)
            entries_data.append({
                'id': i + 1,
                'name': payload.get('name', f'Entry {i+1}'),
                'type': payload.get('entityType', 'unknown'),
                'content': self._extract_content(entry)
            })
        
        # Build merge prompt
        prompt = self._build_merge_prompt(entries_data)
        
        # Call LLM
        result = await self._call_llm(prompt)
        
        if result is None:
            raise Exception("LLM returned no result")
        
        return MergeResult(
            merged_content=result['merged_content'],
            merged_name=result['merged_name'],
            confidence=result.get('confidence', 0.7),
            reasoning=result.get('reasoning', ''),
            source_count=cluster.size,
            tokens_used=result.get('tokens_used', 0)
        )
    
    def _build_merge_prompt(self, entries_data: List[Dict[str, Any]]) -> str:
        """Build prompt for LLM merging."""
        entries_text = ""
        for entry in entries_data:
            entries_text += f"""
## Entry {entry['id']}: {entry['name']}
**Type**: {entry['type']}
**Content**:
{entry['content']}

---
"""
        
        return f"""
You are an expert technical writer tasked with merging similar documentation/debugging entries into a single comprehensive entry.

**Entries to Merge:**
{entries_text}

**Instructions:**
1. **Analyze** all entries for overlapping and unique information
2. **Preserve** all unique insights and solutions from each entry
3. **Remove** redundant or duplicate information
4. **Organize** information logically with clear structure
5. **Maintain** technical accuracy and completeness
6. **Create** a coherent narrative that flows well

**Requirements:**
- Combine names intelligently (not just concatenation)
- Structure content with clear sections/headings if appropriate
- Preserve code examples, commands, and specific technical details
- Maintain the most comprehensive and current information
- Remove contradictions by keeping the most accurate information

**Output Format:**
Respond ONLY with valid JSON in this exact format:
{{
    "merged_name": "Intelligent combined name for the entry",
    "merged_content": "Comprehensive merged content with clear structure",
    "confidence": 0.X,
    "reasoning": "Brief explanation of merge decisions and what was preserved/removed"
}}
"""
    
    def _extract_content(self, entry: Dict[str, Any]) -> str:
        """Extract content from entry for merging."""
        payload = entry.get('payload', entry)
        
        # Try different content field names
        content_fields = ['content', 'observations', 'description', 'solution', 'pattern']
        
        for field in content_fields:
            if field in payload:
                content = payload[field]
                if isinstance(content, str):
                    return content
                elif isinstance(content, list):
                    return '\n'.join(str(item) for item in content)
                elif isinstance(content, dict):
                    return json.dumps(content, indent=2)
        
        # Fallback to string representation
        return str(payload)
    
    async def _call_llm(self, prompt: str) -> Optional[Dict[str, Any]]:
        """Call LLM with retry logic and error handling."""
        max_retries = 3
        retry_delay = 1.0
        
        for attempt in range(max_retries):
            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {
                            "role": "system",
                            "content": "You are an expert technical writer specializing in merging and consolidating documentation. Always respond with valid JSON only."
                        },
                        {
                            "role": "user",
                            "content": prompt
                        }
                    ],
                    temperature=0.2,  # Low temperature for consistent merging
                    max_tokens=1500,  # Allow longer responses for merged content
                    timeout=45.0
                )
                
                content = response.choices[0].message.content.strip()
                
                # Track usage
                if hasattr(response, 'usage'):
                    tokens_used = response.usage.total_tokens
                    self.total_tokens_used += tokens_used
                    self.total_api_calls += 1
                else:
                    tokens_used = 0
                
                # Parse JSON response
                try:
                    result = json.loads(content)
                    
                    # Validate response format
                    required_fields = ['merged_name', 'merged_content']
                    if not all(field in result for field in required_fields):
                        raise ValueError(f"Missing required fields: {required_fields}")
                    
                    # Add token usage info
                    result['tokens_used'] = tokens_used
                    
                    return result
                    
                except (json.JSONDecodeError, ValueError) as e:
                    self.logger.warning(f"Invalid LLM response format (attempt {attempt + 1}): {e}")
                    self.logger.debug(f"Raw response: {content}")
                    
                    if attempt == max_retries - 1:
                        return None
                    
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                
            except openai.RateLimitError:
                self.logger.warning(f"Rate limit hit (attempt {attempt + 1}), waiting...")
                await asyncio.sleep(retry_delay * 2)
                retry_delay *= 2
                
            except openai.APIError as e:
                self.logger.error(f"OpenAI API error (attempt {attempt + 1}): {e}")
                if attempt == max_retries - 1:
                    return None
                await asyncio.sleep(retry_delay)
                retry_delay *= 2
                
            except Exception as e:
                self.logger.error(f"Unexpected error during merge (attempt {attempt + 1}): {e}")
                if attempt == max_retries - 1:
                    return None
                await asyncio.sleep(retry_delay)
                retry_delay *= 2
        
        return None
    
    def _fallback_simple_merge(self, cluster: SimilarityCluster) -> MergeResult:
        """Fallback to simple string concatenation when LLM is disabled or fails."""
        merged_content = []
        merged_name_parts = []
        
        for entry in cluster.entries:
            payload = entry.get('payload', entry)
            
            # Collect names
            name = payload.get('name', '')
            if name and name not in merged_name_parts:
                merged_name_parts.append(name)
            
            # Collect content
            content = self._extract_content(entry)
            if content and content not in merged_content:
                merged_content.append(content)
        
        return MergeResult(
            merged_content='\n\n---\n\n'.join(merged_content),
            merged_name=' | '.join(merged_name_parts),
            confidence=0.5,  # Lower confidence for simple merge
            reasoning="Simple concatenation fallback (LLM disabled or failed)",
            source_count=cluster.size
        )
    
    def get_usage_stats(self) -> Dict[str, Any]:
        """Get API usage statistics for cost tracking."""
        return {
            'total_api_calls': self.total_api_calls,
            'total_tokens_used': self.total_tokens_used,
            'estimated_cost_usd': self.total_tokens_used * 0.0000001,  # GPT-4.1-mini pricing
            'enabled': self.enabled
        }