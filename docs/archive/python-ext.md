# Python File Operation Detection Extension Plan

## Executive Summary

This plan extends the existing Python file operation detection in `PythonParser._extract_file_operations()` to support additional patterns including pandas operations, pathlib methods, requests/API calls, and other common file operations. The implementation maintains full compatibility with v2.4/v2.5 progressive disclosure architecture and reuses existing infrastructure.

## Current State Analysis

### ✅ Already Implemented (Working)
- Basic file operations: `open()`, `json.load()`, `yaml.load()`, `csv.reader()`, `pickle.load()`
- Path operations: `Path().open()`
- Context managers: `with open()` statements
- Tree-sitter AST traversal using `node.type == 'call'`
- Relations created via `RelationFactory.create_imports_relation()` with custom `import_type`

### 🎯 Missing Patterns to Implement
1. **Pandas Operations** (High Priority)
   - `pandas.read_json()`, `pandas.read_csv()`, `pandas.read_excel()`
   - `DataFrame.to_json()`, `DataFrame.to_csv()`, `DataFrame.to_excel()`

2. **Pathlib Extended Operations**
   - `Path().read_text()`, `Path().read_bytes()`
   - `Path().write_text()`, `Path().write_bytes()`

3. **Requests/API Operations**
   - `requests.get('api/data.json').json()`
   - `urllib.request.urlopen()`

4. **Configuration File Operations**
   - `configparser.read()`, `toml.load()`, `xml.etree.ElementTree.parse()`

## Architecture & Implementation Details

### 1. Core Implementation Location
**File**: `/Users/duracula/Documents/GitHub/Claude-code-memory/claude_indexer/analysis/parser.py`
**Method**: `PythonParser._extract_file_operations()` (lines 498-610)

### 2. Implementation Pattern

```python
def _extract_file_operations(self, tree: 'tree_sitter.Tree', file_path: Path, content: str) -> List['Relation']:
    """Extract file operations from Python AST using tree-sitter."""
    relations = []

    # Extended file operation patterns
    FILE_OPERATIONS = {
        # Existing patterns (DO NOT MODIFY)
        'open': 'file_open',
        'json.load': 'json_load',
        # ... existing patterns ...

        # NEW PATTERNS TO ADD:
        # Pandas operations
        'pandas.read_json': 'pandas_json_read',
        'pandas.read_csv': 'pandas_csv_read',
        'pandas.read_excel': 'pandas_excel_read',
        'pd.read_json': 'pandas_json_read',  # Common alias
        'pd.read_csv': 'pandas_csv_read',
        'pd.read_excel': 'pandas_excel_read',
        '.to_json': 'pandas_json_write',
        '.to_csv': 'pandas_csv_write',
        '.to_excel': 'pandas_excel_write',

        # Pathlib operations
        '.read_text': 'path_read_text',
        '.read_bytes': 'path_read_bytes',
        '.write_text': 'path_write_text',
        '.write_bytes': 'path_write_bytes',

        # Requests operations
        'requests.get': 'requests_get',
        'requests.post': 'requests_post',
        'urllib.request.urlopen': 'urllib_open',

        # Config operations
        'configparser.read': 'config_ini_read',
        'toml.load': 'toml_read',
        'xml.etree.ElementTree.parse': 'xml_parse',
    }
```

### 3. AST Node Processing Enhancement

#### Pattern 1: Method Chaining Detection
For patterns like `requests.get().json()` or `df.to_json()`:

```python
# Inside find_file_operations() function
if node.type == 'call':
    func_node = node.child_by_field_name('function')

    # Handle method calls (e.g., df.to_json())
    if func_node and func_node.type == 'attribute':
        attr_value = func_node.child_by_field_name('attr')
        if attr_value:
            method_name = '.' + attr_value.text.decode('utf-8')
            if method_name in FILE_OPERATIONS:
                # Extract file path from arguments
                # Create relation with appropriate import_type
```

#### Pattern 2: Chained Method Calls
For `requests.get('url').json()`:

```python
# Check for chained calls by looking at parent nodes
parent = node.parent
if parent and parent.type == 'attribute':
    # This is a chained call like .json()
    # Look for the original call (requests.get)
```

### 4. String Extraction Enhancement

Current implementation handles simple string literals. Need to add:

