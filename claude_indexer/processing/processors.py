"""Specialized processor implementations."""

from typing import List, Set, TYPE_CHECKING
from .content_processor import ContentProcessor
from .context import ProcessingContext
from .results import ProcessingResult

if TYPE_CHECKING:
    from ..analysis.entities import Entity, Relation, EntityChunk, RelationChunk


class EntityProcessor(ContentProcessor):
    """Processor for entity metadata chunks."""
    
    def process_batch(self, entities: List['Entity'], context: ProcessingContext) -> ProcessingResult:
        """Process entity metadata with Git+Meta deduplication."""
        if not entities:
            return ProcessingResult.success_result()
        
        if self.logger:
            self.logger.debug(f"🧠 Processing entities with Git+Meta deduplication: {len(entities)} items")
        
        # Create metadata chunks with implementation flags
        chunks_to_process = []
        for entity in entities:
            has_implementation = entity.name in context.implementation_entity_names
            from ..analysis.entities import EntityChunk
            metadata_chunk = EntityChunk.create_metadata_chunk(entity, has_implementation)
            chunks_to_process.append(metadata_chunk)
        
        # Check deduplication
        chunks_to_embed, chunks_to_skip = self.check_deduplication(
            chunks_to_process, context.collection_name
        )
        
        # Update changed entity IDs for relation filtering
        for chunk in chunks_to_embed:
            entity_id = f"{chunk.metadata.get('file_path', '')}::{chunk.entity_name}"
            if chunk.metadata.get('file_path'):
                context.changed_entity_ids.add(entity_id)
        
        # Log efficiency gains
        if chunks_to_skip and self.logger:
            self.logger.info(f"⚡ Git+Meta Efficiency: Skipped {len(chunks_to_skip)} unchanged entities (saved {len(chunks_to_skip)} embeddings)")
        
        if not chunks_to_embed:
            return ProcessingResult.success_result(
                items_processed=len(entities),
                embeddings_skipped=len(chunks_to_skip)
            )
        
        # Generate embeddings
        embedding_results, cost_data = self.process_embeddings(chunks_to_embed, "entity")
        
        # Create points
        points, failed_count = self.create_points(
            chunks_to_embed, embedding_results, context.collection_name
        )
        
        if self.logger:
            self.logger.debug(f"🧠 Created {len(points)} entity points from {len(chunks_to_embed)} embedded chunks")
            if failed_count > 0:
                self.logger.warning(f"⚠️ {failed_count} entity embeddings failed and were skipped")
        
        return ProcessingResult.success_result(
            items_processed=len(entities),
            embeddings_saved=len(points),
            embeddings_skipped=len(chunks_to_skip),
            total_tokens=cost_data['tokens'],
            total_cost=cost_data['cost'],
            total_requests=cost_data['requests'],
            points_created=points
        )
