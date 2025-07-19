"""Unit tests for code parsing functionality."""

from pathlib import Path

from claude_indexer.analysis.entities import Entity, EntityType
from claude_indexer.analysis.parser import (
    MarkdownParser,
    ParserRegistry,
    ParserResult,
    PythonParser,
)

# Sample code for testing
PYTHON_CODE = '''"""Sample Python module for testing."""

def add(x, y):
    """Add two numbers."""
    return x + y

class Calculator:
    """A simple calculator class."""

    def __init__(self, name="default"):
        """Initialize calculator."""
        self.name = name

    def multiply(self, a, b):
        """Multiply two numbers."""
        return a * b

# Module variable
DEFAULT_PRECISION = 2
'''

MARKDOWN_CODE = """# Main Title

This is a markdown document for testing.

## Section 1

Some content here.

### Subsection 1.1

More content.

## Section 2

Final section.

### Subsection 2.1

Content in subsection.

#### Deep Subsection

Very deep content.
"""


class TestPythonParser:
    """Test Python file parsing functionality."""

    def test_python_parser_initialization(self, tmp_path):
        """Test Python parser initialization."""
        parser = PythonParser(tmp_path)

        assert parser.project_path == tmp_path
        assert parser.can_parse(tmp_path / "test.py")
        assert not parser.can_parse(tmp_path / "test.txt")
        assert ".py" in parser.get_supported_extensions()

    def test_python_parser_without_tree_sitter(self, tmp_path, monkeypatch):
        """Test parser graceful handling when tree-sitter unavailable."""
        # Mock tree-sitter unavailable
        monkeypatch.setattr(
            "claude_indexer.analysis.parser.TREE_SITTER_AVAILABLE", False
        )

        # Parser should still instantiate but not process files
        parser = PythonParser(tmp_path)

        # Should return False for all files when tree-sitter unavailable
        assert not parser.can_parse(tmp_path / "test.py")

    def test_can_parse_method(self, tmp_path):
        """Test file type detection."""
        parser = PythonParser(tmp_path)

        # Python files should be parseable
        assert parser.can_parse(tmp_path / "script.py")
        assert parser.can_parse(tmp_path / "module.py")

        # Other files should not be
        assert not parser.can_parse(tmp_path / "readme.md")
        assert not parser.can_parse(tmp_path / "config.json")
        assert not parser.can_parse(tmp_path / "style.css")

    def test_parse_simple_function(self, tmp_path):
        """Test parsing a simple function."""
        test_file = tmp_path / "simple.py"
        test_file.write_text('''def hello():
    """Say hello."""
    return "Hello, World!"
''')

        parser = PythonParser(tmp_path)
        result = parser.parse(test_file)

        assert result.success
        assert len(result.entities) >= 2  # File + function
        assert result.file_hash != ""
        assert result.parsing_time > 0

        # Find function entity
        function_entities = [
            e for e in result.entities if e.entity_type == EntityType.FUNCTION
        ]
        assert len(function_entities) >= 1

        hello_func = next((e for e in function_entities if e.name == "hello"), None)
        assert hello_func is not None
        assert hello_func.file_path == test_file
        assert hello_func.line_number is not None

    def test_parse_class_with_methods(self, tmp_path):
        """Test parsing a class with methods."""
        test_file = tmp_path / "class_test.py"
        test_file.write_text('''class TestClass:
    """A test class."""

    def __init__(self):
        """Initialize."""
        pass

    def method(self):
        """A method."""
        return True
''')

        parser = PythonParser(tmp_path)
        result = parser.parse(test_file)

        assert result.success

        # Find class entity
        class_entities = [
            e for e in result.entities if e.entity_type == EntityType.CLASS
        ]
        assert len(class_entities) >= 1

        test_class = next((e for e in class_entities if e.name == "TestClass"), None)
        assert test_class is not None

        # Find method entities
        function_entities = [
            e for e in result.entities if e.entity_type == EntityType.FUNCTION
        ]
        method_names = {f.name for f in function_entities}
        assert "__init__" in method_names
        assert "method" in method_names

    def test_parse_complex_module(self, tmp_path):
        """Test parsing a complex module."""
        test_file = tmp_path / "complex.py"
        test_file.write_text(PYTHON_CODE)

        parser = PythonParser(tmp_path)
        result = parser.parse(test_file)

        assert result.success
        assert len(result.entities) >= 4  # File + function + class + methods
        assert len(result.relations) >= 2  # Contains relations

        # Check for specific entities
        entity_names = {e.name for e in result.entities}
        assert "add" in entity_names
        assert "Calculator" in entity_names

        # Check containment relations
        file_path_str = str(test_file)
        contains_relations = [
            r for r in result.relations if r.from_entity == file_path_str
        ]
        assert len(contains_relations) >= 2  # File contains function and class

    def test_parse_with_imports(self, tmp_path):
        """Test parsing file with imports."""
        test_file = tmp_path / "with_imports.py"
        test_file.write_text('''import os
from pathlib import Path
import json as js

def process_file(path):
    """Process a file."""
    return os.path.exists(path)
''')

        parser = PythonParser(tmp_path)
        result = parser.parse(test_file)

        assert result.success

        # Check for import relations
        import_relations = [
            r for r in result.relations if "import" in r.relation_type.value.lower()
        ]
        assert len(import_relations) >= 1  # Should have import relations

    def test_parse_syntax_error_handling(self, tmp_path):
        """Test handling of syntax errors."""
        test_file = tmp_path / "syntax_error.py"
        test_file.write_text("""def broken_function(
    # Intentional syntax error - missing closing parenthesis
    return "This won't parse"
""")

        parser = PythonParser(tmp_path)
        result = parser.parse(test_file)

        # Parser should handle errors gracefully
        # Even with syntax errors, tree-sitter may extract some entities
        assert isinstance(result, ParserResult)
        assert result.file_path == test_file
        # May or may not be successful depending on tree-sitter's error recovery

    def test_parse_empty_file(self, tmp_path):
        """Test parsing an empty file."""
        test_file = tmp_path / "empty.py"
        test_file.write_text("")

        parser = PythonParser(tmp_path)
        result = parser.parse(test_file)

        assert result.success
        assert len(result.entities) >= 1  # At least file entity
        assert result.file_hash != ""  # Should have hash of empty content

    def test_parse_docstring_extraction(self, tmp_path):
        """Test that docstrings are properly extracted."""
        test_file = tmp_path / "docstrings.py"
        test_file.write_text('''"""Module docstring."""

def documented_function():
    """This function has a docstring."""
    pass

class DocumentedClass:
    """This class has a docstring."""

    def method_with_doc(self):
        """This method has a docstring."""
        pass
''')

        parser = PythonParser(tmp_path)
        result = parser.parse(test_file)

        assert result.success

        # Check that entities with docstrings are captured
        documented_entities = [e for e in result.entities if e.docstring]
        assert len(documented_entities) >= 1  # Should find entities with docstrings


