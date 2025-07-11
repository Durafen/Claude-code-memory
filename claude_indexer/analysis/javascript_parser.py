from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import time
from tree_sitter import Node
from .base_parsers import TreeSitterParser
from .parser import ParserResult
from .entities import Entity, Relation, EntityChunk, EntityType, RelationType, EntityFactory, RelationFactory


class JavaScriptParser(TreeSitterParser):
    """Parse JS/TS files with tree-sitter, optional TSServer for semantics."""
    
    SUPPORTED_EXTENSIONS = ['.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs']
    
    def __init__(self, config: Dict[str, Any] = None):
        import tree_sitter_javascript as tsjs
        super().__init__(tsjs, config)
        self.ts_server = self._init_ts_server() if config and config.get('use_ts_server') else None
        
        # Store language modules for TypeScript support
        try:
            import tree_sitter_typescript as tsts
            self.ts_language = tsts.language_typescript()
            self.tsx_language = tsts.language_tsx()
        except ImportError:
            self.ts_language = None
            self.tsx_language = None
        
    def parse_tree(self, content: str, file_path: Path = None):
        """Parse content with appropriate language based on file extension."""
        if file_path and file_path.suffix in ['.ts'] and self.ts_language:
            # Use TypeScript grammar for .ts files
            from tree_sitter import Parser, Language
            parser = Parser(Language(self.ts_language))
            return parser.parse(bytes(content, "utf8"))
        elif file_path and file_path.suffix in ['.tsx'] and self.tsx_language:
            # Use TSX grammar for .tsx files
            from tree_sitter import Parser, Language
            parser = Parser(Language(self.tsx_language))
            return parser.parse(bytes(content, "utf8"))
        else:
            # Use JavaScript grammar for .js, .jsx, .mjs, .cjs files
            return super().parse_tree(content)
        
    def parse(self, file_path: Path, batch_callback=None, global_entity_names=None) -> ParserResult:
        """Extract functions, classes, imports with progressive disclosure."""
        start_time = time.time()
        result = ParserResult(file_path=file_path, entities=[], relations=[])
        
        try:
            # Read file and calculate hash
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            result.file_hash = self._get_file_hash(file_path)
            tree = self.parse_tree(content, file_path)
            
            # Check for syntax errors
            if self._has_syntax_errors(tree):
                result.errors.append(f"Syntax errors detected in {file_path.name}")
            
            # Extract entities and chunks
            entities = []
            chunks = []
            
            # Extract functions (including arrow functions)
            for node in self._find_nodes_by_type(tree.root_node, 
                ['function_declaration', 'arrow_function', 'function_expression', 'method_definition']):
                entity, entity_chunks = self._create_function_entity(node, file_path, content)
                if entity:
                    entities.append(entity)
                    chunks.extend(entity_chunks)
            
            # Extract classes
            for node in self._find_nodes_by_type(tree.root_node, ['class_declaration', 'class_expression']):
                entity, entity_chunks = self._create_class_entity(node, file_path, content)
                if entity:
                    entities.append(entity)
                    chunks.extend(entity_chunks)
            
            # Extract TypeScript interfaces
            for node in self._find_nodes_by_type(tree.root_node, ['interface_declaration']):
                entity, entity_chunks = self._create_interface_entity(node, file_path, content)
                if entity:
                    entities.append(entity)
                    chunks.extend(entity_chunks)
            
            # Extract variables (var, let, const declarations and assignments)
            variable_entities = self._extract_variables(tree.root_node, file_path, content)
            for entity in variable_entities:
                entities.append(entity)
            
            # Extract imports
            relations = []
            for node in self._find_nodes_by_type(tree.root_node, ['import_statement', 'import_from']):
                relation = self._create_import_relation(node, file_path, content)
                if relation:
                    relations.append(relation)
            
            # Extract dynamic JSON/file loading patterns
            json_relations = self._extract_json_loading_patterns(tree.root_node, file_path, content)
            relations.extend(json_relations)
            
            # Extract inheritance relations (extends/implements)
            inheritance_relations = self._extract_inheritance_relations(tree.root_node, file_path, content)
            relations.extend(inheritance_relations)
            
            # Extract exception handling relations (try/catch/throw)
            exception_relations = self._extract_exception_relations(tree.root_node, file_path, content)
            relations.extend(exception_relations)
            
            # Extract decorator relations (TypeScript)
            decorator_relations = self._extract_decorator_relations(tree.root_node, file_path, content)
            relations.extend(decorator_relations)
            
            # Create file entity
            file_entity = self._create_file_entity(file_path, len(entities), "javascript")
            entities.insert(0, file_entity)
            
            # Create containment relations
            file_name = str(file_path)
            for entity in entities[1:]:  # Skip file entity
                if entity.entity_type in [EntityType.FUNCTION, EntityType.CLASS, EntityType.VARIABLE]:
                    relation = RelationFactory.create_contains_relation(file_name, entity.name)
                    relations.append(relation)
            
            # Create function call relations from semantic metadata
            # Combine current file entities with global entities for comprehensive validation
            all_entity_names = set()
            
            # Add current file entities
            for entity in entities:
                all_entity_names.add(entity.name)
            
            # Add global entities if available
            if global_entity_names:
                all_entity_names.update(global_entity_names)
            
            # Convert to list for compatibility with existing method signature
            entity_names_list = list(all_entity_names)
            
            function_call_relations = self._create_function_call_relations(chunks, file_path, entity_names_list)
            relations.extend(function_call_relations)
            
            result.entities = entities
            result.relations = relations
            result.implementation_chunks = chunks
            
        except Exception as e:
            result.errors.append(f"JavaScript parsing failed: {e}")
        
        result.parsing_time = time.time() - start_time
        return result
        
    def _create_function_entity(self, node: Node, file_path: Path, content: str) -> Tuple[Optional[Entity], List[EntityChunk]]:
        """Create function entity with metadata and implementation chunks."""
        name = self._extract_function_name(node, content)
        if not name:
            return None, []
        
        # Create entity
        entity = EntityFactory.create_function_entity(
            name=name,
            file_path=file_path,
            line_number=node.start_point[0] + 1,
            metadata={
                "end_line": node.end_point[0] + 1,
                "source": "tree-sitter",
                "node_type": node.type
            }
        )
        
        # Create chunks
        chunks = []
        
        # Implementation chunk first
        implementation = self.extract_node_text(node, content)
        impl_chunk = EntityChunk(
            id=self._create_chunk_id(file_path, name, "implementation"),
            entity_name=name,
            chunk_type="implementation",
            content=implementation,
            metadata={
                "entity_type": "function",
                "file_path": str(file_path),
                "start_line": node.start_point[0] + 1,
                "end_line": node.end_point[0] + 1,
                "semantic_metadata": {
                    "calls": self._extract_function_calls(implementation),
                    "complexity": self._calculate_complexity(implementation)
                }
            }
        )
        chunks.append(impl_chunk)
        
        # Note: Metadata chunks are auto-generated by progressive disclosure from Entity objects
        
        return entity, chunks
    
    def _extract_function_name(self, node: Node, content: str) -> Optional[str]:
        """Extract function name from various function node types."""
        # Handle different function types
        if node.type == 'method_definition':
            # For class methods
            name_node = node.child_by_field_name('name')
            if name_node:
                return self.extract_node_text(name_node, content)
        
        # For regular functions
        name_node = node.child_by_field_name('name')
        if name_node:
            return self.extract_node_text(name_node, content)
        
        # For arrow functions assigned to variables
        if node.type == 'arrow_function' and node.parent:
            if node.parent.type == 'variable_declarator':
                id_node = node.parent.child_by_field_name('name')
                if id_node:
                    return self.extract_node_text(id_node, content)
        
        return None
    
    def _extract_function_signature(self, node: Node, content: str) -> str:
        """Extract function signature with parameters and return type."""
        name = self._extract_function_name(node, content) or 'anonymous'
        
        # Get parameters
        params_node = node.child_by_field_name('parameters')
        params = "()"
        if params_node:
            params = self.extract_node_text(params_node, content)
        
        # Get return type for TypeScript
        return_type = ""
        type_node = node.child_by_field_name('return_type')
        if type_node:
            return_type = f": {self.extract_node_text(type_node, content)}"
        
        # Handle different function types
        if node.type == 'arrow_function':
            return f"const {name} = {params} => {{...}}{return_type}"
        else:
            return f"function {name}{params}{return_type}"
    
    def _extract_function_calls(self, implementation: str) -> List[str]:
        """Extract function calls using simple pattern matching."""
        import re
        # Simple regex to find function calls
        call_pattern = r'(\w+)\s*\('
        calls = re.findall(call_pattern, implementation)
        
        # No filtering for built-ins - let entity validation handle it
        return list(set(calls))
    
    def _calculate_complexity(self, implementation: str) -> int:
        """Calculate cyclomatic complexity (simplified)."""
        complexity = 1
        complexity_keywords = ['if', 'else if', 'for', 'while', 'case', 'catch', '?', '&&', '||']
        for keyword in complexity_keywords:
            complexity += implementation.count(keyword)
        return complexity
    
    def _create_class_entity(self, node: Node, file_path: Path, content: str) -> Tuple[Optional[Entity], List[EntityChunk]]:
        """Create class entity with chunks."""
        name_node = node.child_by_field_name('name')
        if not name_node:
            return None, []
        
        name = self.extract_node_text(name_node, content)
        
        # Create entity
        entity = EntityFactory.create_class_entity(
            name=name,
            file_path=file_path,
            line_number=node.start_point[0] + 1,
            metadata={
                "end_line": node.end_point[0] + 1,
                "source": "tree-sitter"
            }
        )
        
        # Create chunks
        chunks = []
        
        # Implementation chunk first
        impl_chunk = EntityChunk(
            id=self._create_chunk_id(file_path, name, "implementation"),
            entity_name=name,
            chunk_type="implementation",
            content=self.extract_node_text(node, content),
            metadata={
                "entity_type": "class",
                "file_path": str(file_path),
                "start_line": node.start_point[0] + 1,
                "end_line": node.end_point[0] + 1
            }
        )
        chunks.append(impl_chunk)
        
        # Note: Metadata chunks are auto-generated by progressive disclosure from Entity objects
        
        return entity, chunks
    
    def _create_interface_entity(self, node: Node, file_path: Path, content: str) -> Tuple[Optional[Entity], List[EntityChunk]]:
        """Create TypeScript interface entity."""
        name_node = node.child_by_field_name('name')
        if not name_node:
            return None, []
        
        name = self.extract_node_text(name_node, content)
        
        # Interfaces are like classes but for TypeScript
        entity = Entity(
            name=name,
            entity_type=EntityType.CLASS,  # Reuse class type for interfaces
            observations=[f"TypeScript interface: {name}"],
            file_path=file_path,
            line_number=node.start_point[0] + 1,
            end_line_number=node.end_point[0] + 1
        )
        
        # Create chunks for interface
        chunks = []
        
        # Implementation chunk with interface definition
        interface_content = self.extract_node_text(node, content)
        impl_chunk = EntityChunk(
            id=self._create_chunk_id(file_path, name, "implementation"),
            entity_name=name,
            chunk_type="implementation",
            content=interface_content,
            metadata={
                "entity_type": "interface",
                "file_path": str(file_path),
                "start_line": node.start_point[0] + 1,
                "end_line": node.end_point[0] + 1
            }
        )
        chunks.append(impl_chunk)
        
        # Note: Metadata chunks are auto-generated by progressive disclosure from Entity objects
        
        return entity, chunks
    
    def _create_import_relation(self, node: Node, file_path: Path, content: str) -> Optional[Relation]:
        """Create import relation from import statement."""
        # Find the source/module being imported
        source_node = None
        
        # Handle different import statement structures
        for child in node.children:
            if child.type == 'string' and child.parent.type in ['import_statement', 'import_from']:
                source_node = child
                break
        
        if not source_node:
            return None
        
        # Extract module name, removing quotes
        module_name = self.extract_node_text(source_node, content).strip('"\'')
        
        # Skip external modules to avoid orphan relations
        # Relative imports (starting with ./ or ../) are always internal
        if not module_name.startswith('./') and not module_name.startswith('../'):
            # Skip common Node.js built-in modules and npm packages
            if (module_name.startswith('@') or  # Scoped npm packages
                '/' not in module_name or  # Top-level npm packages  
                module_name in {'fs', 'path', 'os', 'crypto', 'http', 'https', 'url',
                               'child_process', 'dotenv', 'express', 'react', 'vue'}):
                return None
        
        return RelationFactory.create_imports_relation(
            importer=str(file_path),
            imported=module_name,
            import_type="module"
        )
    
    def _extract_json_loading_patterns(self, root: Node, file_path: Path, content: str) -> List[Relation]:
        """Extract dynamic JSON loading patterns like fetch(), require(), json.load()."""
        relations = []
        
        # Find all function calls
        for call in self._find_nodes_by_type(root, ['call_expression']):
            call_text = self.extract_node_text(call, content)
            
            # Pattern 1: fetch('config.json')
            if 'fetch(' in call_text:
                json_file = self._extract_string_from_call(call, content, 'fetch')
                if json_file and json_file.endswith('.json'):
                    relation = RelationFactory.create_imports_relation(
                        importer=str(file_path),
                        imported=json_file,
                        import_type="json_fetch"
                    )
                    relations.append(relation)
            
            # Pattern 2: require('./config.json')
            elif 'require(' in call_text:
                json_file = self._extract_string_from_call(call, content, 'require')
                if json_file and json_file.endswith('.json'):
                    relation = RelationFactory.create_imports_relation(
                        importer=str(file_path),
                        imported=json_file,
                        import_type="json_require"
                    )
                    relations.append(relation)
            
            # Pattern 3: JSON.parse() with file content
            elif 'JSON.parse(' in call_text:
                # Look for potential file references in the arguments
                for child in call.children:
                    if child.type == 'arguments':
                        arg_text = self.extract_node_text(child, content)
                        # Simple heuristic: if contains .json in string
                        if '.json' in arg_text:
                            import re
                            json_match = re.search(r'["\']([^"\']*\.json)["\']', arg_text)
                            if json_match:
                                json_file = json_match.group(1)
                                relation = RelationFactory.create_imports_relation(
                                    importer=str(file_path),
                                    imported=json_file,
                                    import_type="json_parse"
                                )
                                relations.append(relation)
        
        return relations
    
    def _extract_string_from_call(self, call_node: Node, content: str, function_name: str) -> Optional[str]:
        """Extract string argument from a function call."""
        # Find arguments node
        for child in call_node.children:
            if child.type == 'arguments':
                # Get first string argument
                for arg in child.children:
                    if arg.type == 'string':
                        string_value = self.extract_node_text(arg, content)
                        # Remove quotes
                        return string_value.strip('\'"')
        return None
    
    def _create_function_call_relations(self, chunks: List[EntityChunk], file_path: Path, entities_or_names) -> List[Relation]:
        """Create CALLS relations only for project-defined entities."""
        relations = []
        
        # Build set of available entity names for validation
        if isinstance(entities_or_names, list) and entities_or_names:
            if hasattr(entities_or_names[0], 'name'):
                # It's a list of Entity objects
                entity_names = {entity.name for entity in entities_or_names}
            else:
                # It's already a list of names
                entity_names = set(entities_or_names)
        else:
            entity_names = set()
        
        for chunk in chunks:
            if chunk.chunk_type == "implementation":
                calls = chunk.metadata.get("semantic_metadata", {}).get("calls", [])
                
                for called_function in calls:
                    # Only create relations to entities we actually indexed
                    if called_function in entity_names:
                        relation = RelationFactory.create_calls_relation(
                            caller=chunk.entity_name,
                            callee=called_function,
                            context=f"Function call in {file_path.name}"
                        )
                        relations.append(relation)
        
        return relations
    
    def _extract_inheritance_relations(self, root: Node, file_path: Path, content: str) -> List[Relation]:
        """Extract class inheritance relations (extends/implements)."""
        relations = []
        
        for class_node in self._find_nodes_by_type(root, ['class_declaration']):
            class_name = self._get_class_name(class_node, content)
            if not class_name:
                continue
                
            # Look for class heritage (extends/implements)
            for child in class_node.children:
                if child.type == 'class_heritage':
                    # JavaScript AST has direct 'extends' and 'identifier' nodes under class_heritage
                    extends_found = False
                    for heritage_child in child.children:
                        if heritage_child.type == 'extends':
                            extends_found = True
                        elif heritage_child.type in ['identifier', 'type_identifier'] and extends_found:
                            parent_name = self.extract_node_text(heritage_child, content)
                            relation = RelationFactory.create_inherits_relation(
                                subclass=class_name,
                                superclass=parent_name,
                                context=f"{class_name} extends {parent_name}"
                            )
                            relations.append(relation)
                            extends_found = False  # Reset for next potential inheritance
                        
                        # Handle TypeScript extends_clause and implements_clause (if they exist)
                        elif heritage_child.type == 'extends_clause':
                            # Find parent class name inside extends_clause
                            for extends_child in heritage_child.children:
                                if extends_child.type in ['identifier', 'type_identifier']:
                                    parent_name = self.extract_node_text(extends_child, content)
                                    relation = RelationFactory.create_inherits_relation(
                                        subclass=class_name,
                                        superclass=parent_name,
                                        context=f"{class_name} extends {parent_name}"
                                    )
                                    relations.append(relation)
                        
                        elif heritage_child.type == 'implements_clause':
                            # Find interface name inside implements_clause
                            for implements_child in heritage_child.children:
                                if implements_child.type in ['identifier', 'type_identifier']:
                                    interface_name = self.extract_node_text(implements_child, content)
                                    relation = RelationFactory.create_inherits_relation(
                                        subclass=class_name,
                                        superclass=interface_name,
                                        context=f"{class_name} implements {interface_name}"
                                    )
                                    relations.append(relation)
                        
        
        return relations
    
    def _extract_exception_relations(self, root: Node, file_path: Path, content: str) -> List[Relation]:
        """Extract exception handling relations (try/catch/throw)."""
        relations = []
        
        # Extract try statements - focus on meaningful exception relations
        # Note: try/catch blocks are captured via throw statement relations to exception classes
        
        # Extract throw statements
        for throw_node in self._find_nodes_by_type(root, ['throw_statement']):
            containing_function = self._find_containing_function(throw_node, content)
            if containing_function:
                # Extract exception type from throw statement
                exception_type = self._extract_exception_type(throw_node, content)
                relation = RelationFactory.create_calls_relation(
                    caller=containing_function,
                    callee=exception_type,  # Point to actual exception class
                    context=f"{containing_function} throws {exception_type}"
                )
                relations.append(relation)
        
        return relations
    
    def _extract_decorator_relations(self, root: Node, file_path: Path, content: str) -> List[Relation]:
        """Extract TypeScript decorator relations."""
        relations = []
        
        for decorator_node in self._find_nodes_by_type(root, ['decorator']):
            # Extract decorator name
            decorator_name = self._extract_decorator_name(decorator_node, content)
            if not decorator_name:
                continue
            
            # Find what the decorator applies to
            target = self._find_decorator_target(decorator_node, content)
            if target:
                relation = RelationFactory.create_calls_relation(
                    caller=target,
                    callee=decorator_name,  # Point to decorator function name without @
                    context=f"{target} uses decorator @{decorator_name}"
                )
                relations.append(relation)
        
        return relations
    
    def _get_class_name(self, class_node: Node, content: str) -> Optional[str]:
        """Extract class name from class declaration."""
        for child in class_node.children:
            if child.type in ['type_identifier', 'identifier']:
                return self.extract_node_text(child, content)
        return None
    
    def _find_containing_function(self, node: Node, content: str) -> Optional[str]:
        """Find the function that contains the given node."""
        current = node.parent
        while current:
            if current.type in ['function_declaration', 'arrow_function', 'method_definition']:
                return self._extract_function_name(current, content)
            current = current.parent
        return None
    
    def _get_catch_parameter(self, catch_node: Node, content: str) -> str:
        """Extract parameter name from catch clause."""
        for child in catch_node.children:
            if child.type == 'identifier':
                return self.extract_node_text(child, content)
        return "error"
    
    def _extract_exception_type(self, throw_node: Node, content: str) -> str:
        """Extract exception type from throw statement."""
        for child in throw_node.children:
            if child.type == 'new_expression':
                # Look for constructor name
                for new_child in child.children:
                    if new_child.type == 'identifier':
                        return self.extract_node_text(new_child, content)
            elif child.type == 'identifier':
                return self.extract_node_text(child, content)
        return "Error"
    
    def _extract_decorator_name(self, decorator_node: Node, content: str) -> Optional[str]:
        """Extract decorator name from decorator node."""
        for child in decorator_node.children:
            if child.type == 'identifier':
                return self.extract_node_text(child, content)
            elif child.type == 'call_expression':
                # Handle decorators with parameters like @Component()
                for call_child in child.children:
                    if call_child.type == 'identifier':
                        return self.extract_node_text(call_child, content)
        return None
    
    def _find_decorator_target(self, decorator_node: Node, content: str) -> Optional[str]:
        """Find what the decorator applies to (class, method, property)."""
        parent = decorator_node.parent
        if not parent:
            return None
            
        # Handle direct parent types
        if parent.type == 'class_declaration':
            return self._get_class_name(parent, content)
        elif parent.type == 'method_definition':
            return self._extract_function_name(parent, content)
        elif parent.type in ['property_definition', 'field_definition', 'public_field_definition']:
            return self._get_property_name(parent, content)
        
        # Handle TypeScript method decorators (parent is class_body)
        elif parent.type == 'class_body':
            # Find the next sibling that is a method_definition
            decorator_index = None
            for i, child in enumerate(parent.children):
                if child == decorator_node:
                    decorator_index = i
                    break
            
            if decorator_index is not None:
                # Look for the next method_definition sibling
                for j in range(decorator_index + 1, len(parent.children)):
                    sibling = parent.children[j]
                    if sibling.type == 'method_definition':
                        return self._extract_function_name(sibling, content)
        
        return None
    
    def _get_property_name(self, prop_node: Node, content: str) -> Optional[str]:
        """Extract property name from property definition."""
        for child in prop_node.children:
            if child.type in ['property_identifier', 'identifier']:
                return self.extract_node_text(child, content)
        return None

    def _extract_variables(self, root: Node, file_path: Path, content: str) -> List[Entity]:
        """
        ENHANCED variable extraction with comprehensive fixes:
        1. Destructuring pattern support (object_pattern, array_pattern)
        2. Enhanced scope filtering (block scope awareness) 
        3. Individual variable name extraction
        4. Recursive destructuring patterns
        """
        variables = []
        seen_variables = set()  # Track variable names to avoid duplicates
        
        def traverse_for_variables(node, scope_context=None):
            """Enhanced traversal with scope awareness and destructuring support."""
            
            # Enhanced scope tracking - track all block-creating contexts
            current_scope = scope_context
            if node.type in [
                'function_declaration', 'arrow_function', 'function_expression', 
                'method_definition', 'for_statement', 'for_in_statement', 
                'for_of_statement', 'while_statement', 'if_statement', 
                'block_statement', 'try_statement', 'catch_clause'
            ]:
                current_scope = node.type
            
            # For any scope-creating context, propagate to children
            # This ensures variables inside functions stay function-scoped
            elif scope_context in ['function_declaration', 'arrow_function', 'function_expression', 'method_definition']:
                current_scope = scope_context
            
            # Extract variables from declarations
            if node.type in ['variable_declaration', 'lexical_declaration']:
                # Only process direct children of this declaration, not nested ones
                direct_declarators = [child for child in node.children if child.type == 'variable_declarator']
                for declarator in direct_declarators:
                    name_node = declarator.child_by_field_name('name')
                    if name_node:
                        # Extract variables based on pattern type
                        extracted_vars = self._extract_variable_names_from_pattern(
                            name_node, content, declarator, file_path, current_scope
                        )
                        for var_entity in extracted_vars:
                            if var_entity.name not in seen_variables:
                                seen_variables.add(var_entity.name)
                                variables.append(var_entity)
            
            # Extract from assignment expressions (e.g., assigned = "value")
            elif node.type == 'assignment_expression':
                left_node = node.child_by_field_name('left')
                if left_node and left_node.type == 'identifier':
                    # Only include module-level assignments
                    if current_scope is None:  # Module level
                        var_name = self.extract_node_text(left_node, content)
                        if var_name and self._should_include_variable(var_name, current_scope):
                            if var_name not in seen_variables:
                                seen_variables.add(var_name)
                                entity = Entity(
                                    name=var_name,
                                    entity_type=EntityType.VARIABLE,
                                    observations=[
                                        f"Variable: {var_name}",
                                        f"Defined in: {file_path}",
                                        f"Line: {node.start_point[0] + 1}",
                                        f"Assignment expression"
                                    ],
                                    file_path=file_path,
                                    line_number=node.start_point[0] + 1,
                                    end_line_number=node.end_point[0] + 1
                                )
                                variables.append(entity)
            
            # Recursively traverse children
            for child in node.children:
                traverse_for_variables(child, current_scope)
        
        traverse_for_variables(root)
        return variables
    
    def _extract_variable_names_from_pattern(self, pattern_node: Node, content: str, 
                                          declarator: Node, file_path: Path, 
                                          scope_context: str) -> List[Entity]:
        """
        Extract individual variable names from different pattern types.
        Handles: identifier, object_pattern, array_pattern, rest_pattern.
        """
        variables = []
        
        if pattern_node.type == 'identifier':
            # Simple variable declaration
            var_name = self.extract_node_text(pattern_node, content)
            if var_name and self._should_include_variable(var_name, scope_context):
                entity = Entity(
                    name=var_name,
                    entity_type=EntityType.VARIABLE,
                    observations=[
                        f"Variable: {var_name}",
                        f"Defined in: {file_path}",
                        f"Line: {declarator.start_point[0] + 1}"
                    ],
                    file_path=file_path,
                    line_number=declarator.start_point[0] + 1,
                    end_line_number=declarator.end_point[0] + 1
                )
                variables.append(entity)
        
        elif pattern_node.type == 'object_pattern':
            # Object destructuring: const {name, age} = user
            variables.extend(
                self._extract_from_object_pattern(pattern_node, content, declarator, file_path, scope_context)
            )
        
        elif pattern_node.type == 'array_pattern':
            # Array destructuring: const [first, second] = array
            variables.extend(
                self._extract_from_array_pattern(pattern_node, content, declarator, file_path, scope_context)
            )
        
        return variables
    
    def _extract_from_object_pattern(self, object_node: Node, content: str,
                                   declarator: Node, file_path: Path, 
                                   scope_context: str) -> List[Entity]:
        """
        Extract variables from object destructuring patterns.
        Handles: {name, age}, {username: uname}, nested {address: {street, city}}
        """
        variables = []
        
        for child in object_node.children:
            if child.type == 'shorthand_property_identifier_pattern':
                # Shorthand: {name}
                var_name = self.extract_node_text(child, content)
                if var_name and self._should_include_variable(var_name, scope_context):
                    entity = self._create_variable_entity(var_name, file_path, declarator, "object destructuring")
                    variables.append(entity)
            
            elif child.type == 'object_assignment_pattern':
                # Assignment with default: {timeout = 5000}
                # Find the identifier being assigned
                for assignment_child in child.children:
                    if assignment_child.type == 'shorthand_property_identifier_pattern':
                        var_name = self.extract_node_text(assignment_child, content)
                        if var_name and self._should_include_variable(var_name, scope_context):
                            entity = self._create_variable_entity(var_name, file_path, declarator, "object destructuring with default")
                            variables.append(entity)
                        break
            
            elif child.type == 'pair_pattern':
                # Renamed destructuring: {username: uname} or nested: {address: {street, city}}
                # Find the identifier child (the variable name)
                for pair_child in child.children:
                    if pair_child.type == 'identifier':
                        # This is the variable name being assigned
                        var_name = self.extract_node_text(pair_child, content)
                        if var_name and self._should_include_variable(var_name, scope_context):
                            entity = self._create_variable_entity(var_name, file_path, declarator, "object destructuring")
                            variables.append(entity)
                    elif pair_child.type == 'object_pattern':
                        # Nested object pattern: {address: {street, city}}
                        nested_vars = self._extract_from_object_pattern(
                            pair_child, content, declarator, file_path, scope_context
                        )
                        variables.extend(nested_vars)
                    elif pair_child.type == 'array_pattern':
                        # Nested array pattern
                        nested_vars = self._extract_from_array_pattern(
                            pair_child, content, declarator, file_path, scope_context
                        )
                        variables.extend(nested_vars)
            
            elif child.type == 'rest_pattern':
                # Rest: {...rest}
                id_node = child.child_by_field_name('name')
                if id_node and id_node.type == 'identifier':
                    var_name = self.extract_node_text(id_node, content)
                    if var_name and self._should_include_variable(var_name, scope_context):
                        entity = self._create_variable_entity(var_name, file_path, declarator, "rest pattern")
                        variables.append(entity)
        
        return variables
    
    def _extract_from_array_pattern(self, array_node: Node, content: str,
                                  declarator: Node, file_path: Path,
                                  scope_context: str) -> List[Entity]:
        """
        Extract variables from array destructuring patterns.
        Handles: [first, second], [head, ...tail]
        """
        variables = []
        
        for child in array_node.children:
            if child.type == 'identifier':
                # Simple element: [first, second]
                var_name = self.extract_node_text(child, content)
                if var_name and self._should_include_variable(var_name, scope_context):
                    entity = self._create_variable_entity(var_name, file_path, declarator, "array destructuring")
                    variables.append(entity)
            
            elif child.type == 'rest_pattern':
                # Rest element: [...tail]
                # Based on AST, rest_pattern has direct identifier child
                for rest_child in child.children:
                    if rest_child.type == 'identifier':
                        var_name = self.extract_node_text(rest_child, content)
                        if var_name and self._should_include_variable(var_name, scope_context):
                            entity = self._create_variable_entity(var_name, file_path, declarator, "array rest pattern")
                            variables.append(entity)
                        break
                
                # Fallback to field-based access
                id_node = child.child_by_field_name('name')
                if id_node and id_node.type == 'identifier':
                    var_name = self.extract_node_text(id_node, content)
                    if var_name and self._should_include_variable(var_name, scope_context):
                        entity = self._create_variable_entity(var_name, file_path, declarator, "array rest pattern")
                        variables.append(entity)
            
            elif child.type in ['object_pattern', 'array_pattern']:
                # Nested destructuring: [[a, b], {c, d}]
                nested_vars = self._extract_variable_names_from_pattern(
                    child, content, declarator, file_path, scope_context
                )
                variables.extend(nested_vars)
        
        return variables
    
    def _should_include_variable(self, var_name: str, scope_context: str) -> bool:
        """
        Enhanced scope filtering - exclude function-local and block-scoped variables.
        Only include module-level variables (like Python parser logic).
        """
        # Skip if inside any function or block scope
        if scope_context in [
            'function_declaration', 'arrow_function', 'function_expression', 
            'method_definition', 'for_statement', 'for_in_statement', 
            'for_of_statement', 'while_statement', 'if_statement', 
            'block_statement', 'try_statement', 'catch_clause'
        ]:
            return False
        
        # Skip common loop variables and temporary names
        if var_name in ['i', 'j', 'k', 'index', 'item', 'key', 'value', 'temp', 'tmp']:
            return False
        
        # Skip very short variable names that are likely temporary
        if len(var_name) <= 1:
            return False
        
        return True
    
    def _create_variable_entity(self, var_name: str, file_path: Path, 
                              declarator: Node, pattern_type: str) -> Entity:
        """Helper to create variable entity with consistent structure."""
        return Entity(
            name=var_name,
            entity_type=EntityType.VARIABLE,
            observations=[
                f"Variable: {var_name}",
                f"Defined in: {file_path}",
                f"Line: {declarator.start_point[0] + 1}",
                f"Pattern: {pattern_type}"
            ],
            file_path=file_path,
            line_number=declarator.start_point[0] + 1,
            end_line_number=declarator.end_point[0] + 1
        )

    def _init_ts_server(self):
        """Initialize TypeScript language server (stub for future implementation)."""
        # Future: Could integrate with tsserver for advanced type inference
        return None