class RelationProcessor(ContentProcessor):
    """Processor for relation chunks with smart filtering."""
    
    def process_batch(self, relations: List['Relation'], context: ProcessingContext) -> ProcessingResult:
        """Process relations with Git+Meta smart filtering."""
        if not relations:
            return ProcessingResult.success_result()
        
        if self.logger:
            self.logger.debug(f"🔗 Processing relations with Git+Meta smart filtering: {len(relations)} items")
        
        # Import SmartRelationsProcessor for filtering
        from ..storage.diff_layers import SmartRelationsProcessor
        relations_processor = SmartRelationsProcessor()
        
        # Apply smart filtering if we have changed entities
        if context.changed_entity_ids:
            relations_to_embed, relations_unchanged = relations_processor.filter_relations_for_changes(
                relations, context.changed_entity_ids
            )
            
            if self.logger:
                self.logger.debug(f"🔗 Smart Relations filtering: {len(relations_to_embed)} to embed, {len(relations_unchanged)} unchanged")
            
            if relations_unchanged:
                self.logger.info(f"⚡ Smart Relations: Skipped {len(relations_unchanged)} unchanged relations (saved {len(relations_unchanged)} embeddings)")
            
            relations_to_process = relations_to_embed
        else:
            # Fallback: process all relations (initial indexing)
            relations_to_process = relations
            if self.logger:
                self.logger.debug(f"🔗 Initial indexing: Processing all {len(relations_to_process)} relations")
        
        if not relations_to_process:
            return ProcessingResult.success_result(
                items_processed=len(relations),
                embeddings_skipped=len(relations) - len(relations_to_process)
            )
        
        # Deduplicate relations BEFORE embedding to save API costs
        unique_relations = self._deduplicate_relations(relations_to_process)
        
        # Generate relation texts for embedding  
        relation_texts = [self._relation_to_text(relation) for relation in unique_relations]
        
        if self.logger:
            self.logger.debug(f"🔤 Generating embeddings for {len(relation_texts)} unique relation texts")
        
        # Generate embeddings
        embedding_results, cost_data = self.process_embeddings(unique_relations, "relation")
        
        # Create relation chunk points
        points, failed_count = self.create_points(
            unique_relations, embedding_results, context.collection_name, 
            'create_relation_chunk_point'
        )
        
        if self.logger:
            self.logger.debug(f"🔗 Created {len(points)} relation points from {len(unique_relations)} unique relations")
            if failed_count > 0:
                self.logger.warning(f"⚠️ {failed_count} relation embeddings failed and were skipped")
        
        return ProcessingResult.success_result(
            items_processed=len(relations),
            embeddings_saved=len(points),
            embeddings_skipped=len(relations) - len(relations_to_process),
            total_tokens=cost_data['tokens'],
            total_cost=cost_data['cost'],
            total_requests=cost_data['requests'],
            points_created=points
        )    
    def _deduplicate_relations(self, relations: List['Relation']) -> List['Relation']:
        """Deduplicate relations before embedding to save costs."""
        seen_relation_keys = set()
        unique_relations = []
        duplicate_count = 0
        duplicate_details = {}
        
        if self.logger:
            self.logger.debug(f"🔍 === RELATION DEDUPLICATION ===")
            self.logger.debug(f"   Total relations to process: {len(relations)}")
        
        for relation in relations:
            # Generate the same key that will be used for storage
            from ..analysis.entities import RelationChunk
            relation_chunk = RelationChunk.from_relation(relation)
            relation_key = relation_chunk.id
            import_type = relation.metadata.get('import_type', 'none') if relation.metadata else 'none'
            
            if relation_key not in seen_relation_keys:
                seen_relation_keys.add(relation_key)
                unique_relations.append(relation)
                if self.logger and len(unique_relations) <= 5:
                    self.logger.debug(f"   ✅ Unique: {relation_key} [import_type: {import_type}]")
            else:
                duplicate_count += 1
                if import_type not in duplicate_details:
                    duplicate_details[import_type] = 0
                duplicate_details[import_type] += 1
                if self.logger and duplicate_count <= 5:
                    self.logger.debug(f"   ❌ Duplicate: {relation_key} [import_type: {import_type}]")
        
        if self.logger:
            self.logger.debug(f"   Unique relations: {len(unique_relations)}")
            self.logger.debug(f"   Duplicates removed: {duplicate_count}")
            if duplicate_details:
                self.logger.debug(f"   Duplicates by type: {duplicate_details}")
        
        return unique_relations
    
    def _relation_to_text(self, relation: 'Relation') -> str:
        """Convert relation to text for embedding."""
        text = f"Relation: {relation.from_entity} {relation.relation_type.value} {relation.to_entity}"
        
        if relation.context:
            text += f" | Context: {relation.context}"
        
        return text
    
    def create_points(self, items: List, embedding_results: List, collection_name: str, 
                     point_creation_method: str = 'create_relation_chunk_point') -> tuple:
        """Override to handle relation chunk creation."""
        points = []
        failed_count = 0
        
        for relation, embedding_result in zip(items, embedding_results):
            if embedding_result.success:
                # Convert relation to chunk for v2.4 pure architecture
                from ..analysis.entities import RelationChunk
                relation_chunk = RelationChunk.from_relation(relation)
                point = self.vector_store.create_relation_chunk_point(
                    relation_chunk, embedding_result.embedding, collection_name
                )
                points.append(point)
            else:
                failed_count += 1
                if self.logger:
                    error_msg = getattr(embedding_result, 'error', 'Unknown error')
                    self.logger.warning(f"❌ Relation embedding failed: {relation.from_entity} -> {relation.to_entity} - {error_msg}")
        
        return points, failed_count


class ImplementationProcessor(ContentProcessor):
    """Processor for implementation chunks."""
    
    def process_batch(self, implementation_chunks: List['EntityChunk'], context: ProcessingContext) -> ProcessingResult:
        """Process implementation chunks with Git+Meta deduplication."""
        if not implementation_chunks:
            return ProcessingResult.success_result()
        
        if self.logger:
            self.logger.debug(f"💻 Processing implementation chunks with Git+Meta deduplication: {len(implementation_chunks)} items")
        
        # Check which implementation chunks need embedding
        chunks_to_embed, chunks_to_skip = self.check_deduplication(
            implementation_chunks, context.collection_name
        )
        
        # Log efficiency gains
        if chunks_to_skip and self.logger:
            self.logger.info(f"⚡ Git+Meta Implementation: Skipped {len(chunks_to_skip)} unchanged implementations (saved {len(chunks_to_skip)} embeddings)")
        
        if not chunks_to_embed:
            return ProcessingResult.success_result(
                items_processed=len(implementation_chunks),
                embeddings_skipped=len(chunks_to_skip)
            )
        
        # Generate embeddings
        embedding_results, cost_data = self.process_embeddings(chunks_to_embed, "implementation")
        
        # Create points
        points, failed_count = self.create_points(
            chunks_to_embed, embedding_results, context.collection_name
        )
        
        if self.logger:
            self.logger.debug(f"💻 Created {len(points)} implementation points from {len(chunks_to_embed)} embedded chunks")
            if failed_count > 0:
                self.logger.warning(f"⚠️ {failed_count} implementation embeddings failed and were skipped")
        
        return ProcessingResult.success_result(
            items_processed=len(implementation_chunks),
            embeddings_saved=len(points),
            embeddings_skipped=len(chunks_to_skip),
            total_tokens=cost_data['tokens'],
            total_cost=cost_data['cost'],
            total_requests=cost_data['requests'],
            points_created=points
        )