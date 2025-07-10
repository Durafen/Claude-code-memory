# AI Prompt: Comprehensive Relation Analysis & Debugging

## Role & Context
You are an expert code relationship analyzer tasked with systematically verifying relation extraction accuracy in a Tree-sitter-based parsing system. Use memory-first debugging and entity-specific analysis to identify missing relations and garbage extractions.

## Supported File Types & Extensions
The system supports **24+ file extensions** across multiple programming languages:

### **Programming Languages**
- **Python**: `.py`, `.pyi` (type stubs)
- **JavaScript/TypeScript**: `.js`, `.jsx`, `.ts`, `.tsx`, `.mjs`, `.cjs`
- **Web Technologies**: `.html`, `.htm`, `.css`, `.scss`, `.sass`
- **Data Formats**: `.json`, `.yaml`, `.yml`, `.xml`
- **Configuration**: `.ini`, `.cfg`, `.conf`, `.toml`
- **Documentation**: `.md`, `.markdown`, `.rst`, `.txt`
- **Data Files**: `.csv`, `.tsv`
- **Shell Scripts**: `.sh`, `.bash`, `.zsh`
- **Other**: `.dockerfile`, `.gitignore`, `.env`

### **Parser Architecture**
- **TreeSitter-based**: Python, JavaScript/TypeScript, HTML, CSS, JSON, YAML
- **Text-based**: Markdown, CSV, INI, Configuration files
- **Streaming Support**: Large JSON files (>50MB) with ijson
- **Progressive Disclosure**: Metadata chunks for fast search, implementation chunks on-demand

## Primary Objectives

### 1. **Memory-First Investigation**
```
- Search memory for patterns: "missing relations inner functions inheritance"
- Search memory for patterns: "garbage false positive Tree-sitter extraction" 
- Use existing debugging patterns from memory before starting analysis
- Store findings as debugging_pattern (30%) or implementation_pattern (25%)
```

### 2. **Entity-Specific Relation Verification**
```
Target 5-8 key functions/classes for focused analysis:
- Use read_graph(entity="FunctionName", mode="relationships") for laser-focused debugging
- Use get_implementation(entityName="Function", scope="logical") to reveal actual code
- Compare semantic_metadata.calls with relationship graph
- Focus on 10-20 targeted relations vs 300+ scattered ones
```

### 3. **Critical Missing Relations Detection**

#### **A. Inner Function/Method Relations**
```
✓ Check: Function → nested helper function calls
✓ Check: Method → private method calls within same class
✓ Check: Method → method calls on different files  and class
✓ Check: Recursive function calls (function calling itself)
✓ Example: _extract_file_operations → find_file_operations
```

#### **B. Class Inheritance Relations** 
```
✓ Check: Child class → Parent class inheritance
✓ Check: Multiple inheritance patterns
✓ Check: Abstract class implementations
✓ Example: HTMLParser(TreeSitterParser) should create HTMLParser → TreeSitterParser
```

#### **C. Cross-File Import Relations**
```
✓ Check: File → imported module/file relations
✓ Check: from X import Y patterns  
✓ Check: import X.Y.Z patterns
✓ Check: Relative imports (from .module import item)
✓ Check: Parent imports (from ..parent import item)
✓ Check: Sibling imports (from .sibling_module import Class)
✓ Example: html_parser.py imports base_parsers.py
✓ Example: from .entities import Entity, Relation
✓ Example: from ..indexer_logging import get_logger
✓ Example: from .observation_extractor import ObservationExtractor
```

#### **D. Factory/Utility Function Relations**
```
✓ Check: Class → Factory method calls
✓ Check: Utility function usage patterns
✓ Example: HTMLParser → RelationFactory.create_imports_relation()
```

#### **E. Composition Relations (Object Instantiation)**
```
✓ Check: Class → instantiated object relations
✓ Check: Constructor calls: MyClass() → MyClass.__init__
✓ Check: Dependency injection patterns
✓ Example: parser = HTMLParser() should create relation
```

#### **F. Exception Relations**
```
✓ Check: Function → raised exceptions (raise ValueError)
✓ Check: Function → caught exceptions (except TypeError)
✓ Check: Exception propagation chains
✓ Example: parse() → raises ParseError
```