1. **F-string support**: `f"data_{date}.json"`
2. **String concatenation**: `"data/" + filename + ".json"`
3. **Variable references**: Track simple variable assignments

```python
def extract_string_or_path(self, node, content):
    """Enhanced string extraction supporting f-strings and variables."""
    if node.type == 'string':
        # Existing string literal handling
    elif node.type == 'formatted_string':
        # Extract f-string pattern
        # Return simplified representation like "data_*.json"
    elif node.type == 'identifier':
        # Could implement simple variable tracking
        # For MVP: return None (skip complex cases)
```

### 5. Relation Type Mapping

All new relations use existing `RelationFactory.create_imports_relation()` with semantic `import_type`:

```python
relation = RelationFactory.create_imports_relation(
    importer=str(file_path),
    imported=extracted_file_path,
    import_type=operation_type  # e.g., 'pandas_csv_read'
)
```

## Testing Strategy

### 1. Test File Creation
Create comprehensive test file `/Users/duracula/Documents/GitHub/Claude-code-memory/test-debug/test_python_extended.py`:

```python
import pandas as pd
from pathlib import Path
import requests
import configparser
import toml
import xml.etree.ElementTree as ET

# Pandas operations
df = pd.read_json('sales_data.json')
df2 = pd.read_csv('customers.csv')
df3 = pd.read_excel('inventory.xlsx')

# DataFrame exports
df.to_json('output/results.json')
df.to_csv('output/results.csv')
df.to_excel('output/results.xlsx')

# Pathlib operations
config_text = Path('config.txt').read_text()
binary_data = Path('data.bin').read_bytes()
Path('output.txt').write_text('results')

# Requests operations
data = requests.get('https://api.example.com/data.json').json()
response = requests.post('api/upload.json', json=data)

# Config file operations
config = configparser.ConfigParser()
config.read('settings.ini')

settings = toml.load('pyproject.toml')
tree = ET.parse('config.xml')
```

### 2. Test Execution
```bash
# Use watcher-test collection for safety
claude-indexer -p /path/to/test-debug -c watcher-test --verbose

# Expected results:
# - Original relations: ~35
# - New file operation relations: ~15-20
# - Total relations: ~50-55
```

### 3. Validation Queries
```python
# In claude-indexer CLI or Python script
from claude_indexer.storage.qdrant import QdrantStore

store = QdrantStore(config)
relations = store.search_similar("pandas_csv_read", limit=20)
# Should return relations with import_type='pandas_csv_read'
```

## Compatibility & Non-Breaking Changes

### ✅ v2.4/v2.5 Compatibility Maintained
1. **No chunk format changes** - Only adds Relations, not chunks
2. **EntityChunk structure unchanged** - Still uses metadata/implementation types
3. **Progressive disclosure preserved** - File operations are relations, not entity modifications
4. **Existing patterns untouched** - All current detections continue working

### ✅ No Code Duplication
1. **Reuses `_find_nodes_by_type()`** from base_parsers.py
2. **Reuses `extract_string_literal()`** helper
3. **Reuses `RelationFactory.create_imports_relation()`**
4. **Extends existing `FILE_OPERATIONS` dictionary**

## Implementation Steps for Developer

### Phase 1: Core Extension (2 hours)
1. Open `/claude_indexer/analysis/parser.py`
2. Locate `_extract_file_operations()` method (line 498)
3. Add new patterns to `FILE_OPERATIONS` dictionary
4. Enhance node processing for method calls (`.to_json()` patterns)
5. Test with basic pandas operations

### Phase 2: Advanced Patterns (1 hour)
1. Add chained method detection (`requests.get().json()`)
2. Implement f-string basic support
3. Add pathlib method detection
4. Test with comprehensive test file

### Phase 3: Testing & Validation (1 hour)
1. Create test file with all patterns
2. Run indexing with watcher-test collection
3. Verify relation counts and types
4. Check logs for any parsing errors
5. Query Qdrant to validate relations stored correctly

## Error Handling & Edge Cases

### Handle Gracefully
- Missing file path arguments: Skip relation creation
- Complex string expressions: Log debug message, skip
- Unsupported patterns: Silent skip (no error)
- API URLs vs file paths: Check for file extensions

### Logging Strategy
```python
if self.logger.is_debug_enabled():
    self.logger.debug(f"Detected {operation_type}: {file_ref}")
```

