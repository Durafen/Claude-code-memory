"""
Similarity clustering engine for memory cleanup.

Groups similar manual entries using existing embeddings to identify
potential duplicates and conflicts. Uses dynamic field-based detection
to work with any entity type.
"""

import logging
from typing import List, Dict, Any, Set, Optional, Tuple
import numpy as np
from dataclasses import dataclass
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.cluster import AgglomerativeClustering

from .detection import is_manual_entry

logger = logging.getLogger(__name__)


@dataclass
class SimilarityCluster:
    """Represents a cluster of similar entries."""
    entries: List[Dict[str, Any]]
    centroid_embedding: Optional[np.ndarray] = None
    avg_similarity: float = 0.0
    cluster_id: str = ""
    
    @property
    def size(self) -> int:
        return len(self.entries)
    
    @property
    def entity_types(self) -> Set[str]:
        """Get unique entity types in this cluster."""
        return {entry.get('entityType', 'unknown') for entry in self.entries}


class SimilarityClusterer:
    """
    Groups similar manual entries using existing embeddings.
    
    This clustering engine is entity-type agnostic and works with ANY entity type,
    present or future, by examining field structure rather than type names.
    """
    
    def __init__(self, similarity_threshold: float = 0.85):
        """
        Initialize the clusterer.
        
        Args:
            similarity_threshold: Minimum cosine similarity for clustering (0.0-1.0)
        """
        self.similarity_threshold = similarity_threshold
        self.logger = logging.getLogger(__name__)
    
    def cluster_manual_patterns(
        self, 
        patterns: List[Dict[str, Any]], 
        threshold: float = None
    ) -> List[SimilarityCluster]:
        """
        Cluster manual entries by similarity using dynamic field-based detection.
        
        This method is entity-type agnostic and works with ANY entity type,
        present or future, by examining field structure rather than type names.
        
        Process:
        1. Filter entries using is_manual_entry() - excludes ANY entry with automation fields
        2. Group remaining manual entries by cosine similarity > threshold  
        3. Return clusters for duplicate/conflict detection
        
        The clustering works identically whether the entity type is:
        - Traditional: debugging_pattern, implementation_pattern, etc.
        - Domain-specific: ml_experiment, security_vulnerability, etc.
        - Future types: Any new category users create
        
        Args:
            patterns: List of entries with embeddings to cluster
            threshold: Override default similarity threshold
            
        Returns:
            List of SimilarityCluster objects containing grouped entries
        """
        if threshold is None:
            threshold = self.similarity_threshold
            
        # Dynamic field-based filtering - works with any entity type
        manual_entries = [p for p in patterns if is_manual_entry(p.get('payload', {}))]
        
        self.logger.info(f"Filtered {len(manual_entries)} manual entries from {len(patterns)} total")
        
        if len(manual_entries) < 2:
            return [SimilarityCluster(entries=manual_entries)] if manual_entries else []
        
        # Extract embeddings from manual entries
        embeddings = []
        valid_entries = []
        
        for entry in manual_entries:
            embedding = self._extract_embedding(entry)
            if embedding is not None:
                embeddings.append(embedding)
                valid_entries.append(entry)
        
        if len(embeddings) < 2:
            return [SimilarityCluster(entries=valid_entries)] if valid_entries else []
        
        # Perform similarity-based clustering
        clusters = self._cluster_by_similarity(valid_entries, embeddings, threshold)
        
        self.logger.info(f"Created {len(clusters)} clusters from {len(valid_entries)} manual entries")
        
        return clusters
    
    def _extract_embedding(self, entry: Dict[str, Any]) -> Optional[np.ndarray]:
        """
        Extract embedding vector from entry.
        
        Supports multiple embedding formats from different storage backends.
        """
        # Try different embedding field names
        embedding_fields = ['embedding', 'vector', 'embeddings']
        
        for field in embedding_fields:
            if field in entry:
                embedding = entry[field]
                if isinstance(embedding, (list, np.ndarray)):
                    return np.array(embedding)
        
        # Check nested payload
        payload = entry.get('payload', {})
        for field in embedding_fields:
            if field in payload:
                embedding = payload[field]
                if isinstance(embedding, (list, np.ndarray)):
                    return np.array(embedding)
        
        self.logger.warning(f"No embedding found for entry: {entry.get('id', 'unknown')}")
        return None
    
    def _cluster_by_similarity(
        self, 
        entries: List[Dict[str, Any]], 
        embeddings: List[np.ndarray], 
        threshold: float
    ) -> List[SimilarityCluster]:
        """
        Perform agglomerative clustering based on cosine similarity.
        """
        if len(embeddings) < 2:
            return [SimilarityCluster(entries=entries)]
        
        # Calculate similarity matrix
        embeddings_matrix = np.array(embeddings)
        similarity_matrix = cosine_similarity(embeddings_matrix)
        
        # Convert similarity to distance (for clustering)
        distance_matrix = 1 - similarity_matrix
        
        # Perform agglomerative clustering
        # Use distance_threshold instead of n_clusters for automatic cluster count
        clustering = AgglomerativeClustering(
            metric='precomputed',
            linkage='average',
            distance_threshold=1 - threshold,  # Convert similarity threshold to distance
            n_clusters=None
        )
        
        cluster_labels = clustering.fit_predict(distance_matrix)
        
        # Group entries by cluster labels
        clusters_dict = {}
        for i, label in enumerate(cluster_labels):
            if label not in clusters_dict:
                clusters_dict[label] = []
            clusters_dict[label].append(entries[i])
        
        # Create SimilarityCluster objects
        clusters = []
        for cluster_id, cluster_entries in clusters_dict.items():
            # Calculate cluster statistics
            cluster_indices = [i for i, label in enumerate(cluster_labels) if label == cluster_id]
            cluster_embeddings = embeddings_matrix[cluster_indices]
            
            # Calculate centroid and average similarity
            centroid = np.mean(cluster_embeddings, axis=0)
            cluster_similarities = []
            
            for i in range(len(cluster_embeddings)):
                for j in range(i + 1, len(cluster_embeddings)):
                    sim = cosine_similarity([cluster_embeddings[i]], [cluster_embeddings[j]])[0, 0]
                    cluster_similarities.append(sim)
            
            avg_similarity = np.mean(cluster_similarities) if cluster_similarities else 1.0
            
            cluster = SimilarityCluster(
                entries=cluster_entries,
                centroid_embedding=centroid,
                avg_similarity=avg_similarity,
                cluster_id=f"cluster_{cluster_id}"
            )
            clusters.append(cluster)
        
        # Sort clusters by size (largest first)
        clusters.sort(key=lambda c: c.size, reverse=True)
        
        return clusters
    
    def find_potential_duplicates(
        self, 
        patterns: List[Dict[str, Any]], 
        strict_threshold: float = 0.95
    ) -> List[Tuple[Dict[str, Any], Dict[str, Any], float]]:
        """
        Find potential duplicate entries with very high similarity.
        
        Args:
            patterns: List of entries to analyze
            strict_threshold: High similarity threshold for duplicate detection
            
        Returns:
            List of tuples (entry1, entry2, similarity_score) for potential duplicates
        """
        manual_entries = [p for p in patterns if is_manual_entry(p.get('payload', {}))]
        duplicates = []
        
        if len(manual_entries) < 2:
            return duplicates
        
        # Extract embeddings
        embeddings = []
        valid_entries = []
        
        for entry in manual_entries:
            embedding = self._extract_embedding(entry)
            if embedding is not None:
                embeddings.append(embedding)
                valid_entries.append(entry)
        
        if len(embeddings) < 2:
            return duplicates
        
        # Calculate pairwise similarities
        embeddings_matrix = np.array(embeddings)
        similarity_matrix = cosine_similarity(embeddings_matrix)
        
        # Find pairs above strict threshold
        for i in range(len(valid_entries)):
            for j in range(i + 1, len(valid_entries)):
                similarity = similarity_matrix[i, j]
                if similarity >= strict_threshold:
                    duplicates.append((valid_entries[i], valid_entries[j], similarity))
        
        # Sort by similarity (highest first)
        duplicates.sort(key=lambda x: x[2], reverse=True)
        
        return duplicates
    
    def analyze_cluster_diversity(self, cluster: SimilarityCluster) -> Dict[str, Any]:
        """
        Analyze the diversity within a cluster.
        
        Returns statistics about entity types, content patterns, and potential conflicts.
        """
        if not cluster.entries:
            return {}
        
        analysis = {
            'size': cluster.size,
            'entity_types': list(cluster.entity_types),
            'entity_type_counts': {},
            'content_lengths': [],
            'unique_names': set(),
            'creation_dates': [],
            'avg_similarity': cluster.avg_similarity
        }
        
        # Analyze entry characteristics
        for entry in cluster.entries:
            payload = entry.get('payload', entry)
            
            # Entity type distribution
            entity_type = payload.get('entityType', 'unknown')
            analysis['entity_type_counts'][entity_type] = \
                analysis['entity_type_counts'].get(entity_type, 0) + 1
            
            # Content characteristics
            content = payload.get('content', payload.get('observations', ''))
            if isinstance(content, str):
                analysis['content_lengths'].append(len(content))
            elif isinstance(content, list):
                analysis['content_lengths'].append(sum(len(str(c)) for c in content))
            
            # Unique identifiers
            name = payload.get('name', '')
            if name:
                analysis['unique_names'].add(name)
            
            # Temporal information (if available)
            created_at = payload.get('created_at', payload.get('timestamp'))
            if created_at:
                analysis['creation_dates'].append(created_at)
        
        # Calculate diversity metrics
        analysis['name_diversity'] = len(analysis['unique_names']) / cluster.size
        analysis['type_diversity'] = len(analysis['entity_types']) / cluster.size
        analysis['dominant_type'] = max(analysis['entity_type_counts'], 
                                      key=analysis['entity_type_counts'].get)
        
        if analysis['content_lengths']:
            analysis['avg_content_length'] = np.mean(analysis['content_lengths'])
            analysis['content_length_std'] = np.std(analysis['content_lengths'])
        
        return analysis