class TestMarkdownParser:
    """Test Markdown file parsing functionality."""

    def test_markdown_parser_initialization(self):
        """Test Markdown parser initialization."""
        parser = MarkdownParser()

        assert parser.can_parse(Path("test.md"))
        assert parser.can_parse(Path("test.markdown"))
        assert not parser.can_parse(Path("test.py"))

        extensions = parser.get_supported_extensions()
        assert ".md" in extensions
        assert ".markdown" in extensions

    def test_can_parse_method(self):
        """Test file type detection."""
        parser = MarkdownParser()

        # Markdown files should be parseable
        assert parser.can_parse(Path("readme.md"))
        assert parser.can_parse(Path("docs.markdown"))
        assert parser.can_parse(Path("README.MD"))  # Case insensitive

        # Other files should not be
        assert not parser.can_parse(Path("script.py"))
        assert not parser.can_parse(Path("config.json"))
        assert not parser.can_parse(Path("style.css"))

    def test_parse_simple_markdown(self, tmp_path):
        """Test parsing simple markdown content."""
        test_file = tmp_path / "simple.md"
        test_file.write_text("""# Main Header

Some content here.

## Second Header

More content.
""")

        parser = MarkdownParser()
        result = parser.parse(test_file)

        assert result.success
        assert len(result.entities) >= 3  # File + 2 headers
        assert result.file_hash != ""

        # Check for header entities
        doc_entities = [
            e for e in result.entities if e.entity_type == EntityType.DOCUMENTATION
        ]
        header_names = {e.name for e in doc_entities}
        assert "Main Header" in header_names
        assert "Second Header" in header_names

    def test_parse_complex_markdown(self, tmp_path):
        """Test parsing complex markdown with multiple header levels."""
        test_file = tmp_path / "complex.md"
        test_file.write_text(MARKDOWN_CODE)

        parser = MarkdownParser()
        result = parser.parse(test_file)

        assert result.success
        assert len(result.entities) >= 6  # File + multiple headers

        # Check header levels are captured
        doc_entities = [
            e for e in result.entities if e.entity_type == EntityType.DOCUMENTATION
        ]
        header_levels = [
            e.metadata.get("header_level") for e in doc_entities if e.metadata
        ]

        assert 1 in header_levels  # H1
        assert 2 in header_levels  # H2
        assert 3 in header_levels  # H3
        assert 4 in header_levels  # H4

    def test_parse_empty_markdown(self, tmp_path):
        """Test parsing empty markdown file."""
        test_file = tmp_path / "empty.md"
        test_file.write_text("")

        parser = MarkdownParser()
        result = parser.parse(test_file)

        assert result.success
        assert len(result.entities) >= 1  # At least file entity

    def test_parse_markdown_with_code_blocks(self, tmp_path):
        """Test parsing markdown with code blocks (should ignore code content)."""
        test_file = tmp_path / "with_code.md"
        test_file.write_text("""# Documentation

Some text here.

```python
def code_function():
    pass
```

## Another Section

More text.
""")

        parser = MarkdownParser()
        result = parser.parse(test_file)

        assert result.success

        # Should find headers but not parse code within code blocks
        doc_entities = [
            e for e in result.entities if e.entity_type == EntityType.DOCUMENTATION
        ]
        header_names = {e.name for e in doc_entities}
        assert "Documentation" in header_names
        assert "Another Section" in header_names
        # Should not create entities for code within markdown
        assert "code_function" not in header_names

    def test_parse_malformed_headers(self, tmp_path):
        """Test handling of malformed headers."""
        test_file = tmp_path / "malformed.md"
        test_file.write_text("""# Valid Header

## Another Valid Header

###

####Empty header

#####

Regular text with # that's not a header
""")

        parser = MarkdownParser()
        result = parser.parse(test_file)

        assert result.success

        # Should handle malformed headers gracefully
        doc_entities = [
            e for e in result.entities if e.entity_type == EntityType.DOCUMENTATION
        ]
        header_names = {e.name for e in doc_entities}
        assert "Valid Header" in header_names
        assert "Another Valid Header" in header_names
        # Empty headers should be filtered out
        assert "" not in header_names