## Performance Considerations

- **No performance regression**: Adds ~10-20 condition checks per file
- **Maintains O(n) complexity**: Single AST traversal
- **Memory efficient**: No additional data structures
- **Fast pattern matching**: Dictionary lookup O(1)

## Summary & Benefits

### What This Delivers
1. **Comprehensive file operation detection** across popular Python libraries
2. **Semantic relation types** for better search and understanding
3. **Full pandas/pathlib/requests coverage** for modern Python code
4. **Backward compatible** with all existing functionality
5. **Production-ready** implementation following established patterns

### What This Enables
- Search: "Show me all files that read CSV data"
- Understanding: "What files does this pandas pipeline depend on?"
- Impact analysis: "If I change sales_data.json, what code is affected?"
- Documentation: Auto-generated data flow diagrams

### Success Metrics
- ✅ 15+ new relation types detected
- ✅ Zero breaking changes
- ✅ All tests passing
- ✅ Performance within 5% of current
- ✅ Compatible with MCP server progressive disclosure

## Additional Testing Scenarios

### Edge Case Tests
```python
# test_edge_cases.py

# Nested function calls
data = json.load(open('nested.json'))
pd.read_csv(Path('data.csv'))

# Multiple operations on same line
df1, df2 = pd.read_json('a.json'), pd.read_csv('b.csv')

# URLs that look like files
api_data = requests.get('https://api.com/data.json').json()

# Chained operations
Path('out.json').write_text(json.dumps(data))

# With statements
with Path('config.yaml').open() as f:
    config = yaml.load(f)
```

### Expected Behavior
- Nested calls: Detect both operations if possible
- Multiple operations: Create separate relations for each
- URLs with .json: Create relation (let user filter if needed)
- Chained operations: Detect all file references

## Implementation Checklist

- [ ] Extend FILE_OPERATIONS dictionary with new patterns
- [ ] Add method call detection (`.to_json()` style)
- [ ] Update string extraction for method arguments
- [ ] Handle pandas alias `pd` in addition to `pandas`
- [ ] Add debug logging for detected operations
- [ ] Create comprehensive test file
- [ ] Run tests with watcher-test collection
- [ ] Verify no performance regression
- [ ] Update documentation if needed

## Potential Issues & Solutions

### Issue 1: Method Call Detection
**Problem**: `.to_json()` is called on a variable, not directly on pandas
**Solution**: Check if node type is 'attribute' and extract method name from 'attr' field

### Issue 2: Import Alias Detection
**Problem**: Code uses `pd` but FILE_OPERATIONS has `pandas`
**Solution**: Add both patterns OR track import aliases (MVP: just add both)

### Issue 3: F-String File Paths
**Problem**: `f"data_{date}.json"` won't match simple string extraction
**Solution**: For MVP, skip f-strings. Log as debug: "Skipped f-string pattern"

### Issue 4: Relative vs Absolute Paths
**Problem**: Mix of './data.csv', 'data.csv', '/home/data.csv'
**Solution**: Store as-is, let search handle normalization

### Issue 5: Non-File URLs
**Problem**: `requests.get('https://api.com/users')` isn't a file
**Solution**: Only create relations if URL/path ends with known extensions (.json, .csv, etc.)

## Quick Testing Commands

```bash
# 1. Create test file
cat > test-debug/test_extended_ops.py << 'EOF'
import pandas as pd
df = pd.read_csv('data.csv')
df.to_json('output.json')
EOF

# 2. Index with verbose output
claude-indexer -p test-debug -c watcher-test --verbose

# 3. Check logs for new relations
grep "import_type" test-debug/logs/watcher-test.log | grep -E "pandas|path_|requests"

# 4. Verify in Python
python3 -c "
from claude_indexer.storage.qdrant import QdrantStore
from claude_indexer.config import load_config
config = load_config()
store = QdrantStore(config)
# Search for pandas operations
results = store.scroll_collection('watcher-test', limit=100)
pandas_ops = [r for r in results if 'pandas' in str(r.payload.get('import_type', ''))]
print(f'Found {len(pandas_ops)} pandas operations')
"
```

---

**Note for Implementer**: This plan focuses on MVP functionality. Future enhancements could include variable tracking, complex expression evaluation, and cross-file constant resolution. For now, keep it simple and reliable.
