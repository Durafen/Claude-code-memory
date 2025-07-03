from pathlib import Path
from typing import List, Dict, Any, Optional
import time
import hashlib
from .parser import CodeParser, ParserResult
from .entities import Entity, Relation, EntityChunk, EntityType, RelationType, EntityFactory, RelationFactory


class TextParser(CodeParser):
    """Parse plain text files with configurable chunking."""
    
    SUPPORTED_EXTENSIONS = ['.txt', '.log']
    
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.chunk_size = self.config.get('chunk_size', 50)
        self.max_line_length = self.config.get('max_line_length', 1000)
        
    def can_parse(self, file_path: Path) -> bool:
        """Check if this parser can handle the file."""
        return file_path.suffix in self.SUPPORTED_EXTENSIONS
    
    def get_supported_extensions(self) -> List[str]:
        """Return list of supported file extensions."""
        return self.SUPPORTED_EXTENSIONS
        
    def parse(self, file_path: Path) -> ParserResult:
        """Split text into searchable chunks."""
        start_time = time.time()
        result = ParserResult(file_path=file_path, entities=[], relations=[])
        
        try:
            # Read file content
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            result.file_hash = self._get_file_hash(file_path)
            
            entities = []
            chunks = []
            
            # Create file entity
            file_entity = EntityFactory.create_file_entity(
                file_path,
                content_type="text",
                parsing_method="text-chunking"
            )
            entities.append(file_entity)
            
            # Split content into chunks
            text_chunks = self._create_chunks(content, self.chunk_size)
            
            # Create entity for each significant chunk
            for i, chunk_content in enumerate(text_chunks):
                if len(chunk_content.strip()) > 10:  # Only meaningful chunks
                    chunk_name = f"{file_path.stem}_chunk_{i+1}"
                    
                    # Create entity for this chunk
                    entity = Entity(
                        name=chunk_name,
                        entity_type=EntityType.DOCUMENTATION,
                        observations=[
                            f"Text chunk {i+1} from {file_path.name}",
                            f"Lines {i*self.chunk_size + 1}-{(i+1)*self.chunk_size}",
                            f"Content preview: {chunk_content[:100]}..."
                        ],
                        file_path=file_path,
                        line_number=i * self.chunk_size + 1,
                        metadata={
                            "type": "text_chunk",
                            "chunk_index": i,
                            "chunk_size": len(chunk_content)
                        }
                    )
                    entities.append(entity)
                    
                    # Create implementation chunk with full text content
                    impl_chunk = EntityChunk(
                        id=f"{str(file_path)}::{chunk_name}::implementation",
                        entity_name=chunk_name,
                        chunk_type="implementation",
                        content=chunk_content,
                        metadata={
                            "entity_type": "text_chunk",
                            "file_path": str(file_path),
                            "start_line": i * self.chunk_size + 1,
                            "end_line": (i + 1) * self.chunk_size,
                            "chunk_index": i
                        }
                    )
                    chunks.append(impl_chunk)
                    
                    # Create metadata chunk with preview
                    preview = chunk_content[:200] + "..." if len(chunk_content) > 200 else chunk_content
                    metadata_chunk = EntityChunk(
                        id=f"{str(file_path)}::{chunk_name}::metadata",
                        entity_name=chunk_name,
                        chunk_type="metadata",
                        content=preview,
                        metadata={
                            "entity_type": "text_chunk",
                            "file_path": str(file_path),
                            "chunk_index": i,
                            "start_line": i * self.chunk_size + 1,
                            "end_line": (i + 1) * self.chunk_size,
                            "has_implementation": True  # Truth-based: we created implementation chunk
                        }
                    )
                    chunks.append(metadata_chunk)
            
            # Create containment relations
            file_name = str(file_path)
            for entity in entities[1:]:  # Skip file entity
                relation = RelationFactory.create_contains_relation(file_name, entity.name)
                result.relations.append(relation)
            
            result.entities = entities
            result.implementation_chunks = chunks
            
        except Exception as e:
            result.errors.append(f"Text parsing failed: {e}")
        
        result.parsing_time = time.time() - start_time
        return result
    
    def _get_file_hash(self, file_path: Path) -> str:
        """Calculate SHA256 hash of file contents."""
        try:
            with open(file_path, 'rb') as f:
                return hashlib.sha256(f.read()).hexdigest()
        except Exception:
            return ""
    
    def _create_chunks(self, content: str, chunk_size: int) -> List[str]:
        """Split text into chunks by lines."""
        lines = content.split('\n')
        chunks = []
        
        for i in range(0, len(lines), chunk_size):
            chunk_lines = lines[i:i + chunk_size]
            
            # Truncate very long lines
            truncated_lines = []
            for line in chunk_lines:
                if len(line) > self.max_line_length:
                    truncated_line = line[:self.max_line_length] + "..."
                    truncated_lines.append(truncated_line)
                else:
                    truncated_lines.append(line)
            
            chunk_content = '\n'.join(truncated_lines)
            chunks.append(chunk_content)
        
        return chunks