#### **G. Decorator Relations**
```
✓ Check: Function → applied decorators (@property, @staticmethod)
✓ Check: Class → class decorators (@dataclass)
✓ Check: Decorator chains and nested decorators
✓ Example: @cached_property → function modification
```

#### **H. Variable/Attribute Relations**
```
✓ Check: Function → local variables
✓ Check: Class → instance attributes (self.attr)
✓ Check: Global variable usage patterns
✓ Example: self.config → class attribute access
```

#### **I. Control Flow Relations**
```
✓ Check: if/else branch dependencies
✓ Check: loop iteration patterns (for/while)
✓ Check: switch/case statement flows
✓ Check: conditional execution paths
✓ Example: if condition → branch_function()
```

#### **J. Data Flow Relations**
```
✓ Check: Variable assignment chains (a = b = c)
✓ Check: Return value usage (result = func())
✓ Check: Parameter passing chains
✓ Check: Value propagation through function calls
✓ Example: func1() → return value → func2(value)
```

#### **K. File Operations Relations**
```
✓ Check: Python file operations (open('file.txt'), json.load('config.json'))
✓ Check: Pandas operations (pd.read_csv('data.csv'), df.to_json('output.json'))
✓ Check: Pathlib operations (Path('file.txt').read_text())
✓ Check: JavaScript file operations (fs.readFile('file.txt'), require('./module'))
✓ Check: Configuration file access (configparser.read('config.ini'))
✓ Example: open('config.json') → config.json
✓ Example: pd.read_csv('data.csv') → data.csv
✓ Example: fs.readFile('file.txt') → file.txt
```

#### **L. Context Manager Relations (Python)**
```
✓ Check: With statement context managers (with open('file'))
✓ Check: Context manager protocols (__enter__, __exit__)
✓ Check: Custom context managers (contextlib.contextmanager)
✓ Check: Multiple context managers (with open('a'), open('b'))
✓ Example: with open('file.txt') as f → file.txt
✓ Example: with database.transaction() → database
```

#### **M. Async/Await Relations**
```
✓ Check: Python async functions (async def func())
✓ Check: Python await expressions (await something())
✓ Check: JavaScript async functions (async function func())
✓ Check: JavaScript await expressions (await promise)
✓ Check: Async context managers (async with)
✓ Example: async def process() → async function
✓ Example: await fetch_data() → fetch_data
```

#### **N. Method Chaining Relations**
```
✓ Check: Python method chains (obj.method1().method2())
✓ Check: JavaScript method chains (obj.method1().method2())
✓ Check: Promise chains (fetch().then().catch())
✓ Check: Fluent interface patterns (builder.add().build())
✓ Check: Pandas method chains (df.filter().groupby().sum())
✓ Example: obj.method1().method2() → method1 → method2
✓ Example: fetch().then().catch() → fetch → then → catch
```

#### **O. Lambda/Closure Relations**
```
✓ Check: Python lambda functions (lambda x: x+1)
✓ Check: JavaScript arrow functions ((x) => x+1)
✓ Check: Function closures and scope capture
✓ Check: Higher-order functions (map, filter, reduce)
✓ Check: Callback function patterns
✓ Example: lambda x: x+1 → closure relation
✓ Example: (x) => x+1 → arrow function relation
```

#### **P. Conditional Import Relations**
```
✓ Check: Python conditional imports (if condition: import module)
✓ Check: JavaScript dynamic imports (import('./module'))
✓ Check: Try/except imports (try: import optional_module)
✓ Check: Runtime imports (importlib.import_module)
✓ Check: Conditional require (if (condition) require('module'))
✓ Example: if DEBUG: import debug_tools → debug_tools
✓ Example: import('./module') → dynamic import to module
```

#### **Q. Destructuring Relations**
```
✓ Check: JavaScript object destructuring (const {a, b} = obj)
✓ Check: JavaScript array destructuring (const [x, y] = arr)
✓ Check: Python tuple unpacking (a, b = tuple)
✓ Check: Nested destructuring patterns
✓ Check: Default values in destructuring
✓ Example: const {name, age} = person → name, age from person
✓ Example: a, b = get_values() → a, b from get_values
```

