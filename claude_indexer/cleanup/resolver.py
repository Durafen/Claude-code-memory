"""
Conflict resolution engine for memory cleanup.

Resolves contradictory patterns intelligently using various strategies
based on content analysis, quality scores, and temporal information.
"""

import re
import logging
from typing import List, Dict, Any, Optional, Union
from dataclasses import dataclass
from enum import Enum
from datetime import datetime, timezone
import difflib

from .clustering import SimilarityCluster
from .scorer import QualityScore

logger = logging.getLogger(__name__)


class ResolutionAction(Enum):
    """Types of resolution actions for conflicting entries."""
    KEEP_HIGHEST_QUALITY = "keep_highest_quality"
    MERGE_COMPATIBLE = "merge_compatible"
    CREATE_HIERARCHY = "create_hierarchy"
    VERSION_SEPARATE = "version_separate"
    PLATFORM_SEPARATE = "platform_separate"
    ARCHIVE_OUTDATED = "archive_outdated"
    MANUAL_REVIEW = "manual_review"


@dataclass
class ResolutionPlan:
    """Plan for resolving conflicts in a cluster."""
    action: ResolutionAction
    entries_to_keep: List[Dict[str, Any]]
    entries_to_remove: List[Dict[str, Any]]
    entries_to_merge: List[Dict[str, Any]]
    merged_entry: Optional[Dict[str, Any]] = None
    reasoning: str = ""
    confidence: float = 0.0


