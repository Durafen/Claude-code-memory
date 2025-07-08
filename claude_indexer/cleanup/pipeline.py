"""
Main memory cleanup pipeline orchestrator.

Coordinates all cleanup components to provide intelligent memory maintenance
for manual entries while preserving auto-indexed code.
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timezone

from .detection import is_manual_entry, classify_entry_type
from .clustering import SimilarityClusterer, SimilarityCluster
from .scorer import QualityScorer, QualityScore
from .resolver import ConflictResolver, ResolutionPlan
from .executor import CleanupExecutor, SafetyConfig, ExecutionResult
from .safety import SafetyManager
from .llm_merger import LLMMerger

logger = logging.getLogger(__name__)


@dataclass
class CleanupConfig:
    """Configuration for cleanup pipeline."""
    similarity_threshold: float = 0.85
    quality_thresholds: Dict[str, float] = None
    safety_limits: Dict[str, Any] = None
    openai_api_key: Optional[str] = None
    model: str = "gpt-4o-mini"
    max_concurrent_scoring: int = 5
    
    def __post_init__(self):
        if self.quality_thresholds is None:
            self.quality_thresholds = {
                "delete": 0.3,
                "merge": 0.8
            }
        
        if self.safety_limits is None:
            self.safety_limits = {
                "max_deletion_percentage": 15,
                "rollback_threshold": 0.2
            }


@dataclass
class CleanupReport:
    """Comprehensive report of cleanup operation."""
    collection_name: str
    start_time: datetime
    end_time: datetime
    total_entries_analyzed: int
    manual_entries_found: int
    auto_indexed_entries_skipped: int
    clusters_created: int
    conflicts_detected: int
    quality_scores_generated: int
    execution_result: Optional[ExecutionResult] = None
    backup_id: Optional[str] = None
    errors: List[str] = None
    warnings: List[str] = None
    
    def __post_init__(self):
        if self.errors is None:
            self.errors = []
        if self.warnings is None:
            self.warnings = []
    
    @property
    def duration_seconds(self) -> float:
        return (self.end_time - self.start_time).total_seconds()
    
    @property
    def success_rate(self) -> float:
        if not self.execution_result:
            return 0.0
        total_actions = self.execution_result.actions_executed + self.execution_result.actions_failed
        if total_actions == 0:
            return 1.0
        return self.execution_result.actions_executed / total_actions


class MemoryCleanupPipeline:
    """Main orchestrator for memory cleanup operations."""
    
    def __init__(self, store, config: CleanupConfig = None):
        """
        Initialize the cleanup pipeline.
        
        Args:
            store: Storage backend (QdrantStore)
            config: Cleanup configuration
        """
        self.store = store
        self.config = config or CleanupConfig()
        
        # Initialize components
        self.clusterer = SimilarityClusterer(self.config.similarity_threshold)
        self.scorer = QualityScorer(self.config.openai_api_key, "gpt-4.1-mini")
        self.resolver = ConflictResolver(llm_merger=LLMMerger(api_key=self.config.openai_api_key))
        self.safety_manager = SafetyManager(store)
        self.executor = CleanupExecutor(store, self.safety_manager)
        
        self.logger = logging.getLogger(__name__)
    
    async def run_cleanup(
        self, 
        collection_name: str, 
        dry_run: bool = True,
        mode: str = "analysis"
    ) -> CleanupReport:
        """
        Execute cleanup pipeline for manual entries only.
        
        Args:
            collection_name: Target collection to clean
            dry_run: If True, only analyze without making changes
            mode: "analysis" for analysis only, "full" for complete cleanup
            
        Returns:
            CleanupReport with results and statistics
        """
        start_time = datetime.now(timezone.utc)
        
        report = CleanupReport(
            collection_name=collection_name,
            start_time=start_time,
            end_time=start_time,  # Will be updated at the end
            total_entries_analyzed=0,
            manual_entries_found=0,
            auto_indexed_entries_skipped=0,
            clusters_created=0,
            conflicts_detected=0,
            quality_scores_generated=0
        )
        
        try:
            self.logger.info(f"Starting cleanup pipeline for collection: {collection_name}")
            self.logger.info(f"Mode: {mode}, Dry run: {dry_run}")
            
            # Step 1: Create backup
            if not dry_run:
                try:
                    backup_id = self.safety_manager.create_backup(collection_name)
                    report.backup_id = backup_id
                    self.logger.info(f"Created backup: {backup_id}")
                except Exception as e:
                    report.errors.append(f"Backup failed: {str(e)}")
                    self.logger.error(f"Backup failed: {e}")
                    return report
            
            # Step 2: Filter manual entries using field-based detection
            manual_entries = await self._filter_manual_entries(collection_name, report)
            
            if not manual_entries:
                self.logger.info("No manual entries found for cleanup")
                report.end_time = datetime.now(timezone.utc)
                return report
            
            # Step 3: Analyze manual patterns for duplicates/conflicts
            clusters = await self._analyze_patterns(manual_entries, report)
            
            if mode == "analysis":
                self.logger.info("Analysis mode completed")
                report.end_time = datetime.now(timezone.utc)
                return report
            
            # Step 4: Score quality of manual insights
            quality_scores = await self._score_quality(manual_entries, report)
            
            # Step 5: Generate resolution plans
            resolution_plans = await self._generate_resolution_plans(clusters, quality_scores, report)
            
            # Step 6: Execute cleanup on manual entries only
            if resolution_plans:
                execution_result = await self._execute_cleanup(
                    resolution_plans, collection_name, dry_run, report
                )
                report.execution_result = execution_result
            
            # Step 7: Validate results
            if not dry_run and report.execution_result:
                validation_success = self.safety_manager.validate_cleanup(
                    collection_name, report.execution_result
                )
                if not validation_success:
                    report.warnings.append("Cleanup validation failed - consider rollback")
            
            self.logger.info("Cleanup pipeline completed successfully")
            
        except Exception as e:
            error_msg = f"Critical error in cleanup pipeline: {str(e)}"
            report.errors.append(error_msg)
            self.logger.error(error_msg, exc_info=True)
        
        finally:
            report.end_time = datetime.now(timezone.utc)
        
        return report
    
    async def _filter_manual_entries(
        self, 
        collection_name: str, 
        report: CleanupReport
    ) -> List[Dict[str, Any]]:
        """Filter entries to find only manual ones using dynamic detection."""
        try:
            self.logger.info("Filtering manual entries using dynamic field-based detection")
            
            # Get all entries from collection
            all_entries = []
            
            # Use scroll to get all entries efficiently WITH VECTORS for clustering
            scroll_result = self.store._scroll_collection(collection_name, with_vectors=True)
            if scroll_result:
                all_entries.extend(scroll_result)
            
            report.total_entries_analyzed = len(all_entries)
            self.logger.info(f"Retrieved {len(all_entries)} total entries")
            
            # Filter using dynamic field-based detection
            manual_entries = []
            auto_indexed_count = 0
            
            for entry in all_entries:
                # Extract payload for analysis (entry is a Record object from Qdrant)
                payload = entry.payload
                
                entry_type = classify_entry_type(payload)
                
                if entry_type == 'clean':
                    # Convert Record to Dict format expected by other components
                    dict_entry = {
                        'id': entry.id,
                        'payload': entry.payload,
                        'vector': getattr(entry, 'vector', None)
                    }
                    manual_entries.append(dict_entry)
                elif entry_type == 'preserve':
                    auto_indexed_count += 1
                else:
                    # Invalid entries - log but don't process
                    self.logger.debug(f"Invalid entry structure: {payload.get('name', 'unknown')}")
            
            report.manual_entries_found = len(manual_entries)
            report.auto_indexed_entries_skipped = auto_indexed_count
            
            self.logger.info(
                f"Dynamic detection results: {len(manual_entries)} manual, "
                f"{auto_indexed_count} auto-indexed, "
                f"{len(all_entries) - len(manual_entries) - auto_indexed_count} invalid"
            )
            
            return manual_entries
            
        except Exception as e:
            error_msg = f"Error filtering manual entries: {str(e)}"
            report.errors.append(error_msg)
            self.logger.error(error_msg)
            return []
    
    async def _analyze_patterns(
        self, 
        manual_entries: List[Dict[str, Any]], 
        report: CleanupReport
    ) -> List[SimilarityCluster]:
        """Analyze manual patterns for similarities and conflicts."""
        try:
            self.logger.info(f"Analyzing {len(manual_entries)} manual entries for patterns")
            
            # Create similarity clusters
            clusters = self.clusterer.cluster_manual_patterns(
                manual_entries, 
                self.config.similarity_threshold
            )
            
            report.clusters_created = len(clusters)
            
            # Count clusters with potential conflicts (size > 1)
            report.conflicts_detected = len([c for c in clusters if c.size > 1])
            
            self.logger.info(
                f"Created {len(clusters)} clusters, "
                f"{report.conflicts_detected} with potential conflicts"
            )
            
            # Log cluster statistics
            for i, cluster in enumerate(clusters):
                if cluster.size > 1:
                    analysis = self.clusterer.analyze_cluster_diversity(cluster)
                    self.logger.debug(
                        f"Cluster {i}: {cluster.size} entries, "
                        f"types: {list(cluster.entity_types)}, "
                        f"similarity: {cluster.avg_similarity:.3f}"
                    )
            
            return clusters
            
        except Exception as e:
            error_msg = f"Error analyzing patterns: {str(e)}"
            report.errors.append(error_msg)
            self.logger.error(error_msg)
            return []
    
    async def _score_quality(
        self, 
        manual_entries: List[Dict[str, Any]], 
        report: CleanupReport
    ) -> List[Optional[QualityScore]]:
        """Score quality of manual entries using LLM."""
        try:
            self.logger.info(f"Scoring quality of {len(manual_entries)} manual entries")
            
            # Score entries in batches to avoid rate limits
            quality_scores = await self.scorer.score_batch(
                manual_entries, 
                max_concurrent=self.config.max_concurrent_scoring
            )
            
            # Count successful scores
            successful_scores = [s for s in quality_scores if s is not None]
            report.quality_scores_generated = len(successful_scores)
            
            self.logger.info(f"Generated {len(successful_scores)} quality scores")
            
            # Log quality statistics
            if successful_scores:
                stats = self.scorer.get_quality_statistics(quality_scores)
                self.logger.info(f"Quality statistics: {stats}")
            
            return quality_scores
            
        except Exception as e:
            error_msg = f"Error scoring quality: {str(e)}"
            report.errors.append(error_msg)
            self.logger.error(error_msg)
            return [None] * len(manual_entries)
    
    async def _generate_resolution_plans(
        self, 
        clusters: List[SimilarityCluster],
        quality_scores: List[Optional[QualityScore]],
        report: CleanupReport
    ) -> List[ResolutionPlan]:
        """Generate resolution plans for conflict clusters."""
        try:
            self.logger.info(f"Generating resolution plans for {len(clusters)} clusters")
            
            resolution_plans = []
            
            for cluster in clusters:
                if cluster.size < 2:
                    continue  # Skip single-entry clusters
                
                # Extract quality scores for this cluster
                cluster_scores = []
                for entry in cluster.entries:
                    # Find matching quality score
                    # This is a simplified approach - in practice, you'd need better ID matching
                    cluster_scores.append(None)  # Placeholder
                
                # Generate resolution plan
                plan = await self.resolver.resolve_conflicts(cluster, cluster_scores)
                resolution_plans.append(plan)
                
                self.logger.debug(
                    f"Resolution plan for cluster of {cluster.size}: {plan.action}, "
                    f"confidence: {plan.confidence:.2f}"
                )
            
            self.logger.info(f"Generated {len(resolution_plans)} resolution plans")
            return resolution_plans
            
        except Exception as e:
            error_msg = f"Error generating resolution plans: {str(e)}"
            report.errors.append(error_msg)
            self.logger.error(error_msg)
            return []
    
    async def _execute_cleanup(
        self, 
        resolution_plans: List[ResolutionPlan],
        collection_name: str,
        dry_run: bool,
        report: CleanupReport
    ) -> ExecutionResult:
        """Execute cleanup actions."""
        try:
            self.logger.info(f"Executing {len(resolution_plans)} cleanup actions")
            
            # Create safety config
            safety_config = SafetyConfig(
                max_deletion_percentage=self.config.safety_limits["max_deletion_percentage"],
                dry_run=dry_run,
                backup_before_cleanup=False  # Already created backup
            )
            
            # Execute actions
            execution_result = self.executor.execute_actions(
                resolution_plans, 
                safety_config, 
                collection_name
            )
            
            self.logger.info(
                f"Execution completed: {execution_result.actions_executed} successful, "
                f"{execution_result.actions_failed} failed"
            )
            
            return execution_result
            
        except Exception as e:
            error_msg = f"Error executing cleanup: {str(e)}"
            report.errors.append(error_msg)
            self.logger.error(error_msg)
            
            # Return empty result on error
            return ExecutionResult(errors=[error_msg])
    
    def get_cleanup_statistics(self, collection_name: str) -> Dict[str, Any]:
        """Get statistics about cleanup potential for a collection."""
        try:
            # Get sample of entries
            all_entries = []
            scroll_result = self.store.scroll_collection(collection_name, limit=1000)
            if scroll_result:
                all_entries.extend(scroll_result)
            
            # Classify entries
            manual_count = 0
            auto_indexed_count = 0
            
            for entry in all_entries:
                payload = entry.get('payload', entry)
                entry_type = classify_entry_type(payload)
                
                if entry_type == 'manual':
                    manual_count += 1
                elif entry_type == 'auto-indexed':
                    auto_indexed_count += 1
            
            # Quick duplicate analysis
            duplicates = self.clusterer.find_potential_duplicates(
                [e for e in all_entries if classify_entry_type(e.get('payload', e)) == 'manual']
            )
            
            return {
                'total_entries': len(all_entries),
                'manual_entries': manual_count,
                'auto_indexed_entries': auto_indexed_count,
                'potential_duplicates': len(duplicates),
                'cleanup_candidates': manual_count,
                'estimated_savings_percentage': (len(duplicates) / manual_count * 100) if manual_count > 0 else 0
            }
            
        except Exception as e:
            self.logger.error(f"Error getting cleanup statistics: {e}")
            return {}
    
    async def quick_analysis(self, collection_name: str) -> Dict[str, Any]:
        """Perform quick analysis without full cleanup."""
        try:
            report = await self.run_cleanup(collection_name, dry_run=True, mode="analysis")
            
            stats = {
                'analysis_duration': report.duration_seconds,
                'total_entries': report.total_entries_analyzed,
                'manual_entries': report.manual_entries_found,
                'auto_indexed_entries': report.auto_indexed_entries_skipped,
                'clusters_found': report.clusters_created,
                'conflicts_detected': report.conflicts_detected,
                'cleanup_potential': 'high' if report.conflicts_detected > 0 else 'low',
                'errors': report.errors,
                'warnings': report.warnings
            }
            
            return stats
            
        except Exception as e:
            self.logger.error(f"Error in quick analysis: {e}")
            return {'error': str(e)}