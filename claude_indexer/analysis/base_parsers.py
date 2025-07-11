from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import hashlib
import time
from tree_sitter import Parser, Node
from .parser import CodeParser, ParserResult
from .entities import Entity, Relation, EntityChunk, EntityType, RelationType, EntityFactory


class TreeSitterParser(CodeParser):
    """Base class for all tree-sitter based parsers with common functionality."""
    
    # Child classes must define this
    SUPPORTED_EXTENSIONS: List[str] = []
    
    def __init__(self, language_module, config: Dict[str, Any] = None):
        from tree_sitter import Language
        
        self.config = config or {}
        # Set the language on the parser during initialization
        if hasattr(language_module, 'language'):
            # For tree-sitter packages that expose language as a function
            language_capsule = language_module.language()
            language = Language(language_capsule)
            self.parser = Parser(language)
        else:
            # For direct language objects
            self.parser = Parser(language_module)
    
    def can_parse(self, file_path: Path) -> bool:
        """Check if this parser can handle the file."""
        return file_path.suffix in self.SUPPORTED_EXTENSIONS
    
    def get_supported_extensions(self) -> List[str]:
        """Return list of supported file extensions."""
        return self.SUPPORTED_EXTENSIONS
    
    def update_config(self, config: Dict[str, Any]) -> None:
        """Update parser configuration."""
        self.config.update(config)
        
    def parse_tree(self, content: str):
        """Parse content into tree-sitter AST."""
        return self.parser.parse(bytes(content, "utf8"))
        
    def extract_node_text(self, node: Node, content: str) -> str:
        """Extract text from tree-sitter node."""
        # Convert content to bytes for proper byte-based indexing
        content_bytes = content.encode('utf-8')
        return content_bytes[node.start_byte:node.end_byte].decode('utf-8')
    
    def _get_file_hash(self, file_path: Path) -> str:
        """Calculate SHA256 hash of file contents (follows existing pattern)."""
        try:
            with open(file_path, 'rb') as f:
                return hashlib.sha256(f.read()).hexdigest()
        except Exception:
            return ""
    
    def _find_nodes_by_type(self, root: Node, node_types: List[str]) -> List[Node]:
        """Recursively find all nodes matching given types."""
        nodes = []
        
        def walk(node: Node):
            if node.type in node_types:
                nodes.append(node)
            for child in node.children:
                walk(child)
                
        walk(root)
        return nodes
    
    def _has_syntax_errors(self, tree) -> bool:
        """Check if the parse tree contains syntax errors."""
        def check_node_for_errors(node):
            if node.type == 'ERROR':
                return True
            for child in node.children:
                if check_node_for_errors(child):
                    return True
            return False
        
        return check_node_for_errors(tree.root_node)
    
    def _create_file_entity(self, file_path: Path, entity_count: int = 0, 
                           content_type: str = "code") -> Entity:
        """Create file entity using EntityFactory pattern."""
        return EntityFactory.create_file_entity(
            file_path,
            entity_count=entity_count,
            content_type=content_type,
            parsing_method="tree-sitter"
        )