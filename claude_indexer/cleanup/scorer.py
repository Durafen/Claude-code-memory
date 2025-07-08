"""
LLM-based quality scoring for memory cleanup.

Uses GPT-4.1-mini for cost-effective quality assessment of manual entries.
Implements multi-dimensional scoring framework with weighted aggregation.
"""

import json
import logging
import asyncio
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import openai
from openai import AsyncOpenAI

from .detection import is_manual_entry

logger = logging.getLogger(__name__)


@dataclass
class QualityScore:
    """Quality score for a memory entry."""
    accuracy: float  # Solution correctness (0.0-1.0)
    completeness: float  # Information completeness (0.0-1.0)
    specificity: float  # Problem definition clarity (0.0-1.0)
    reusability: float  # Cross-context applicability (0.0-1.0)
    recency: float  # Current best practices alignment (0.0-1.0)
    overall: float  # Weighted overall score (0.0-1.0)
    reasoning: str = ""  # LLM reasoning for the score
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class QualityScorer:
    """
    LLM-based quality assessment using GPT-4.1-mini for patterns.
    
    Provides cost-effective quality scoring with 83% cost reduction compared to GPT-4o.
    Works with dynamic field-based detection for any entity type.
    """
    
    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-4.1-mini"):
        """
        Initialize the quality scorer.
        
        Args:
            api_key: OpenAI API key (if None, uses environment)
            model: Model to use for scoring (default: gpt-4.1-mini, 83% cheaper than GPT-4o)
        """
        self.model = model
        self.client = AsyncOpenAI(api_key=api_key)
        self.quality_weights = {
            "accuracy": 0.3,      # Solution correctness weight
            "completeness": 0.25, # Information completeness
            "specificity": 0.2,   # Problem-solution specificity  
            "reusability": 0.15,  # Cross-context applicability
            "recency": 0.1        # Temporal relevance
        }
        self.logger = logging.getLogger(__name__)
    
    async def score_manual_entry(self, entry: Dict[str, Any]) -> Optional[QualityScore]:
        """
        Score entries using dynamic field-based detection.
        Works with ANY entity type - no hardcoded lists needed.
        
        Args:
            entry: Entry to score (can be raw entry or with payload wrapper)
            
        Returns:
            QualityScore object or None if entry is auto-indexed
        """
        # Extract payload for dynamic detection
        payload = entry.get('payload', entry)
        
        # Dynamic detection - works for any current or future entity type
        if not is_manual_entry(payload):
            self.logger.debug(f"Skipping auto-indexed entry: {payload.get('name', 'unknown')}")
            return None
            
        # Score any manual entry regardless of its entityType
        try:
            prompt = self._build_scoring_prompt(payload)
            scores = await self._call_llm(prompt)
            
            if scores is None:
                return None
            
            return QualityScore(
                accuracy=scores['accuracy'],
                completeness=scores['completeness'],
                specificity=scores['specificity'],
                reusability=scores['reusability'],
                recency=scores['recency'],
                overall=self._calculate_weighted_score(scores),
                reasoning=scores.get('reasoning', '')
            )
            
        except Exception as e:
            self.logger.error(f"Error scoring entry {payload.get('name', 'unknown')}: {e}")
            return None
    
    def _build_scoring_prompt(self, entry: Dict[str, Any]) -> str:
        """Generate scoring prompt that works for ANY entity type."""
        entity_type = entry.get('entityType', 'unknown')
        name = entry.get('name', 'Unnamed Entry')
        content = self._extract_content(entry)
        
        return f"""
Evaluate this {entity_type} entry for quality across multiple dimensions.

**Entry Details:**
- Name: {name}
- Type: {entity_type}
- Content: {content[:1000]}{'...' if len(content) > 1000 else ''}

**Scoring Instructions:**
Rate each dimension from 0.0 to 1.0 based on the criteria below:

1. **Accuracy (0.0-1.0)**: Is the information correct and factually sound?
   - 1.0: Completely accurate, well-tested solution
   - 0.5: Mostly accurate with minor issues
   - 0.0: Contains errors or incorrect information

2. **Completeness (0.0-1.0)**: Does it provide sufficient detail to be actionable?
   - 1.0: Complete solution with examples and context
   - 0.5: Basic information but missing some details
   - 0.0: Incomplete or fragmentary information

3. **Specificity (0.0-1.0)**: How well-defined is the problem and solution?
   - 1.0: Clear problem definition with specific solution steps
   - 0.5: Somewhat specific but could be clearer
   - 0.0: Vague or overly general

4. **Reusability (0.0-1.0)**: Can this be applied to similar situations?
   - 1.0: Highly reusable pattern or principle
   - 0.5: Moderately reusable with some adaptation
   - 0.0: Very specific to one narrow case

5. **Recency (0.0-1.0)**: Does it reflect current best practices?
   - 1.0: Uses modern, current best practices
   - 0.5: Somewhat current but could be updated
   - 0.0: Outdated or deprecated approaches

**Output Format:**
Respond ONLY with valid JSON in this exact format:
{{
    "accuracy": 0.X,
    "completeness": 0.X,
    "specificity": 0.X,
    "reusability": 0.X,
    "recency": 0.X,
    "reasoning": "Brief explanation of your scoring rationale"
}}
"""
    
    def _extract_content(self, entry: Dict[str, Any]) -> str:
        """Extract content from entry in various formats."""
        # Try different content field names
        content_fields = ['content', 'observations', 'description', 'solution', 'pattern']
        
        for field in content_fields:
            if field in entry:
                content = entry[field]
                if isinstance(content, str):
                    return content
                elif isinstance(content, list):
                    return ' '.join(str(item) for item in content)
                elif isinstance(content, dict):
                    return json.dumps(content, indent=2)
        
        # Fallback to string representation of entire entry
        return str(entry)
    
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
                            "content": "You are a technical expert evaluating the quality of documentation and debugging patterns. Always respond with valid JSON only."
                        },
                        {
                            "role": "user", 
                            "content": prompt
                        }
                    ],
                    temperature=0.1,  # Low temperature for consistent scoring
                    max_tokens=500,
                    timeout=30.0
                )
                
                content = response.choices[0].message.content.strip()
                
                # Clean up JSON if wrapped in code blocks
                if content.startswith('```json'):
                    content = content.replace('```json', '').replace('```', '').strip()
                elif content.startswith('```'):
                    content = content.replace('```', '').strip()
                
                # Parse JSON response
                try:
                    scores = json.loads(content)
                    
                    # Validate score format
                    required_fields = ['accuracy', 'completeness', 'specificity', 'reusability', 'recency']
                    
                    if not all(field in scores for field in required_fields):
                        raise ValueError(f"Missing required fields: {required_fields}")
                    
                    # Validate score ranges
                    for field in required_fields:
                        score = scores[field]
                        if not isinstance(score, (int, float)) or score < 0.0 or score > 1.0:
                            raise ValueError(f"Invalid score for {field}: {score}")
                    
                    return scores
                    
                except (json.JSONDecodeError, ValueError) as e:
                    self.logger.warning(f"Invalid LLM response format (attempt {attempt + 1}): {e}")
                    self.logger.warning(f"Raw response: '{content}'")
                    
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
                self.logger.error(f"Unexpected error scoring entry (attempt {attempt + 1}): {e}")
                if attempt == max_retries - 1:
                    return None
                await asyncio.sleep(retry_delay)
                retry_delay *= 2
        
        return None
    
    def _calculate_weighted_score(self, scores: Dict[str, Any]) -> float:
        """Calculate weighted overall score."""
        weighted_sum = 0.0
        total_weight = 0.0
        
        for dimension, weight in self.quality_weights.items():
            if dimension in scores:
                weighted_sum += scores[dimension] * weight
                total_weight += weight
        
        return weighted_sum / total_weight if total_weight > 0 else 0.0
    
    async def score_batch(
        self, 
        entries: List[Dict[str, Any]], 
        max_concurrent: int = 5
    ) -> List[Optional[QualityScore]]:
        """
        Score multiple entries concurrently with rate limiting.
        
        Args:
            entries: List of entries to score
            max_concurrent: Maximum concurrent API calls
            
        Returns:
            List of QualityScore objects (None for auto-indexed entries)
        """
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def score_with_semaphore(entry):
            async with semaphore:
                return await self.score_manual_entry(entry)
        
        tasks = [score_with_semaphore(entry) for entry in entries]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Handle exceptions
        scores = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                self.logger.error(f"Error scoring entry {i}: {result}")
                scores.append(None)
            else:
                scores.append(result)
        
        return scores
    
    def filter_by_quality(
        self, 
        entries_with_scores: List[tuple], 
        min_threshold: float = 0.6
    ) -> tuple:
        """
        Filter entries by quality threshold.
        
        Args:
            entries_with_scores: List of (entry, QualityScore) tuples
            min_threshold: Minimum quality score to keep
            
        Returns:
            Tuple of (high_quality_entries, low_quality_entries)
        """
        high_quality = []
        low_quality = []
        
        for entry, score in entries_with_scores:
            if score is None:
                # Auto-indexed entries are always preserved
                continue
            elif score.overall >= min_threshold:
                high_quality.append((entry, score))
            else:
                low_quality.append((entry, score))
        
        return high_quality, low_quality
    
    def get_quality_statistics(
        self, 
        scores: List[Optional[QualityScore]]
    ) -> Dict[str, Any]:
        """Calculate quality statistics for a set of scores."""
        valid_scores = [s for s in scores if s is not None]
        
        if not valid_scores:
            return {}
        
        stats = {
            'total_scored': len(valid_scores),
            'dimensions': {}
        }
        
        # Calculate statistics for each dimension
        for dimension in ['accuracy', 'completeness', 'specificity', 'reusability', 'recency', 'overall']:
            values = [getattr(score, dimension) for score in valid_scores]
            stats['dimensions'][dimension] = {
                'mean': sum(values) / len(values),
                'min': min(values),
                'max': max(values),
                'std': (sum((x - sum(values) / len(values)) ** 2 for x in values) / len(values)) ** 0.5
            }
        
        # Quality distribution
        overall_scores = [score.overall for score in valid_scores]
        stats['quality_distribution'] = {
            'excellent': len([s for s in overall_scores if s >= 0.8]),
            'good': len([s for s in overall_scores if 0.6 <= s < 0.8]),
            'fair': len([s for s in overall_scores if 0.4 <= s < 0.6]),
            'poor': len([s for s in overall_scores if s < 0.4])
        }
        
        return stats