class TestParserRegistry:
    """Test the parser registry functionality."""

    def test_registry_initialization(self, tmp_path):
        """Test parser registry initialization."""
        registry = ParserRegistry(tmp_path)

        # Should have default parsers registered
        extensions = registry.get_supported_extensions()
        assert ".py" in extensions
        assert ".md" in extensions

    def test_get_parser_for_file(self, tmp_path):
        """Test getting appropriate parser for files."""
        registry = ParserRegistry(tmp_path)

        # Python files should get Python parser
        python_parser = registry.get_parser_for_file(Path("test.py"))
        assert isinstance(python_parser, PythonParser)

        # Markdown files should get Markdown parser
        md_parser = registry.get_parser_for_file(Path("test.md"))
        assert isinstance(md_parser, MarkdownParser)

        # Unsupported files should return None
        no_parser = registry.get_parser_for_file(Path("test.txt"))
        assert no_parser is None

    def test_parse_file_with_registry(self, tmp_path):
        """Test parsing files through the registry."""
        registry = ParserRegistry(tmp_path)

        # Create test files
        py_file = tmp_path / "test.py"
        py_file.write_text("def test(): pass")

        md_file = tmp_path / "test.md"
        md_file.write_text("# Test Header")

        # Parse Python file
        py_result = registry.parse_file(py_file)
        assert py_result.success
        function_entities = [
            e for e in py_result.entities if e.entity_type == EntityType.FUNCTION
        ]
        assert len(function_entities) >= 1

        # Parse Markdown file
        md_result = registry.parse_file(md_file)
        assert md_result.success
        doc_entities = [
            e for e in md_result.entities if e.entity_type == EntityType.DOCUMENTATION
        ]
        assert len(doc_entities) >= 1

    def test_parse_unsupported_file(self, tmp_path):
        """Test parsing unsupported file type."""
        registry = ParserRegistry(tmp_path)

        # Create unsupported file
        txt_file = tmp_path / "test.txt"
        txt_file.write_text("Just plain text")

        # Should return error result
        result = registry.parse_file(txt_file)
        assert not result.success
        assert len(result.errors) > 0
        assert "No parser available" in result.errors[0]

    def test_register_custom_parser(self, tmp_path):
        """Test registering a custom parser."""
        from claude_indexer.analysis.parser import CodeParser

        class CustomParser(CodeParser):
            def can_parse(self, file_path: Path) -> bool:
                return file_path.suffix == ".custom"

            def get_supported_extensions(self) -> list[str]:
                return [".custom"]

            def parse(self, file_path: Path) -> ParserResult:
                return ParserResult(file_path=file_path, entities=[], relations=[])

        registry = ParserRegistry(tmp_path)
        custom_parser = CustomParser()

        # Register custom parser
        registry.register(custom_parser)

        # Should now support .custom files
        extensions = registry.get_supported_extensions()
        assert ".custom" in extensions

        # Should return custom parser for .custom files
        parser = registry.get_parser_for_file(Path("test.custom"))
        assert isinstance(parser, CustomParser)