#### **R. Template/String Interpolation Relations**
```
✓ Check: JavaScript template literals (`${variable}`)
✓ Check: Python f-strings (f"Hello {name}")
✓ Check: Python string formatting ("Hello {}".format(name))
✓ Check: Variable references in strings
✓ Check: Expression evaluation in templates
✓ Example: `Hello ${name}` → name variable reference
✓ Example: f"Value: {value}" → value variable reference
```

### 4. **Garbage Relation Detection**

#### **A. String Literal False Positives**
```
❌ Generic words extracted as functions: files, blocks, content, arrays
❌ Pandas method names in strings: to_csv, to_json, read_csv
❌ Configuration keys: limit, streaming, title, pairs
```

#### **B. Built-in Method False Positives**
```
❌ Built-in methods: len, str, int, list, append, strip, upper, print
❌ Reserved keywords: class, def, if, while
❌ Operators: or, and, not, ==, !=, <, >
❌ Generic attributes: type, name, value, data
```

### 5. **Systematic Verification Methodology**

#### **Phase 1: Memory Search**
```python
# Search existing patterns
search_similar("relation missing inheritance imports")
search_similar("garbage false positive extraction") 

# Use entity-specific debugging from memory
read_graph(entity="TargetFunction", mode="smart")
```

#### **Phase 2: Code vs Relations Comparison**
```python
# Get actual implementation 
get_implementation("HTMLParser", scope="logical")

# Compare with extracted relations
read_graph(entity="HTMLParser", mode="relationships")

# Identify discrepancies
```

#### **Phase 3: Pattern Classification**
```
For each missing/garbage relation:
1. Categorize: inheritance/import/inner-function/garbage
2. Identify root cause: Tree-sitter parsing, semantic filtering, extraction logic
3. Document pattern for future prevention
```

### 6. **Output Format**

```markdown
## Missing Relations Analysis ❌

### Inheritance Relations
- Class → Parent: [List missing inheritance relations]
- Root Cause: [Why Tree-sitter missed these]

### Import Relations  
- File → Import: [List missing import relations]
- Root Cause: [Import statement parsing issues]

### Inner Function Relations
- Function → Helper: [List missing inner calls]  
- Root Cause: [Nested function extraction issues]

### Composition Relations
- Class → Instantiation: [List missing object creation relations]
- Root Cause: [Constructor call parsing issues]

### Exception Relations
- Function → Exceptions: [List missing exception handling relations]
- Root Cause: [Exception statement parsing gaps]

### Decorator Relations
- Function → Decorators: [List missing decorator relations]
- Root Cause: [Decorator extraction issues]

### Variable/Attribute Relations
- Function → Variables: [List missing variable access relations]
- Root Cause: [Variable assignment parsing gaps]

### Control Flow Relations
- Function → Branches: [List missing conditional flow relations]
- Root Cause: [Control flow statement parsing issues]

### Data Flow Relations
- Function → Data Chain: [List missing data propagation relations]
- Root Cause: [Value flow tracking gaps]

## Garbage Relations Analysis 🗑️

### String Literal Extractions
- False Functions: [List garbage function names]
- Pattern: [Common extraction pattern causing issue]

### Operator Extractions
- False Operators: [List operators treated as functions]
- Pattern: [Operator parsing pattern issue]

## Working Relations Analysis ✅

### Cross-File Function Calls
- [List confirmed working cross-file relations]

### Method Calls Within Classes  
- [List confirmed working intra-class relations]

## Root Cause Summary
- [Primary issues with Tree-sitter extraction]
- [Semantic filtering gaps]
- [Recommended solutions]

## Memory Storage
- Store as debugging_pattern: [Critical findings for future reference]
- Store as implementation_pattern: [Working solutions and patterns]
```

### 7. **Success Criteria**

✅ **Complete Coverage**: All 10 relation types verified (inheritance, imports, inner functions, cross-file, composition, exceptions, decorators, variables, control flow, data flow)  
✅ **Pattern Recognition**: Garbage relation patterns identified and categorized  
✅ **Memory Integration**: Findings stored for future debugging sessions  
✅ **Actionable Results**: Clear root causes and solutions provided  
✅ **Entity-Focused**: Used laser-focused entity analysis vs information overload

---

**Execute this analysis systematically, use memory-first approach, and provide comprehensive findings for improving relation extraction accuracy.**