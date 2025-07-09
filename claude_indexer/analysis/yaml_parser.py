from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import time
from tree_sitter import Node
from .base_parsers import TreeSitterParser
from .parser import ParserResult
from .entities import Entity, Relation, EntityChunk, EntityType, RelationType, EntityFactory, RelationFactory


class YAMLParser(TreeSitterParser):
    """Parse YAML configuration files."""
    
    SUPPORTED_EXTENSIONS = ['.yaml', '.yml']
    
    def __init__(self, config: Dict[str, Any] = None):
        import tree_sitter_yaml as tsyaml
        super().__init__(tsyaml, config)
        self.detect_type = config.get('detect_type', True) if config else True
        
    def parse(self, file_path: Path) -> ParserResult:
        """Extract YAML structure and configuration."""
        start_time = time.time()
        result = ParserResult(file_path=file_path, entities=[], relations=[])
        
        try:
            # Read and parse YAML
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            result.file_hash = self._get_file_hash(file_path)
            tree = self.parse_tree(content)
            
            # Check for syntax errors
            if self._has_syntax_errors(tree):
                result.errors.append(f"YAML syntax errors in {file_path.name}")
            
            entities = []
            relations = []
            chunks = []
            
            # Create file entity with type detection
            file_type = self._detect_yaml_type(file_path, content)
            file_entity = self._create_file_entity(file_path, content_type=file_type)
            entities.append(file_entity)
            
            # Special handling based on detected type
            if file_type == "github_workflow":
                special_entities, special_relations = self._handle_github_workflow(tree.root_node, content, file_path)
                entities.extend(special_entities)
                relations.extend(special_relations)
            elif file_type == "docker_compose":
                special_entities, special_relations = self._handle_docker_compose(tree.root_node, content, file_path)
                entities.extend(special_entities)
                relations.extend(special_relations)
            elif file_type == "kubernetes":
                special_entities, special_relations = self._handle_kubernetes(tree.root_node, content, file_path)
                entities.extend(special_entities)
                relations.extend(special_relations)
            else:
                # Generic YAML structure extraction
                generic_entities = self._extract_generic_structure(tree.root_node, content, file_path)
                entities.extend(generic_entities)
            
            # Create containment relations
            file_name = str(file_path)
            for entity in entities[1:]:  # Skip file entity
                relation = RelationFactory.create_contains_relation(file_name, entity.name)
                relations.append(relation)
            
            # Create chunks for searchability
            chunks = self._create_yaml_chunks(file_path, tree.root_node, content)
            
            result.entities = entities
            result.relations = relations
            result.implementation_chunks = chunks
            
        except Exception as e:
            result.errors.append(f"YAML parsing failed: {e}")
        
        result.parsing_time = time.time() - start_time
        return result
    
    def _detect_yaml_type(self, file_path: Path, content: str) -> str:
        """Detect the type of YAML file."""
        if not self.detect_type:
            return "configuration"
        
        # Check file path patterns
        if '.github/workflows' in str(file_path):
            return "github_workflow"
        
        if file_path.name in ['docker-compose.yml', 'docker-compose.yaml']:
            return "docker_compose"
        
        # Check content patterns
        if 'apiVersion:' in content and 'kind:' in content:
            return "kubernetes"
        
        if 'on:' in content and ('jobs:' in content or 'steps:' in content):
            return "github_workflow"
        
        if 'version:' in content and 'services:' in content:
            return "docker_compose"
        
        return "configuration"
    
    def _handle_github_workflow(self, root: Node, content: str, file_path: Path) -> Tuple[List[Entity], List[Relation]]:
        """Handle GitHub Actions workflow files."""
        entities = []
        relations = []
        
        # Extract workflow name
        workflow_name = self._extract_yaml_value(root, 'name', content) or file_path.stem
        
        entity = Entity(
            name=f"Workflow: {workflow_name}",
            entity_type=EntityType.DOCUMENTATION,
            observations=[
                f"GitHub Actions workflow: {workflow_name}",
                f"File: {file_path.name}"
            ],
            file_path=file_path,
            line_number=1,
            metadata={"type": "github_workflow", "workflow_name": workflow_name}
        )
        entities.append(entity)
        
        # Extract jobs
        jobs = self._extract_yaml_mapping_keys(root, 'jobs', content)
        for job_name in jobs:
            job_entity = Entity(
                name=f"Job: {job_name}",
                entity_type=EntityType.FUNCTION,  # Jobs as functions
                observations=[
                    f"GitHub Actions job: {job_name}",
                    f"In workflow: {workflow_name}"
                ],
                file_path=file_path,
                line_number=1,  # TODO: Extract actual line number
                metadata={"type": "github_job", "job_name": job_name}
            )
            entities.append(job_entity)
            
            # Create relation from workflow to job
            relation = RelationFactory.create_contains_relation(f"Workflow: {workflow_name}", f"Job: {job_name}")
            relations.append(relation)
        
        return entities, relations
    
    def _handle_docker_compose(self, root: Node, content: str, file_path: Path) -> Tuple[List[Entity], List[Relation]]:
        """Handle Docker Compose files."""
        entities = []
        relations = []
        
        # Extract services
        services = self._extract_yaml_mapping_keys(root, 'services', content)
        for service_name in services:
            service_entity = Entity(
                name=f"Service: {service_name}",
                entity_type=EntityType.CLASS,  # Services as classes
                observations=[
                    f"Docker Compose service: {service_name}",
                    f"File: {file_path.name}"
                ],
                file_path=file_path,
                line_number=1,
                metadata={"type": "docker_service", "service_name": service_name}
            )
            entities.append(service_entity)
        
        # Extract networks
        networks = self._extract_yaml_mapping_keys(root, 'networks', content)
        for network_name in networks:
            network_entity = Entity(
                name=f"Network: {network_name}",
                entity_type=EntityType.DOCUMENTATION,
                observations=[
                    f"Docker network: {network_name}",
                    f"File: {file_path.name}"
                ],
                file_path=file_path,
                line_number=1,
                metadata={"type": "docker_network", "network_name": network_name}
            )
            entities.append(network_entity)
        
        return entities, relations
    
    def _handle_kubernetes(self, root: Node, content: str, file_path: Path) -> Tuple[List[Entity], List[Relation]]:
        """Handle Kubernetes manifest files."""
        entities = []
        relations = []
        
        # Extract kind and name
        kind = self._extract_yaml_value(root, 'kind', content)
        name = self._extract_yaml_value(root, 'metadata.name', content)
        
        if kind and name:
            entity = Entity(
                name=f"{kind}: {name}",
                entity_type=EntityType.CLASS,  # K8s resources as classes
                observations=[
                    f"Kubernetes {kind}: {name}",
                    f"File: {file_path.name}"
                ],
                file_path=file_path,
                line_number=1,
                metadata={"type": "kubernetes_resource", "kind": kind, "name": name}
            )
            entities.append(entity)
        
        return entities, relations
    
    def _extract_generic_structure(self, root: Node, content: str, file_path: Path) -> List[Entity]:
        """Extract generic YAML structure."""
        entities = []
        
        # Extract top-level keys as entities
        for mapping in self._find_nodes_by_type(root, ['block_mapping']):
            # This is a simplified extraction - in a full implementation,
            # we would recursively parse the YAML structure
            break  # For now, just handle the first mapping
        
        return entities
    
    def _extract_yaml_value(self, root: Node, key_path: str, content: str) -> Optional[str]:
        """Extract a value from YAML by key path (e.g., 'metadata.name')."""
        # This is a simplified implementation
        # In a full implementation, we would properly traverse the YAML tree
        lines = content.split('\n')
        key = key_path.split('.')[-1]  # Get the last part of the path
        
        for line in lines:
            if line.strip().startswith(f'{key}:'):
                value = line.split(':', 1)[1].strip()
                return value.strip('\'"')
        
        return None
    
    def _extract_yaml_mapping_keys(self, root: Node, parent_key: str, content: str) -> List[str]:
        """Extract keys from a YAML mapping under a parent key."""
        # This is a simplified implementation
        keys = []
        lines = content.split('\n')
        in_section = False
        base_indent = None
        
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(f'{parent_key}:'):
                in_section = True
                base_indent = len(line) - len(line.lstrip())
                continue
            
            if in_section:
                current_indent = len(line) - len(line.lstrip())
                
                # If we're back to the same or lower indentation, we've left the section
                if line.strip() and current_indent <= base_indent:
                    break
                
                # If this is a key at the right indentation level
                if ':' in stripped and current_indent > base_indent:
                    key = stripped.split(':')[0].strip()
                    if key:
                        keys.append(key)
        
        return keys
    
    def _create_yaml_chunks(self, file_path: Path, root: Node, content: str) -> List[EntityChunk]:
        """Create searchable chunks from YAML content."""
        chunks = []
        
        # Create implementation chunk with full YAML content
        impl_chunk = EntityChunk(
            id=self._create_chunk_id(file_path, "content", "implementation"),
            entity_name=str(file_path),
            chunk_type="implementation",
            content=content,  # Full YAML content
            metadata={
                "entity_type": "yaml_file",
                "file_path": str(file_path),
                "start_line": 1,
                "end_line": len(content.split('\n'))
            }
        )
        chunks.append(impl_chunk)
        
        # Note: Metadata chunks are auto-generated by progressive disclosure from Entity objects
        
        return chunks