class ConflictResolver:
    """Resolve contradictory patterns intelligently."""
    
    def __init__(self):
        """Initialize the conflict resolver."""
        self.logger = logging.getLogger(__name__)
        
        # Version patterns for different technologies
        self.version_patterns = {
            'python': r'python\s*[23]?\.[0-9]+|py[23]?[0-9]*',
            'node': r'node\s*[0-9]+\.[0-9]+|npm\s*[0-9]+',
            'java': r'java\s*[0-9]+|jdk\s*[0-9]+',
            'react': r'react\s*[0-9]+\.[0-9]+',
            'vue': r'vue\s*[0-9]+\.[0-9]+',
            'angular': r'angular\s*[0-9]+',
            'django': r'django\s*[0-9]+\.[0-9]+',
            'flask': r'flask\s*[0-9]+\.[0-9]+',
        }
        
        # Platform/OS patterns
        self.platform_patterns = {
            'windows': r'windows|win32|\.exe|powershell|cmd|registry',
            'macos': r'macos|darwin|homebrew|\.app|xcode',
            'linux': r'linux|ubuntu|debian|centos|redhat|apt|yum|systemctl',
            'docker': r'docker|dockerfile|container|kubernetes|k8s',
            'aws': r'aws|amazon|ec2|s3|lambda|cloudformation',
            'gcp': r'gcp|google cloud|gke|bigquery',
            'azure': r'azure|microsoft cloud'
        }
    
    async def resolve_conflicts(
        self, 
        cluster: SimilarityCluster,
        quality_scores: Optional[List[QualityScore]] = None
    ) -> ResolutionPlan:
        """
        Determine how to handle conflicting patterns in a cluster.
        
        Args:
            cluster: Cluster of similar entries
            quality_scores: Quality scores for cluster entries (optional)
            
        Returns:
            ResolutionPlan with recommended actions
        """
        if cluster.size < 2:
            return ResolutionPlan(
                action=ResolutionAction.KEEP_HIGHEST_QUALITY,
                entries_to_keep=cluster.entries,
                entries_to_remove=[],
                entries_to_merge=[],
                reasoning="Single entry cluster, no conflicts to resolve",
                confidence=1.0
            )
        
        # Analyze conflict types
        version_conflicts = self._detect_version_conflicts(cluster.entries)
        platform_conflicts = self._detect_platform_conflicts(cluster.entries)
        quality_conflicts = self._analyze_quality_differences(cluster.entries, quality_scores)
        content_conflicts = self._detect_content_conflicts(cluster.entries)
        temporal_conflicts = self._detect_temporal_conflicts(cluster.entries)
        
        # Determine resolution strategy based on conflict analysis
        if version_conflicts['has_conflicts']:
            return self._create_versioned_hierarchy(cluster, version_conflicts)
        elif platform_conflicts['has_conflicts']:
            return self._create_platform_hierarchy(cluster, platform_conflicts)
        elif quality_conflicts['significant_difference']:
            return self._keep_highest_quality(cluster, quality_scores)
        elif content_conflicts['are_compatible']:
            return self._merge_compatible_entries(cluster, quality_scores)
        elif temporal_conflicts['has_outdated']:
            return self._archive_outdated_entries(cluster, temporal_conflicts)
        else:
            return self._recommend_manual_review(cluster)
    
    def _detect_version_conflicts(self, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Detect version-specific conflicts in entries."""
        versions_found = {}
        has_conflicts = False
        
        for entry in entries:
            content = self._extract_content_text(entry)
            content_lower = content.lower()
            
            for tech, pattern in self.version_patterns.items():
                matches = re.findall(pattern, content_lower, re.IGNORECASE)
                if matches:
                    if tech not in versions_found:
                        versions_found[tech] = set()
                    versions_found[tech].update(matches)
        
        # Check if multiple versions exist for same technology
        for tech, versions in versions_found.items():
            if len(versions) > 1:
                has_conflicts = True
                break
        
        return {
            'has_conflicts': has_conflicts,
            'versions_found': {k: list(v) for k, v in versions_found.items()},
            'conflicting_technologies': [
                tech for tech, versions in versions_found.items() 
                if len(versions) > 1
            ]
        }
    
    def _detect_platform_conflicts(self, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Detect platform/OS-specific conflicts in entries."""
        platforms_found = set()
        entry_platforms = {}
        
        for i, entry in enumerate(entries):
            content = self._extract_content_text(entry)
            content_lower = content.lower()
            entry_platforms[i] = set()
            
            for platform, pattern in self.platform_patterns.items():
                if re.search(pattern, content_lower, re.IGNORECASE):
                    platforms_found.add(platform)
                    entry_platforms[i].add(platform)
        
        has_conflicts = len(platforms_found) > 1
        
        return {
            'has_conflicts': has_conflicts,
            'platforms_found': list(platforms_found),
            'entry_platforms': entry_platforms
        }
    
    def _analyze_quality_differences(
        self, 
        entries: List[Dict[str, Any]], 
        quality_scores: Optional[List[QualityScore]]
    ) -> Dict[str, Any]:
        """Analyze quality score differences in cluster."""
        if not quality_scores or len(quality_scores) != len(entries):
            return {'significant_difference': False}
        
        # Filter out None scores (auto-indexed entries)
        valid_scores = [score for score in quality_scores if score is not None]
        
        if len(valid_scores) < 2:
            return {'significant_difference': False}
        
        overall_scores = [score.overall for score in valid_scores]
        min_score = min(overall_scores)
        max_score = max(overall_scores)
        
        # Consider significant if difference > 0.3
        significant_difference = (max_score - min_score) > 0.3
        
        return {
            'significant_difference': significant_difference,
            'min_score': min_score,
            'max_score': max_score,
            'score_range': max_score - min_score,
            'valid_scores': valid_scores
        }
    
    def _detect_content_conflicts(self, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Detect content-based conflicts between entries."""
        contents = [self._extract_content_text(entry) for entry in entries]
        
        # Check for contradictory keywords
        contradiction_patterns = [
            (r'\bnot\s+recommended\b', r'\brecommended\b'),
            (r'\bavoid\b', r'\buse\b'),
            (r'\bdeprecated\b', r'\bcurrent\b'),
            (r'\bdon\'?t\s+use\b', r'\buse\b'),
            (r'\bfalse\b', r'\btrue\b'),
            (r'\bdisable\b', r'\benable\b'),
        ]
        
        conflicts_found = []
        
        for pattern_neg, pattern_pos in contradiction_patterns:
            neg_matches = []
            pos_matches = []
            
            for i, content in enumerate(contents):
                if re.search(pattern_neg, content, re.IGNORECASE):
                    neg_matches.append(i)
                if re.search(pattern_pos, content, re.IGNORECASE):
                    pos_matches.append(i)
            
            if neg_matches and pos_matches:
                conflicts_found.append({
                    'type': 'contradiction',
                    'pattern': (pattern_neg, pattern_pos),
                    'negative_entries': neg_matches,
                    'positive_entries': pos_matches
                })
        
        # Check content similarity for potential merging
        are_compatible = True
        if len(contents) > 1:
            # Use difflib to check similarity
            similarities = []
            for i in range(len(contents)):
                for j in range(i + 1, len(contents)):
                    ratio = difflib.SequenceMatcher(None, contents[i], contents[j]).ratio()
                    similarities.append(ratio)
            
            avg_similarity = sum(similarities) / len(similarities)
            # Consider compatible if average similarity > 0.6 and no major conflicts
            are_compatible = avg_similarity > 0.6 and len(conflicts_found) == 0
        
        return {
            'conflicts_found': conflicts_found,
            'are_compatible': are_compatible,
            'has_contradictions': len(conflicts_found) > 0
        }
    
    def _detect_temporal_conflicts(self, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Detect temporal conflicts (outdated vs current information)."""
        current_time = datetime.now(timezone.utc)
        outdated_entries = []
        temporal_info = []
        
        for i, entry in enumerate(entries):
            payload = entry.get('payload', entry)
            
            # Try to extract temporal information
            created_at = payload.get('created_at') or payload.get('timestamp')
            
            if created_at:
                try:
                    if isinstance(created_at, str):
                        # Try parsing common formats
                        for fmt in ['%Y-%m-%d', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S']:
                            try:
                                dt = datetime.strptime(created_at, fmt)
                                if dt.tzinfo is None:
                                    dt = dt.replace(tzinfo=timezone.utc)
                                temporal_info.append((i, dt))
                                break
                            except ValueError:
                                continue
                    elif isinstance(created_at, datetime):
                        temporal_info.append((i, created_at))
                except Exception:
                    pass
            
            # Check for outdated keywords in content
            content = self._extract_content_text(entry)
            outdated_keywords = [
                'deprecated', 'obsolete', 'legacy', 'old version', 'no longer',
                'discontinued', 'superseded', 'replaced by', 'outdated'
            ]
            
            for keyword in outdated_keywords:
                if keyword in content.lower():
                    outdated_entries.append(i)
                    break
        
        # Check age-based outdating (entries older than 2 years)
        age_threshold = current_time.replace(year=current_time.year - 2)
        
        for i, dt in temporal_info:
            if dt < age_threshold and i not in outdated_entries:
                outdated_entries.append(i)
        
        return {
            'has_outdated': len(outdated_entries) > 0,
            'outdated_entries': outdated_entries,
            'temporal_info': temporal_info
        }
    
    def _create_versioned_hierarchy(
        self, 
        cluster: SimilarityCluster, 
        version_info: Dict[str, Any]
    ) -> ResolutionPlan:
        """Create version-specific hierarchy for conflicting entries."""
        # Group entries by detected versions
        version_groups = {}
        unversioned = []
        
        for i, entry in enumerate(cluster.entries):
            content = self._extract_content_text(entry)
            content_lower = content.lower()
            
            assigned = False
            for tech in version_info['conflicting_technologies']:
                pattern = self.version_patterns[tech]
                matches = re.findall(pattern, content_lower, re.IGNORECASE)
                if matches:
                    version_key = f"{tech}_{matches[0]}"
                    if version_key not in version_groups:
                        version_groups[version_key] = []
                    version_groups[version_key].append(entry)
                    assigned = True
                    break
            
            if not assigned:
                unversioned.append(entry)
        
        return ResolutionPlan(
            action=ResolutionAction.VERSION_SEPARATE,
            entries_to_keep=cluster.entries,  # Keep all, but organize by version
            entries_to_remove=[],
            entries_to_merge=[],
            reasoning=f"Version conflicts detected in {version_info['conflicting_technologies']}. Creating versioned hierarchy.",
            confidence=0.8
        )
    
    def _create_platform_hierarchy(
        self, 
        cluster: SimilarityCluster, 
        platform_info: Dict[str, Any]
    ) -> ResolutionPlan:
        """Create platform-specific hierarchy for conflicting entries."""
        return ResolutionPlan(
            action=ResolutionAction.PLATFORM_SEPARATE,
            entries_to_keep=cluster.entries,  # Keep all, but organize by platform
            entries_to_remove=[],
            entries_to_merge=[],
            reasoning=f"Platform conflicts detected: {platform_info['platforms_found']}. Creating platform-specific hierarchy.",
            confidence=0.8
        )
    
    def _keep_highest_quality(
        self, 
        cluster: SimilarityCluster, 
        quality_scores: Optional[List[QualityScore]]
    ) -> ResolutionPlan:
        """Keep only the highest quality entry from the cluster."""
        if not quality_scores:
            # Fallback to keeping first entry
            return ResolutionPlan(
                action=ResolutionAction.KEEP_HIGHEST_QUALITY,
                entries_to_keep=[cluster.entries[0]],
                entries_to_remove=cluster.entries[1:],
                entries_to_merge=[],
                reasoning="No quality scores available. Keeping first entry.",
                confidence=0.3
            )
        
        # Find entry with highest quality score
        best_score = -1
        best_index = 0
        
        for i, score in enumerate(quality_scores):
            if score is not None and score.overall > best_score:
                best_score = score.overall
                best_index = i
        
        entries_to_keep = [cluster.entries[best_index]]
        entries_to_remove = [
            entry for i, entry in enumerate(cluster.entries) 
            if i != best_index
        ]
        
        return ResolutionPlan(
            action=ResolutionAction.KEEP_HIGHEST_QUALITY,
            entries_to_keep=entries_to_keep,
            entries_to_remove=entries_to_remove,
            entries_to_merge=[],
            reasoning=f"Keeping highest quality entry (score: {best_score:.2f})",
            confidence=0.9
        )
    
    def _merge_compatible_entries(
        self, 
        cluster: SimilarityCluster, 
        quality_scores: Optional[List[QualityScore]]
    ) -> ResolutionPlan:
        """Merge compatible entries into a single comprehensive entry."""
        # Create merged entry by combining information
        merged_content = []
        merged_name_parts = []
        
        for entry in cluster.entries:
            payload = entry.get('payload', entry)
            
            # Collect names
            name = payload.get('name', '')
            if name and name not in merged_name_parts:
                merged_name_parts.append(name)
            
            # Collect content
            content = self._extract_content_text(entry)
            if content and content not in merged_content:
                merged_content.append(content)
        
        # Create merged entry
        base_entry = cluster.entries[0].copy()
        base_payload = base_entry.get('payload', base_entry)
        
        merged_entry = {
            **base_entry,
            'payload': {
                **base_payload,
                'name': ' | '.join(merged_name_parts),
                'content': '\n\n---\n\n'.join(merged_content),
                'merged_from': [
                    entry.get('payload', entry).get('name', f'entry_{i}') 
                    for i, entry in enumerate(cluster.entries)
                ],
                'merge_timestamp': datetime.now(timezone.utc).isoformat()
            }
        }
        
        return ResolutionPlan(
            action=ResolutionAction.MERGE_COMPATIBLE,
            entries_to_keep=[],
            entries_to_remove=cluster.entries,
            entries_to_merge=cluster.entries,
            merged_entry=merged_entry,
            reasoning=f"Merging {cluster.size} compatible entries into comprehensive entry",
            confidence=0.7
        )
    
    def _archive_outdated_entries(
        self, 
        cluster: SimilarityCluster, 
        temporal_info: Dict[str, Any]
    ) -> ResolutionPlan:
        """Archive outdated entries while keeping current ones."""
        outdated_indices = set(temporal_info['outdated_entries'])
        
        entries_to_keep = [
            entry for i, entry in enumerate(cluster.entries)
            if i not in outdated_indices
        ]
        
        entries_to_archive = [
            entry for i, entry in enumerate(cluster.entries)
            if i in outdated_indices
        ]
        
        return ResolutionPlan(
            action=ResolutionAction.ARCHIVE_OUTDATED,
            entries_to_keep=entries_to_keep,
            entries_to_remove=entries_to_archive,
            entries_to_merge=[],
            reasoning=f"Archiving {len(entries_to_archive)} outdated entries",
            confidence=0.8
        )
    
    def _recommend_manual_review(self, cluster: SimilarityCluster) -> ResolutionPlan:
        """Recommend manual review for complex conflicts."""
        return ResolutionPlan(
            action=ResolutionAction.MANUAL_REVIEW,
            entries_to_keep=cluster.entries,
            entries_to_remove=[],
            entries_to_merge=[],
            reasoning="Complex conflicts detected that require manual review",
            confidence=0.1
        )
    
    def _extract_content_text(self, entry: Dict[str, Any]) -> str:
        """Extract text content from entry for analysis."""
        payload = entry.get('payload', entry)
        
        # Try different content fields
        content_fields = ['content', 'observations', 'description', 'pattern', 'solution']
        
        for field in content_fields:
            if field in payload:
                content = payload[field]
                if isinstance(content, str):
                    return content
                elif isinstance(content, list):
                    return ' '.join(str(item) for item in content)
        
        return str(payload)