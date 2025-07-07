# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Claude Code Memory Solution

## Architecture Overview

This is a **dual-component semantic code memory system** for Claude Code:

**Components:**
- **Python Indexer** (`claude_indexer/`) - Universal AST parsing and vector indexing
- **Node.js MCP Server** (`mcp-qdrant-memory/`) - Memory retrieval interface for Claude
- **Qdrant Vector Database** - Semantic search and storage backend

**Core Architecture:**
```
Claude Code ◄──► MCP Server ◄──► Qdrant Database
                      ▲
              Python Indexer
                (Tree-sitter + Jedi)
```

## Essential Commands

### Development Setup
```bash
# Environment setup
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Install global wrapper
./install.sh

# Start Qdrant database
docker run -p 6333:6333 -v $(pwd)/qdrant_storage:/qdrant/storage qdrant/qdrant
```

### Build and Quality
```bash
# Code formatting and linting
black .
isort .
flake8 .
mypy claude_indexer/

# Testing
pytest                     # All tests
pytest tests/unit/         # Unit tests only
pytest tests/integration/  # Integration tests
pytest --cov=claude_indexer --cov-report=html  # With coverage
```

### Indexing and Memory Operations
```bash
# Index project (auto-detects incremental mode)
claude-indexer -p /path/to/project -c collection-name

# Real-time file watching
claude-indexer watch start -p /path -c collection-name

# Search semantic code
claude-indexer search "query" -p /path -c collection-name

# Service management for multiple projects
claude-indexer service start
claude-indexer service add-project /path project-name
```

### MCP Server Configuration
```bash
# Automatic MCP setup (reads settings.txt)
claude-indexer add-mcp -c collection-name
```

**⚠️ Project Memory:** Use `mcp__claude-memory-memory__` prefix for all memory operations on this project.

**🧪 Testing:** Use `parser-test-memory` MCP for isolated testing without contaminating production collections.

**🔑 API Configuration:** OpenAI and Voyage AI keys configured in `settings.txt` for cleanup scoring and embeddings. Contains active API keys for GPT-4.1-mini scoring and Voyage AI embedding generation.

## Key Architecture Components

### Core Indexing Engine
- **Entry Point**: `claude_indexer/cli_full.py` (Click-based CLI)
- **Main Logic**: `claude_indexer/main.py` and `CoreIndexer` (`indexer.py`)
- **Global Command**: `claude-indexer` (installed via `./install.sh`)

### Multi-Language Analysis
- **Parser Registry**: `claude_indexer/analysis/parser.py` (Tree-sitter + Jedi)
- **Supported Languages**: Python, JavaScript/TypeScript, JSON, YAML, HTML, CSS, Markdown
- **Language Parsers**: Individual parsers in `claude_indexer/analysis/` (e.g., `python_parser.py`, `javascript_parser.py`)

### Storage and Embeddings
- **Storage**: `claude_indexer/storage/qdrant.py` (Direct Qdrant integration)
- **Embeddings**: `claude_indexer/embeddings/` (OpenAI + Voyage AI support)
- **Configuration**: `claude_indexer/config/` (Hierarchical project settings)

### Progressive Disclosure System
- **Metadata Chunks**: Fast entity overviews (90% speed boost)
- **Implementation Chunks**: Detailed code content on-demand
- **Entity-Specific Filtering**: Focus on specific components vs. entire project graphs

## Configuration
Use `§m` to search memory for detailed configuration patterns, file organization, and advanced command usage.

## Memory Integration

### Enhanced 9-Category System for Manual Entries

**Research-backed categorization with semantic content analysis:**

- **`debugging_pattern` (30% target)**: SOLUTIONS and resolution patterns for errors (not the bugs themselves)
- **`implementation_pattern` (25% target)**: Coding solutions, algorithms, best practices  
- **`integration_pattern` (15% target)**: APIs, databases, authentication, pipelines
- **`configuration_pattern` (12% target)**: Environment setup, deployment, CI/CD
- **`architecture_pattern` (10% target)**: System design, component structure
- **`performance_pattern` (8% target)**: Optimization, caching, bottlenecks
- **`knowledge_insight`**: Research findings, lessons learned, methodology
- **`active_issue`**: Current bugs/problems requiring attention (delete when fixed)
- **`ideas`**: Project ideas, feature suggestions, future enhancements, brainstorming

## Memory Storage Rules
***Don't store just info about bugs, but store about solutions, insights about how the code works***

When categorizing memories:
- **debugging_pattern**: Store SOLUTIONS and resolution patterns, not the bugs themselves  
- **implementation_pattern**: Working code solutions and techniques
- Focus on "how to fix" rather than "what's broken"
- Only store after issues are resolved with working solutions

