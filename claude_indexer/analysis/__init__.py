"""Code analysis package for parsing and extracting semantic information."""

from .entities import Entity, EntityType, Relation, RelationType
from .parser import CodeParser, ParserResult

__all__ = [
    "Entity",
    "Relation",
    "EntityType",
    "RelationType",
    "CodeParser",
    "ParserResult",
]
