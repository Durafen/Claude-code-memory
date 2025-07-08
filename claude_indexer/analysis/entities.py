"""Data models for entities and relations extracted from code."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Dict, Any, Optional, Literal
from pathlib import Path


class EntityType(Enum):
    """Types of entities that can be extracted from code."""
    PROJECT = "project"
    DIRECTORY = "directory"
    FILE = "file" 
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    VARIABLE = "variable"
    IMPORT = "import"
    MODULE = "module"
    CONSTANT = "constant"
    DOCUMENTATION = "documentation"
    TEST = "test"
    CHAT_HISTORY = "chat_history"  # Claude Code conversation summaries


class RelationType(Enum):
    """Types of relationships between entities."""
    CONTAINS = "contains"
    IMPORTS = "imports"
    INHERITS = "inherits"
    CALLS = "calls"
    USES = "uses"
    IMPLEMENTS = "implements"
    EXTENDS = "extends"
    DOCUMENTS = "documents"
    TESTS = "tests"
    REFERENCES = "references"


# Type alias for chunk types in dual storage
ChunkType = Literal["metadata", "implementation"]


@dataclass(frozen=True)
class EntityChunk:
    """Represents a chunk of entity content for vector storage in progressive disclosure architecture."""
    
    id: str  # Format: "{file_id}::{entity_name}::{chunk_type}"
    entity_name: str
    chunk_type: ChunkType
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self) -> None:
        """Validate chunk after creation."""
        if not self.id or not self.entity_name or not self.content:
            raise ValueError("id, entity_name, and content cannot be empty")
        if self.chunk_type not in ["metadata", "implementation"]:
            raise ValueError(f"chunk_type must be 'metadata' or 'implementation', got: {self.chunk_type}")
    
    def to_vector_payload(self) -> Dict[str, Any]:
        """Convert to Qdrant payload format with progressive disclosure support."""
        payload = {
            "entity_name": self.entity_name,
            "chunk_type": self.chunk_type,
            "content": self.content,
            "created_at": datetime.now().isoformat(),
            **self.metadata
        }
        return payload
    
    @classmethod
    def create_metadata_chunk(cls, entity: 'Entity', has_implementation: bool = False) -> 'EntityChunk':
        """Create metadata chunk from existing Entity for progressive disclosure."""
        # Build content from entity observations
        content_parts = []
        if entity.docstring:
            content_parts.append(f"Description: {entity.docstring}")
        
        # Add key observations
        content_parts.extend(entity.observations)
        content = " | ".join(content_parts)
        
        return cls(
            id=f"{str(entity.file_path)}::{entity.name}::metadata",
            entity_name=entity.name,
            chunk_type="metadata",
            content=content,
            metadata={
                "entity_type": entity.entity_type.value,
                "file_path": str(entity.file_path) if entity.file_path else "",
                "line_number": entity.line_number,
                "end_line_number": entity.end_line_number,
                "has_implementation": has_implementation
            }
        )


@dataclass(frozen=True)
class RelationChunk:
    """Represents a relation as a chunk for v2.4 pure architecture."""
    
    id: str  # Format: "{from_entity}::{relation_type}::{to_entity}"
    from_entity: str
    to_entity: str
    relation_type: RelationType
    content: str  # Human-readable description
    context: Optional[str] = None
    confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self) -> None:
        """Validate relation chunk after creation."""
        if not self.id or not self.from_entity or not self.to_entity:
            raise ValueError("id, from_entity, and to_entity cannot be empty")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError("Confidence must be between 0.0 and 1.0")
    
    @classmethod
    def from_relation(cls, relation: 'Relation') -> 'RelationChunk':
        """Create a RelationChunk from a Relation."""
        chunk_id = f"{relation.from_entity}::{relation.relation_type.value}::{relation.to_entity}"
        
        # Build human-readable content
        content = f"{relation.from_entity} {relation.relation_type.value} {relation.to_entity}"
        if relation.context:
            content += f" ({relation.context})"
            
        return cls(
            id=chunk_id,
            from_entity=relation.from_entity,
            to_entity=relation.to_entity,
            relation_type=relation.relation_type,
            content=content,
            context=relation.context,
            confidence=relation.confidence,
            metadata=relation.metadata.copy() if relation.metadata else {}
        )
    
    def to_vector_payload(self) -> Dict[str, Any]:
        """Convert relation chunk to vector storage payload."""
        payload: Dict[str, Any] = {
            "chunk_type": "relation",
            "entity_name": self.from_entity,  # Primary entity for search
            "relation_target": self.to_entity,
            "relation_type": self.relation_type.value,
            "content": self.content,
            "created_at": datetime.now().isoformat(),
            "type": "chunk"
        }
        
        if self.context:
            payload["context"] = self.context
        if self.confidence != 1.0:
            payload["confidence"] = self.confidence
        
        # Include metadata fields (especially import_type for import relations)
        if self.metadata:
            for key, value in self.metadata.items():
                if key not in payload:  # Don't override existing fields
                    payload[key] = value
            
        return payload


@dataclass(frozen=True)
class ChatChunk:
    """Represents chat data as a chunk for v2.4 pure architecture."""
    
    id: str  # Format: "chat::{chat_id}::{chunk_type}"
    chat_id: str
    chunk_type: str  # "chat_summary" or "chat_detail"
    content: str
    timestamp: Optional[str] = None
    
    def __post_init__(self) -> None:
        """Validate chat chunk after creation."""
        if not self.id or not self.chat_id or not self.content:
            raise ValueError("id, chat_id, and content cannot be empty")
        if self.chunk_type not in ["chat_summary", "chat_detail"]:
            raise ValueError(f"chunk_type must be 'chat_summary' or 'chat_detail', got: {self.chunk_type}")
    
    def to_vector_payload(self) -> Dict[str, Any]:
        """Convert chat chunk to vector storage payload."""
        payload = {
            "chunk_type": self.chunk_type,
            "entity_name": f"chat_{self.chat_id}",
            "entity_type": "chat",
            "content": self.content,
            "created_at": datetime.now().isoformat(),
            "type": "chunk"
        }
        
        # Preserve original timestamp if provided, but also add created_at
        if self.timestamp:
            payload["timestamp"] = self.timestamp
            
        return payload


@dataclass(frozen=True)
class Entity:
    """Immutable entity representing a code component."""
    
    name: str
    entity_type: EntityType
    observations: List[str] = field(default_factory=list)
    
    # Optional metadata
    file_path: Optional[Path] = None
    line_number: Optional[int] = None
    end_line_number: Optional[int] = None
    docstring: Optional[str] = None
    signature: Optional[str] = None
    complexity_score: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self) -> None:
        """Validate entity after creation."""
        if not self.name:
            raise ValueError("Entity name cannot be empty")
        if not self.observations:
            object.__setattr__(self, 'observations', [f"{self.entity_type.value.title()}: {self.name}"])
    
    @property
    def qualified_name(self) -> str:
        """Get fully qualified name including file path."""
        if self.file_path:
            return f"{self.file_path}:{self.name}"
        return self.name
    
    
    def add_observation(self, observation: str) -> 'Entity':
        """Create new entity with additional observation (immutable)."""
        new_observations = list(self.observations) + [observation]
        return Entity(
            name=self.name,
            entity_type=self.entity_type,
            observations=new_observations,
            file_path=self.file_path,
            line_number=self.line_number,
            end_line_number=self.end_line_number,
            docstring=self.docstring,
            signature=self.signature,
            complexity_score=self.complexity_score,
            metadata=self.metadata.copy()
        )


@dataclass(frozen=True)
class Relation:
    """Immutable relationship between two entities."""
    
    from_entity: str
    to_entity: str
    relation_type: RelationType
    
    # Optional metadata
    context: Optional[str] = None
    confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self) -> None:
        """Validate relation after creation."""
        if not self.from_entity or not self.to_entity:
            raise ValueError("Both from_entity and to_entity must be non-empty")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError("Confidence must be between 0.0 and 1.0")
    
    
    @property
    def is_bidirectional(self) -> bool:
        """Check if this relation type is naturally bidirectional."""
        bidirectional_types = {
            RelationType.REFERENCES,
            RelationType.USES,
        }
        return self.relation_type in bidirectional_types
    
    def reverse(self) -> 'Relation':
        """Create the reverse relation (if applicable)."""
        if not self.is_bidirectional:
            raise ValueError(f"Relation type {self.relation_type} is not bidirectional")
        
        return Relation(
            from_entity=self.to_entity,
            to_entity=self.from_entity,
            relation_type=self.relation_type,
            context=self.context,
            confidence=self.confidence,
            metadata=self.metadata.copy()
        )


class EntityFactory:
    """Factory for creating entities with consistent patterns."""
    
    @staticmethod
    def create_file_entity(file_path: Path, **metadata: Any) -> Entity:
        """Create a file entity with standard observations."""
        observations = [
            f"File: {file_path.name}",
            f"Path: {file_path}",
            f"Extension: {file_path.suffix}",
        ]
        
        if file_path.stat().st_size:
            observations.append(f"Size: {file_path.stat().st_size} bytes")
        
        # Log file entity creation for debugging
        from ..indexer_logging import get_logger
        logger = get_logger()
        logger.debug(f"📁 Creating FILE entity: name='{str(file_path)}' (absolute path)")
        
        return Entity(
            name=str(file_path),
            entity_type=EntityType.FILE,
            observations=observations,
            file_path=file_path,
            metadata=metadata
        )
    
    @staticmethod  
    def create_function_entity(name: str, file_path: Path, line_number: int,
                             signature: Optional[str] = None, docstring: Optional[str] = None,
                             **metadata: Any) -> Entity:
        """Create a function entity with standard observations."""
        observations = [
            f"Function: {name}",
            f"Defined in: {file_path}",
            f"Line: {line_number}",
        ]
        
        if signature:
            observations.append(f"Signature: {signature}")
        if docstring:
            observations.append(f"Description: {docstring}")
        
        return Entity(
            name=name,
            entity_type=EntityType.FUNCTION,
            observations=observations,
            file_path=file_path,
            line_number=line_number,
            signature=signature,
            docstring=docstring,
            metadata=metadata if isinstance(metadata, dict) else dict(metadata)
        )
    
    @staticmethod
    def create_class_entity(name: str, file_path: Path, line_number: int,
                          docstring: Optional[str] = None, base_classes: Optional[List[str]] = None,
                          **metadata: Any) -> Entity:
        """Create a class entity with standard observations."""
        observations = [
            f"Class: {name}",
            f"Defined in: {file_path}",
            f"Line: {line_number}",
        ]
        
        if base_classes:
            observations.append(f"Inherits from: {', '.join(base_classes)}")
        if docstring:
            observations.append(f"Description: {docstring}")
        
        return Entity(
            name=name,
            entity_type=EntityType.CLASS,
            observations=observations,
            file_path=file_path,
            line_number=line_number,
            docstring=docstring,
            metadata={**metadata, "base_classes": base_classes or []}
        )


class RelationFactory:
    """Factory for creating relations with consistent patterns."""
    
    @staticmethod
    def create_contains_relation(parent: str, child: str, context: Optional[str] = None) -> Relation:
        """Create a 'contains' relationship."""
        return Relation(
            from_entity=parent,
            to_entity=child,
            relation_type=RelationType.CONTAINS,
            context=context or f"{parent} contains {child}"
        )
    
    @staticmethod
    def create_imports_relation(importer: str, imported: str, 
                              import_type: str = "module") -> Relation:
        """Create an 'imports' relationship."""
        return Relation(
            from_entity=importer,
            to_entity=imported,
            relation_type=RelationType.IMPORTS,
            context=f"Imports {import_type}",
            metadata={"import_type": import_type}
        )
    
    @staticmethod
    def create_calls_relation(caller: str, callee: str, context: Optional[str] = None) -> Relation:
        """Create a 'calls' relationship."""
        return Relation(
            from_entity=caller,
            to_entity=callee,
            relation_type=RelationType.CALLS,
            context=context or f"{caller} calls {callee}"
        )
    
    @staticmethod
    def create_inherits_relation(subclass: str, superclass: str, context: Optional[str] = None) -> Relation:
        """Create an 'inherits' relationship."""
        return Relation(
            from_entity=subclass,
            to_entity=superclass,
            relation_type=RelationType.INHERITS,
            context=context or f"{subclass} inherits from {superclass}"
        )