**Classification Approach**: Analyze content semantics, not format. Identify 3 strongest indicators, then categorize based on actual problem domain rather than documentation style.

### 🎯 Unified entityTypes Filtering (NEW)

**Single parameter supports both entity types and chunk types with OR logic:**

**Entity Types**: `class`, `function`, `documentation`, `text_chunk`
**Chunk Types**: `metadata`, `implementation`

**Usage Examples:**
```python
# Filter by entity types only
search_similar("pattern", entityTypes=["function", "class"])

# Filter by chunk types only  
search_similar("pattern", entityTypes=["metadata"])        # Fast search
search_similar("pattern", entityTypes=["implementation"])  # Detailed code

# Mixed filtering (OR logic)
search_similar("pattern", entityTypes=["function", "metadata", "implementation"])

# All types (no filtering)
search_similar("pattern")  # Returns all entity and chunk types
```

**Benefits:**
- **Single Parameter**: No need for separate `chunkTypes` parameter
- **OR Logic**: Mixed arrays return results matching ANY specified type
- **Backward Compatible**: Existing calls work unchanged
- **Performance**: Filter at database level for optimal speed


## Virtual Environment Usage

**Always activate venv before testing:**
```bash
source .venv/bin/activate  # Required before pytest
pytest                     # Now runs with correct dependencies
```

## Direct Qdrant Access

**Bypass MCP for database operations:**
```bash
python utils/qdrant_stats.py              # Collection health
python utils/find_missing_files.py        # File sync debug
python utils/manual_memory_backup.py      # Backup/restore
```

**Test Qdrant connection:**
```bash
# Test using config loader (loads settings.txt properly)
python3 -c "from claude_indexer.config.config_loader import ConfigLoader; from qdrant_client import QdrantClient; config=ConfigLoader().load(); client=QdrantClient(url=config.qdrant_url, api_key=config.qdrant_api_key); print('Collections:', [c.name for c in client.get_collections().collections])"
```

## Debug Testing Protocol

**Testing Database - watcher-test Collection:**
```bash
# Use dedicated test collection for all debugging (never use production DB)
claude-indexer index -p /path/to/test-files -c watcher-test --verbose
```

**Testing Best Practices:**
- Always use separate test collections (watcher-test, debug-test) for debugging
- Use 1-2 Python files only for cleaner debug output  
- Never contaminate production memory collections during testing
- Test indexing, relations, file processing, incremental updates, parser functionality
- MCP server already configured for watcher-test collection

## Manual Memory Backup & Restore

Protect your valuable manual memories (analysis notes, insights, patterns):

```bash
# Backup all manual entries from a collection
python utils/manual_memory_backup.py backup -c collection-name

# Generate MCP restore commands for manual entries
python utils/manual_memory_backup.py restore -f manual_entries_backup_collection-name.json

# Execute restore automatically via MCP (no manual steps)
python utils/manual_memory_backup.py restore -f manual_entries_backup_collection-name.json --execute

# Dry run to see what would be restored
python utils/manual_memory_backup.py restore -f backup.json --dry-run
```


## 🎯 Entity-Specific Graph Filtering (NEW in v2.7)

**Focus on specific entities instead of browsing entire project graphs:**

```python
# Focus on specific function's dependencies and usage
read_graph(entity="AuthService", mode="smart")
# Returns: AI summary of AuthService's connections, dependencies, usage

# See all relationships for a specific entity  
read_graph(entity="process_login", mode="relationships") 
# Returns: Only relations involving process_login (incoming/outgoing)

# Get entities connected to a specific component
read_graph(entity="validate_token", mode="entities")
# Returns: All entities that connect to validate_token

# Raw data for a specific entity's network
read_graph(entity="DatabaseManager", mode="raw")
# Returns: Complete entities + relations for DatabaseManager's network
```

## 🔧 Enhanced Debugging Workflow with Unified Filtering (v2.8)

**Modern Memory-First Debugging Approach - Leveraging unified entityTypes for 90% faster problem resolution:**

### Phase 1: Smart Error Discovery
```python
# 🎯 Fast metadata scan for initial triage (90% speed boost)
search_similar("error pattern", entityTypes=["metadata"])

# 🔍 Find similar debugging patterns from past solutions
search_similar("authentication error", entityTypes=["debugging_pattern", "function"])

# 🧩 Mixed search for comprehensive context
search_similar("validation error", entityTypes=["function", "metadata", "implementation"])
```