class CSVParser(CodeParser):
    """Parse CSV files for basic structure."""
    
    SUPPORTED_EXTENSIONS = ['.csv']
    
    def can_parse(self, file_path: Path) -> bool:
        """Check if this parser can handle the file."""
        return file_path.suffix in self.SUPPORTED_EXTENSIONS
    
    def get_supported_extensions(self) -> List[str]:
        """Return list of supported file extensions."""
        return self.SUPPORTED_EXTENSIONS
        
    def parse(self, file_path: Path) -> ParserResult:
        """Parse CSV for column headers and basic structure."""
        start_time = time.time()
        result = ParserResult(file_path=file_path, entities=[], relations=[])
        
        try:
            import csv
            
            result.file_hash = self._get_file_hash(file_path)
            
            entities = []
            
            # Create file entity
            file_entity = EntityFactory.create_file_entity(
                file_path,
                content_type="data",
                parsing_method="csv"
            )
            entities.append(file_entity)
            
            # Read CSV headers
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                reader = csv.reader(f)
                headers = next(reader, [])
                
                # Count rows
                row_count = sum(1 for _ in reader) + 1  # +1 for header
            
            # Create entities for columns
            for i, header in enumerate(headers):
                if header.strip():
                    entity = Entity(
                        name=f"Column: {header}",
                        entity_type=EntityType.DOCUMENTATION,
                        observations=[
                            f"CSV column: {header}",
                            f"Position: {i+1}",
                            f"File: {file_path.name}",
                            f"Total rows: {row_count}"
                        ],
                        file_path=file_path,
                        line_number=1,
                        metadata={
                            "type": "csv_column",
                            "column_name": header,
                            "position": i,
                            "row_count": row_count
                        }
                    )
                    entities.append(entity)
            
            # Create containment relations
            file_name = str(file_path)
            for entity in entities[1:]:  # Skip file entity
                relation = RelationFactory.create_contains_relation(file_name, entity.name)
                result.relations.append(relation)
            
            result.entities = entities
            
        except Exception as e:
            result.errors.append(f"CSV parsing failed: {e}")
        
        result.parsing_time = time.time() - start_time
        return result
    
    def _get_file_hash(self, file_path: Path) -> str:
        """Calculate SHA256 hash of file contents."""
        try:
            with open(file_path, 'rb') as f:
                return hashlib.sha256(f.read()).hexdigest()
        except Exception:
            return ""


class INIParser(CodeParser):
    """Parse INI/Config files."""
    
    SUPPORTED_EXTENSIONS = ['.ini', '.conf', '.cfg']
    
    def can_parse(self, file_path: Path) -> bool:
        """Check if this parser can handle the file."""
        return file_path.suffix in self.SUPPORTED_EXTENSIONS
    
    def get_supported_extensions(self) -> List[str]:
        """Return list of supported file extensions."""
        return self.SUPPORTED_EXTENSIONS
        
    def parse(self, file_path: Path) -> ParserResult:
        """Parse INI file for sections and keys."""
        start_time = time.time()
        result = ParserResult(file_path=file_path, entities=[], relations=[])
        
        try:
            import configparser
            
            result.file_hash = self._get_file_hash(file_path)
            
            entities = []
            
            # Create file entity
            file_entity = EntityFactory.create_file_entity(
                file_path,
                content_type="configuration",
                parsing_method="ini"
            )
            entities.append(file_entity)
            
            # Parse INI file
            config = configparser.ConfigParser()
            config.read(file_path, encoding='utf-8')
            
            # Create entities for sections
            for section_name in config.sections():
                section_entity = Entity(
                    name=f"Section: {section_name}",
                    entity_type=EntityType.DOCUMENTATION,
                    observations=[
                        f"INI section: {section_name}",
                        f"File: {file_path.name}",
                        f"Keys: {len(config[section_name])} keys"
                    ],
                    file_path=file_path,
                    line_number=1,  # TODO: Extract actual line number
                    metadata={
                        "type": "ini_section",
                        "section_name": section_name,
                        "key_count": len(config[section_name])
                    }
                )
                entities.append(section_entity)
                
                # Create entities for keys in this section
                for key in config[section_name]:
                    value = config[section_name][key]
                    key_entity = Entity(
                        name=f"Key: {section_name}.{key}",
                        entity_type=EntityType.DOCUMENTATION,
                        observations=[
                            f"INI key: {key}",
                            f"Section: {section_name}",
                            f"Value: {value[:100]}..." if len(value) > 100 else f"Value: {value}",
                            f"File: {file_path.name}"
                        ],
                        file_path=file_path,
                        line_number=1,
                        metadata={
                            "type": "ini_key",
                            "section": section_name,
                            "key": key,
                            "value": value
                        }
                    )
                    entities.append(key_entity)
                    
                    # Create relation from section to key
                    relation = RelationFactory.create_contains_relation(
                        f"Section: {section_name}", 
                        f"Key: {section_name}.{key}"
                    )
                    result.relations.append(relation)
            
            # Create containment relations for sections
            file_name = str(file_path)
            for entity in entities[1:]:  # Skip file entity
                if entity.metadata.get("type") == "ini_section":
                    relation = RelationFactory.create_contains_relation(file_name, entity.name)
                    result.relations.append(relation)
            
            result.entities = entities
            
        except Exception as e:
            result.errors.append(f"INI parsing failed: {e}")
        
        result.parsing_time = time.time() - start_time
        return result
    
    def _get_file_hash(self, file_path: Path) -> str:
        """Calculate SHA256 hash of file contents."""
        try:
            with open(file_path, 'rb') as f:
                return hashlib.sha256(f.read()).hexdigest()
        except Exception:
            return ""