class TestParserResult:
    """Test the ParserResult dataclass."""

    def test_parser_result_initialization(self, tmp_path):
        """Test ParserResult initialization."""
        test_file = tmp_path / "test.py"
        result = ParserResult(file_path=test_file, entities=[], relations=[])

        assert result.file_path == test_file
        assert result.entities == []
        assert result.relations == []
        assert result.errors == []
        assert result.warnings == []
        assert result.parsing_time == 0.0
        assert result.file_hash == ""

    def test_parser_result_success_property(self, tmp_path):
        """Test success property based on errors."""
        test_file = tmp_path / "test.py"

        # No errors = success
        result = ParserResult(file_path=test_file, entities=[], relations=[])
        assert result.success

        # With errors = not success
        result.errors.append("Some error")
        assert not result.success

    def test_parser_result_metrics(self, tmp_path):
        """Test entity and relation count properties."""
        from claude_indexer.analysis.entities import EntityType, Relation, RelationType

        test_file = tmp_path / "test.py"

        # Create test entities and relations
        entities = [
            Entity(name="test1", entity_type=EntityType.FUNCTION, observations=[]),
            Entity(name="test2", entity_type=EntityType.CLASS, observations=[]),
        ]
        relations = [
            Relation(
                from_entity="file",
                to_entity="test1",
                relation_type=RelationType.CONTAINS,
            )
        ]

        result = ParserResult(
            file_path=test_file, entities=entities, relations=relations
        )

        assert result.entity_count == 2
        assert result.relation_count == 1