### Phase 2: Targeted Problem Analysis
```python
# 1. Focus on specific problematic function
read_graph(entity="validate_token", mode="smart")         # AI summary with stats
get_implementation("validate_token", scope="logical")    # Function + helpers
get_implementation("validate_token", scope="dependencies") # Full dependency chain

# 2. Trace error propagation paths
read_graph(entity="handle_request", mode="relationships")
# Shows: incoming calls, outgoing calls, error flow

# 3. Understand class/module architecture
read_graph(entity="AuthService", mode="entities")
# Shows: all connected components
```

### Phase 3: Solution Implementation
```python
# 🎯 Find existing patterns before implementing
search_similar("input validation", entityTypes=["implementation_pattern", "function"])

# 📚 Check documentation for API usage
search_similar("authentication api", entityTypes=["documentation"])

# 🔧 Deep dive into implementation details when needed
search_similar("complex validation logic", entityTypes=["implementation"])
```

### Best Practices for Memory-First Debugging:

1. **Start Fast**: Always begin with `entityTypes=["metadata"]` for quick overview
2. **Use Patterns**: Search `debugging_pattern` category for similar past issues
3. **Progressive Depth**: metadata → function/class → implementation
4. **Store Solutions**: Document fixes as `implementation_pattern` for future reference
5. **Leverage OR Logic**: Mix types like `["function", "metadata"]` for flexible search

### Performance Tips:
- **Metadata-first**: 3.99ms vs traditional full search
- **Targeted Filtering**: Reduce noise by 85% with specific entityTypes
- **Entity-Specific**: 10-20 relevant items vs 300+ unfiltered results
- **Smart Caching**: Frequently accessed patterns cached automatically

**Performance Benefits:**
- **10-20 focused relations** instead of 300+ scattered ones
- **Smart entity summaries** with key statistics and relationship breakdown  
- **Laser-focused debugging** without information overload
- **Backward compatible** - general graph still works without entity parameter

## 🚀 Advanced Implementation Workflow with Unified Filtering

**Efficient Code Implementation Using Memory-First Approach:**

### Phase 1: Pre-Implementation Research
```python
# 🔍 Check if similar functionality exists (avoid duplication)
search_similar("user authentication", entityTypes=["function", "class", "implementation_pattern"])

# 📚 Find relevant documentation and guides
search_similar("auth library usage", entityTypes=["documentation"])

# 🎯 Look for existing patterns and best practices
search_similar("auth pattern", entityTypes=["implementation_pattern", "architecture_pattern"])
```

### Phase 2: Architecture Understanding
```python
# Understand module dependencies before adding new code
read_graph(entity="AuthModule", mode="smart")           # Overview with stats
read_graph(entity="AuthModule", mode="relationships")   # See all connections

# Check existing implementations for consistency
get_implementation("similar_function", scope="logical")  # Understand code style
```

### Phase 3: Smart Implementation
1. **Always search first**: Use memory to find existing solutions
2. **Follow patterns**: Maintain consistency with existing architecture
3. **Progressive disclosure**: Start with metadata, dive deeper as needed
4. **Document patterns**: Store successful implementations for future use

## Debug Commands

**CLI Debugging:**
```bash
claude-indexer -p /path -c collection --verbose    # Detailed error messages
claude-indexer service status --verbose            # Service debugging  
claude-indexer search "query" -p /path -c test     # Test search functionality
```

**Log Analysis:**
```bash
tail -f logs/collection-name.log                   # Real-time monitoring
tail -f ~/.claude-indexer/logs/service.log        # Service logs
```

**Collection Health:**
```bash
python utils/qdrant_stats.py                      # Collection statistics
python utils/find_missing_files.py                # File sync debugging
```

## Basic Troubleshooting

**Qdrant Connection Failed:**
- Ensure Qdrant is running on port 6333
- Check firewall settings  
- Verify API key matches
- Use `search_similar("qdrant connection error", entityTypes=["debugging_pattern"])` for solutions

**MCP Server Not Loading:**
- Restart Claude Code after config changes
- Check absolute paths in MCP configuration
- Search memory: `search_similar("mcp configuration", entityTypes=["configuration_pattern"])`

**No Entities Created:**
- Verify target directory contains supported files (Python, JavaScript, TypeScript, JSON, HTML, CSS, YAML, etc.)
- Use `--verbose` flag for detailed error messages
- Check memory: `search_similar("indexing error no entities", entityTypes=["debugging_pattern", "metadata"])`

## Additional Information

**Use `§m` to search memory for:**
- Multi-language parser specifications
- Configuration system details  
- Performance optimization patterns
- Version history and migration guides
- Advanced debugging workflows