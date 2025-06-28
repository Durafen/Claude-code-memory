# Chat History Summarization Implementation Plan

## Overview

This document outlines the implementation plan for adding chat history summarization capabilities to the Claude Code
Memory Indexer. The feature will parse Claude Code JSONL files, summarize conversations using OpenAI, categorize them
using the existing 7-category system, and store them in Qdrant for semantic search.

### Key Features

1. **Smart Deduplication**: Updates existing manual entries instead of creating duplicates
2. **Project-Specific Indexing**: Only indexes conversations for the current project
3. **Cost-Optimized**: Uses GPT-4o-mini ($0.15/1M tokens) with single API call
4. **Manual Memory Preservation**: Respects and enriches existing manual entries
5. **Unified Search**: Chat summaries searchable alongside code entities

**Important**: Chat indexing is a **separate operation** from code indexing:

- `claude-indexer -p /path -c name` - indexes code files only
- `claude-indexer chat index -p /path -c name` - indexes chat history only
- Both store in the same collection but run independently

## Architecture Integration

### 1. Component Design

The implementation follows the existing modular architecture patterns:

```
claude_indexer/
├── chat/                          # New module for chat-specific logic
│   ├── __init__.py
│   ├── parser.py                  # JSONL parser for Claude Code files  
│   ├── summarizer.py              # Chat summarization with OpenAI
│   └── monitor.py                 # File monitoring for chat updates
├── analysis/
│   └── entities.py                # Extend with CHAT_HISTORY entity type
└── cli_full.py                    # Add chat-related commands
```

### 2. Core Classes

#### ChatParser (inherits from base patterns)

```python
class ChatParser:
    """Parse Claude Code JSONL files."""

    def get_project_chat_directory(self, project_path: Path) -> Path

        def parse_jsonl(self, file_path: Path) -> List[ChatConversation]

        def extract_metadata(self, conversation: Dict) -> ChatMetadata

        def get_last_activity(self, file_path: Path) -> datetime
```

#### ChatSummarizer

```python
class ChatSummarizer:
    """Summarize conversations using OpenAI."""
    
    def summarize_conversation(self, messages: List[Dict]) -> SummaryResult
    def categorize_content(self, summary: str) -> EntityType
    def extract_key_insights(self, messages: List[Dict]) -> List[str]
```

#### ChatMonitor (extends existing Watcher patterns)

```python
class ChatMonitor:
    """Monitor chat files for changes."""
    
    def watch_project_chats(self, project_path: Path) -> None
    def check_inactive_conversations(self, threshold_hours: int) -> List[Path]
    def trigger_summarization(self, file_path: Path) -> None
```

## Implementation Phases

### Phase 0: Debug & Validation (Week 0.5)

**Milestone 0.1: Debug-First Implementation**

- Build JSONL parser without storage ✅ COMPLETED
- Test GPT-4o-mini summarization prompts
- Print results for manual validation
- Iterate on prompt engineering
- NO vector storage until validated

**Progress Update**:

- Created `claude_indexer/chat/` module structure
- Implemented `ChatParser` with full JSONL parsing capabilities
- Added data classes: `ChatMessage`, `ChatMetadata`, `ChatConversation`
- Implemented project path encoding/decoding logic
- Added inactive conversation detection

### Phase 1: Core Infrastructure (Week 1)

**Milestone 1.1: Extend Entity System**

- Add `CHAT_HISTORY` to EntityType enum (line 23 in entities.py)
- Entities marked with `entity_type: "chat_history"` in Qdrant
- Keep chat summaries separate from code entities

**Milestone 1.2: Chat Parser Implementation**

- Implement project path → chat directory mapping
- Parse JSONL files from encoded project directory only
- Extract conversation metadata (timestamps, project context)
- Handle malformed/incomplete JSONL gracefully
- Add comprehensive error handling

**Milestone 1.3: Unit Tests for Parser**

- Test valid JSONL parsing
- Test error handling for malformed data
- Test metadata extraction accuracy
- Test memory efficiency with large files

### Phase 2: Summarization Pipeline (Week 2)

**Milestone 2.1: OpenAI Integration**

- Use GPT-4o-mini model ($3/1M input tokens)
- Single API call for summary + categorization
- Reuse existing OpenAI embedder configuration
- Handle rate limiting and token limits
- Implement retry logic with exponential backoff

**Milestone 2.2: Categorization Logic**

- Map summaries to existing 7-category system:
    - `debugging_pattern`: Troubleshooting discussions
    - `implementation_pattern`: Code implementation conversations
    - `integration_pattern`: API/service integration chats
    - `configuration_pattern`: Setup/config discussions
    - `architecture_pattern`: Design conversations
    - `performance_pattern`: Optimization discussions
    - `knowledge_insight`: General learnings

**Milestone 2.3: Integration Tests**

- Test end-to-end summarization pipeline
- Test categorization accuracy
- Test OpenAI API error handling
- Test token usage optimization

### Phase 3: Storage Integration (Week 3)

**Milestone 3.1: Qdrant Storage**

- Reuse existing VectorStore patterns
- Store chat summaries as VectorPoints
- Include metadata: project, timestamp, category
- Enable semantic search across chat history

**Milestone 3.2: Search Enhancement**

- Extend search to include chat history
- Add filters for time ranges
- Support cross-reference with code entities
- Implement relevance scoring

**Milestone 3.3: E2E Tests**

- Test complete pipeline from JSONL to search
- Test deduplication of re-indexed conversations
- Test incremental updates
- Performance testing with large chat histories

### Phase 4: Automation & CLI (Week 4)

**Milestone 4.1: Time-based Triggers**

- Implement inactivity detection (default: 24 hours)
- Add configurable thresholds
- Integrate with existing service daemon
- Handle multiple project contexts

**Milestone 4.2: CLI Commands**

```bash
# Manual chat indexing
claude-indexer chat index --project /path --collection name

# Enable chat monitoring
claude-indexer chat watch --project /path --threshold 24

# Search chat history
claude-indexer chat search "debugging memory leak" --project /path

# View chat statistics
claude-indexer chat stats --project /path
```

**Milestone 4.3: Service Integration**

- Add chat monitoring to background service
- Configure per-project chat settings
- Implement resource-efficient polling
- Add chat-specific logging

## Test Strategy

### Unit Tests (follow existing patterns)

```python
tests/unit/
├── test_chat_parser.py      # JSONL parsing logic
├── test_chat_summarizer.py  # Summarization & categorization
└── test_chat_monitor.py     # File monitoring logic
```

### Integration Tests

```python
tests/integration/
├── test_chat_pipeline.py    # Parser → Summarizer → Storage
└── test_chat_search.py      # Search functionality
```

### E2E Tests

```python
tests/e2e/
└── test_chat_e2e.py        # Complete workflow testing
```

## Testing Best Practices

### Mock Strategies for OpenAI API

#### 1. DummyEmbedder Pattern (Fast & Deterministic)

```python
class DummyEmbedder:
    """Fast, deterministic embedder for testing - avoids API calls."""
    
    def __init__(self, dimension: int = 1536):
        self.dimension = dimension
    
    def embed_text(self, text: str):
        """Generate deterministic embedding based on text hash."""
        from claude_indexer.embeddings.base import EmbeddingResult
        
        # Create unique but deterministic embedding
        seed = hash(text) % 10000
        np.random.seed(seed)
        embedding = np.random.rand(self.dimension).astype(np.float32).tolist()
        
        return EmbeddingResult(
            text=text,
            embedding=embedding,
            model="dummy",
            token_count=len(text.split()),  # Approximate token count
            processing_time=0.001
        )
```

#### 2. Mock OpenAI Response Pattern

```python
@pytest.fixture()
def mock_openai_embedder(monkeypatch):
    """Mock OpenAI API calls with controlled responses."""
    mock_responses = {
        "test query": [0.1] * 1536,  # Known test embedding
        "authentication": [0.2] * 1536,
        "database connection": [0.3] * 1536
    }
    
    def mock_create(**kwargs):
        text = kwargs.get('input', '')
        embedding = mock_responses.get(text, [0.5] * 1536)
        return {
            "data": [{"embedding": embedding}],
            "usage": {"total_tokens": len(text.split()) * 2}
        }
    
    monkeypatch.setattr("openai.embeddings.create", mock_create)
```

### Test Data Generation for JSONL Files

#### 1. Chat Conversation Generator

```python
def generate_test_chat_jsonl(path: Path, num_conversations: int = 5):
    """Generate realistic test JSONL chat data."""
    conversations = []
    
    for i in range(num_conversations):
        base_time = datetime.now() - timedelta(days=i)
        messages = [
            {
                "id": f"msg-{i}-1",
                "timestamp": base_time.isoformat(),
                "role": "user",
                "content": f"How do I implement {['authentication', 'caching', 'routing'][i % 3]}?"
            },
            {
                "id": f"msg-{i}-2", 
                "timestamp": (base_time + timedelta(minutes=1)).isoformat(),
                "role": "assistant",
                "content": f"Here's how to implement that feature: [detailed response]..."
            }
        ]
        
        # Write each message as separate JSONL entry
        with open(path, 'a') as f:
            for msg in messages:
                f.write(json.dumps(msg) + '\n')
    
    return conversations
```

#### 2. Edge Case Test Data

```python
@pytest.fixture
def jsonl_edge_cases(tmp_path):
    """Create JSONL files with edge cases."""
    edge_cases = {
        "empty.jsonl": "",
        "malformed.jsonl": '{"broken": json}\n{"valid": "entry"}',
        "huge_message.jsonl": json.dumps({
            "id": "huge-1",
            "content": "x" * 100000  # Test token limits
        }),
        "unicode.jsonl": json.dumps({
            "id": "unicode-1", 
            "content": "Test 中文 émojis 🚀 special chars"
        })
    }
    
    for filename, content in edge_cases.items():
        (tmp_path / filename).write_text(content)
    
    return tmp_path
```

### Integration Test Patterns

#### 1. End-to-End Pipeline Pattern

```python
class TestChatIndexingPipeline:
    """Integration test for complete chat indexing flow."""
    
    @pytest.fixture
    def test_pipeline(self, qdrant_store, dummy_embedder, temp_repo):
        """Setup complete test pipeline."""
        return {
            'parser': ChatParser(),
            'summarizer': ChatSummarizer(dummy_embedder),
            'store': ChatVectorStore(qdrant_store.client)
        }
    
    def test_full_indexing_flow(self, test_pipeline, test_chat_data):
        """Test Parser → Summarizer → Storage flow."""
        # 1. Parse chat files
        conversations = test_pipeline['parser'].parse_directory(test_chat_data)
        assert len(conversations) > 0
        
        # 2. Generate summaries
        summaries = []
        for conv in conversations:
            summary = test_pipeline['summarizer'].summarize(conv)
            summaries.append(summary)
        
        # 3. Store in vector DB
        stored_ids = []
        for summary in summaries:
            point_id = test_pipeline['store'].add_summary(summary)
            stored_ids.append(point_id)
        
        # 4. Verify searchability
        results = test_pipeline['store'].search("authentication")
        assert len(results) > 0
        
        # 5. Verify metadata integrity
        for result in results:
            assert 'category' in result.payload
            assert 'timestamp' in result.payload
```

#### 2. Incremental Update Pattern

```python
def test_incremental_chat_updates(indexer, test_repo, qdrant_store):
    """Test incremental indexing detects only changed files."""
    # Initial indexing
    result1 = indexer.index_chat_history(test_repo, "test-collection")
    initial_count = result1.conversations_processed
    
    # Modify one file
    chat_file = test_repo / "chats" / "day1.jsonl"
    with open(chat_file, 'a') as f:
        f.write(json.dumps({"id": "new-msg", "content": "New message"}) + '\n')
    
    # Incremental update
    result2 = indexer.index_chat_history(test_repo, "test-collection")
    assert result2.files_processed == 1  # Only modified file
    assert result2.conversations_processed < initial_count  # Incremental
```

### Performance Testing Approaches

#### 1. Benchmark Decorator Pattern

```python
def benchmark(func):
    """Decorator to measure and assert performance."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        elapsed = time.time() - start
        
        # Performance assertions based on function
        if 'parse' in func.__name__:
            assert elapsed < 0.1, f"Parsing too slow: {elapsed:.3f}s"
        elif 'embed' in func.__name__:
            assert elapsed < 0.5, f"Embedding too slow: {elapsed:.3f}s"
        
        return result
    return wrapper

@benchmark
def test_parse_large_jsonl(large_chat_file):
    """Ensure parsing scales well."""
    parser = ChatParser()
    conversations = parser.parse_file(large_chat_file)
    assert len(conversations) > 100
```

#### 2. Memory Usage Testing

```python
def test_memory_efficient_parsing(huge_jsonl_file):
    """Verify streaming parser doesn't load entire file."""
    import psutil
    import os
    
    process = psutil.Process(os.getpid())
    initial_memory = process.memory_info().rss / 1024 / 1024  # MB
    
    parser = ChatParser()
    # Should use streaming, not load entire file
    for conversation in parser.parse_jsonl_stream(huge_jsonl_file):
        pass  # Process in chunks
    
    final_memory = process.memory_info().rss / 1024 / 1024
    memory_increase = final_memory - initial_memory
    
    # Should not load entire file into memory
    file_size_mb = huge_jsonl_file.stat().st_size / 1024 / 1024
    assert memory_increase < file_size_mb * 0.1  # Max 10% of file size
```

### Coverage Targets

#### 1. Comprehensive Test Matrix

```python
# conftest.py additions for chat testing
CHAT_TEST_SCENARIOS = [
    # (description, chat_files, expected_categories)
    ("coding_session", ["implement_auth.jsonl"], ["implementation_pattern"]),
    ("debug_session", ["fix_memory_leak.jsonl"], ["debugging_pattern"]), 
    ("research_session", ["explore_apis.jsonl"], ["knowledge_insight"]),
    ("mixed_session", ["various_topics.jsonl"], ["multiple_categories"])
]

@pytest.fixture(params=CHAT_TEST_SCENARIOS)
def chat_scenario(request, tmp_path):
    """Generate test data for each scenario."""
    desc, files, categories = request.param
    # Generate appropriate test data...
    return {"description": desc, "files": files, "categories": categories}
```

#### 2. Coverage Requirements

```yaml
# .coverage_requirements.yml
chat_module_coverage:
  overall: 90%
  critical_paths:
    - module: claude_indexer.chat.parser
      min_coverage: 95%  # Critical parsing logic
    - module: claude_indexer.chat.summarizer  
      min_coverage: 85%  # AI responses can vary
    - module: claude_indexer.chat.categorizer
      min_coverage: 90%  # Important for search
    
  integration_tests:
    - test_chat_pipeline: "Must test all 7 categories"
    - test_incremental: "Must verify state persistence"
    - test_error_handling: "Must handle malformed JSONL"
```

### Cost Tracking Verification

#### 1. Token Usage Tracking

```python
class TestTokenUsageTracking:
    """Verify accurate token counting and cost estimation."""
    
    def test_token_counting_accuracy(self, chat_summarizer):
        """Ensure token counts match OpenAI's tokenizer."""
        test_text = "This is a test message for token counting."
        
        # Our count
        our_count = chat_summarizer.estimate_tokens(test_text)
        
        # OpenAI's tiktoken count
        import tiktoken
        encoder = tiktoken.encoding_for_model("gpt-3.5-turbo")
        actual_count = len(encoder.encode(test_text))
        
        # Should be within 5% accuracy
        assert abs(our_count - actual_count) / actual_count < 0.05
    
    def test_cost_tracking_aggregation(self, indexer):
        """Verify costs are tracked and reported correctly."""
        result = indexer.index_with_cost_tracking(test_repo)
        
        assert result.total_cost > 0
        assert result.token_usage['prompt_tokens'] > 0
        assert result.token_usage['completion_tokens'] > 0
        
        # Verify cost calculation
        expected_cost = (
            result.token_usage['prompt_tokens'] * 0.001 / 1000 +
            result.token_usage['completion_tokens'] * 0.002 / 1000
        )
        assert abs(result.total_cost - expected_cost) < 0.0001
```

#### 2. Mock Cost Tracking

```python
@pytest.fixture
def mock_cost_tracker():
    """Mock for testing cost tracking without API calls."""
    class MockCostTracker:
        def __init__(self):
            self.costs = []
            
        def track_api_call(self, tokens_in, tokens_out, model="gpt-3.5-turbo"):
            cost = (tokens_in * 0.001 + tokens_out * 0.002) / 1000
            self.costs.append({
                "tokens_in": tokens_in,
                "tokens_out": tokens_out, 
                "model": model,
                "cost": cost
            })
            return cost
            
        def get_total_cost(self):
            return sum(c['cost'] for c in self.costs)
    
    return MockCostTracker()
```

### Qdrant Test Collection Setup

#### 1. Isolated Test Collections

```python
@pytest.fixture
def chat_test_collection(qdrant_client):
    """Create isolated collection for chat tests."""
    collection_name = f"test_chat_{int(time.time())}"
    
    # Create with chat-specific configuration
    qdrant_client.create_collection(
        collection_name=collection_name,
        vectors_config={
            "summary": VectorParams(size=1536, distance=Distance.COSINE),
            "keywords": VectorParams(size=768, distance=Distance.COSINE)
        },
        # Optimized for test performance
        optimizers_config={"indexing_threshold": 100}
    )
    
    yield collection_name
    
    # Cleanup
    qdrant_client.delete_collection(collection_name)
```

#### 2. Test Data Seeding

```python
def seed_chat_test_data(qdrant_client, collection_name):
    """Pre-populate collection with known test data."""
    test_summaries = [
        {
            "id": "test-chat-1",
            "vector": [0.1] * 1536,
            "payload": {
                "category": "debugging_pattern",
                "summary": "Fixed memory leak in vector processing",
                "timestamp": "2024-01-15T10:00:00Z",
                "keywords": ["memory", "leak", "debugging"]
            }
        },
        # Add more test data...
    ]
    
    qdrant_client.upsert(
        collection_name=collection_name,
        points=test_summaries
    )
```

### End-to-End Test Scenarios

#### 1. Complete User Journey Test

```python
def test_complete_chat_indexing_journey(cli_runner, temp_claude_projects):
    """Test realistic user workflow from setup to search."""
    
    # 1. User sets up configuration
    result = cli_runner.invoke(["config", "set", "claude_projects_path", str(temp_claude_projects)])
    assert result.exit_code == 0
    
    # 2. User indexes their chat history
    result = cli_runner.invoke(["index-chats", "-c", "test-collection"])
    assert "Indexed" in result.output
    assert "conversations" in result.output
    
    # 3. User searches for past solutions
    result = cli_runner.invoke(["search", "memory leak", "--chat", "-c", "test-collection"])
    assert "debugging_pattern" in result.output
    assert result.exit_code == 0
    
    # 4. User monitors for new chats
    result = cli_runner.invoke(["chat-monitor", "start", "-c", "test-collection"])
    assert "Monitoring started" in result.output
```

#### 2. Error Recovery Test

```python
def test_graceful_error_handling(indexer, corrupted_chat_files):
    """Ensure system continues despite individual file errors."""
    
    results = indexer.index_directory(corrupted_chat_files)
    
    # Should process valid files despite errors
    assert results.successful_files > 0
    assert len(results.errors) > 0
    
    # Errors should be informative
    for error in results.errors:
        assert "file" in error.lower()
        assert any(term in error.lower() for term in ["parse", "invalid", "corrupt"])
    
    # Should not crash or lose partial results
    assert results.total_summaries > 0
```

These testing best practices ensure comprehensive coverage of the chat history feature while maintaining the high
quality standards established by the existing 158-test suite. The patterns focus on isolation, performance, and
realistic scenarios while avoiding actual API calls during testing.

## Configuration

Extend existing `settings.txt`:

```ini
[chat]
claude_projects_path = ~/.claude/projects
inactivity_threshold_hours = 24
max_summary_tokens = 500
batch_size = 10
enable_auto_summarization = true
```

## Key Design Decisions

### 1. Reuse Over Duplication

- Leverage existing VectorStore, Embedder, and Config systems
- Extend rather than replace current entity types
- Use established error handling patterns

### 2. Performance Optimization

- Process conversations in batches
- Cache embeddings for similar content
- Implement incremental updates (only new messages)
- Use existing debounce mechanisms

### 3. Privacy & Security

- Never store raw conversation content
- Only store summaries and metadata
- Respect existing file permissions
- Allow opt-out via configuration

### 4. Deduplication Strategy

- Search ALL manual entries for relevant content (not just chat_history type)
- Update most relevant entry (score > 0.8) with new observations
- Preserve manual edits and categorization
- Create new entry only if no relevant match found

**Enhanced implementation approach**:

```python
class ChatDeduplicator:
    """Check and update existing entries instead of creating duplicates."""
    
    def find_existing_entry(self, collection: str, 
                          conversation_id: str) -> Optional[Dict]:
        """Search for existing manual entry about this conversation."""
        # Search by conversation ID or summary content
        results = self.vector_store.search_similar(
            collection,
            f"conversation {conversation_id}",
            limit=5,
            filter={"entity_type": "chat_history"}
        )
        
        for result in results:
            if result['score'] > 0.9:  # High similarity
                return result
        return None
    
    def update_or_create(self, summary: Dict, collection: str) -> str:
        """Update existing entry or create new one."""
        existing = self.find_existing_entry(
            collection, 
            summary['conversation_id']
        )
        
        if existing:
            # Preserve manual edits
            merged = {
                **existing['payload'],
                'observations': list(set(
                    existing['payload']['observations'] + 
                    summary['observations']
                )),
                'last_updated': datetime.now().isoformat()
            }
            return self.vector_store.update_point(
                collection, existing['id'], merged
            )
        else:
            # Create new entry
            return self.vector_store.create_entity_point(
                Entity(
                    name=f"chat_{summary['conversation_id']}",
                    entity_type=EntityType.CHAT_HISTORY,
                    observations=summary['observations']
                ),
                embedding,
                collection
            )
```

### 4. Integration Points

- Separate CLI commands (not part of regular indexing)
- Stores in same collection as code entities
- Seamless integration with existing search (returns both code + chat results)
- Compatible with current MCP server setup
- Works with existing collection management
- Preserves manual memory protection

## Success Metrics

1. **Functionality**
    - Successfully parse 100% of valid JSONL files
    - Achieve >90% categorization accuracy
    - Enable semantic search across chat history

2. **Performance**
    - Summarize average conversation in <2 seconds
    - Index 1000 conversations in <5 minutes
    - Search latency <100ms

3. **Reliability**
    - Zero data loss during summarization
    - Graceful handling of API failures
    - Automatic recovery from interruptions

4. **User Experience**
    - Simple CLI commands
    - Clear progress indicators
    - Helpful error messages
    - Comprehensive documentation

## Chat Parser Implementation

### Directory Mapping Implementation

The chat parser needs to map project paths to Claude's encoded directory structure:

```python
class ChatDirectoryMapper:
    """Maps project paths to Claude Code chat directories."""
    
    def __init__(self, claude_projects_root: Path = None):
        """Initialize mapper with Claude projects root.
        
        Args:
            claude_projects_root: Override default ~/.claude/projects
        """
        if claude_projects_root is None:
            claude_projects_root = Path.home() / '.claude' / 'projects'
        self.projects_root = claude_projects_root
    
    def get_project_chat_directory(self, project_path: Path) -> Path:
        """Get chat directory for a project path.
        
        Args:
            project_path: Absolute path to project
            
        Returns:
            Path to chat directory or None if not found
            
        Example:
            /home/user/projects/myapp → ~/.claude/projects/-home-user-projects-myapp
        """
        # Normalize path to absolute
        project_path = Path(project_path).resolve()
        
        # Encode path by replacing separators with dashes
        encoded_name = str(project_path).replace('/', '-')
        
        # Handle Windows paths
        if os.name == 'nt':
            encoded_name = encoded_name.replace('\\', '-').replace(':', '')
        
        chat_dir = self.projects_root / encoded_name
        
        # Verify directory exists
        if not chat_dir.exists():
            logger.warning(f"Chat directory not found: {chat_dir}")
            return None
            
        return chat_dir
    
    def list_chat_files(self, chat_dir: Path, 
                       min_age_hours: float = 0) -> List[Path]:
        """List JSONL chat files in directory.
        
        Args:
            chat_dir: Chat directory path
            min_age_hours: Only include files older than this
            
        Returns:
            List of JSONL file paths sorted by modification time
        """
        if not chat_dir or not chat_dir.exists():
            return []
        
        current_time = time.time()
        min_age_seconds = min_age_hours * 3600
        
        jsonl_files = []
        for file_path in chat_dir.glob('*.jsonl'):
            # Check age if threshold specified
            if min_age_hours > 0:
                file_age = current_time - file_path.stat().st_mtime
                if file_age < min_age_seconds:
                    continue
            
            jsonl_files.append(file_path)
        
        # Sort by modification time (oldest first)
        return sorted(jsonl_files, key=lambda p: p.stat().st_mtime)
```

### JSONL Parsing Best Practices

Stream large JSONL files to avoid memory issues:

```python
class JSONLStreamParser:
    """Memory-efficient JSONL parser with error recovery."""
    
    def __init__(self, max_messages_per_chunk: int = 100):
        """Initialize parser.
        
        Args:
            max_messages_per_chunk: Max messages before yielding chunk
        """
        self.max_messages_per_chunk = max_messages_per_chunk
        self.logger = get_logger()
    
    def parse_jsonl_stream(self, file_path: Path) -> Iterator[List[Dict]]:
        """Parse JSONL file in chunks.
        
        Yields:
            Lists of parsed messages (up to max_messages_per_chunk)
            
        Example:
            for message_chunk in parser.parse_jsonl_stream(path):
                # Process chunk of messages
                summary = summarizer.summarize(message_chunk)
        """
        chunk = []
        line_num = 0
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line_num += 1
                    line = line.strip()
                    
                    if not line:
                        continue
                    
                    try:
                        message = json.loads(line)
                        chunk.append(message)
                        
                        # Yield chunk if size reached
                        if len(chunk) >= self.max_messages_per_chunk:
                            yield chunk
                            chunk = []
                            
                    except json.JSONDecodeError as e:
                        self.logger.warning(
                            f"Invalid JSON at {file_path}:{line_num} - {e}"
                        )
                        # Continue parsing remaining lines
                        continue
                
                # Yield final chunk
                if chunk:
                    yield chunk
                    
        except Exception as e:
            self.logger.error(f"Failed to read {file_path}: {e}")
            # Yield partial results if any
            if chunk:
                yield chunk
    
    def extract_conversation_metadata(self, messages: List[Dict]) -> Dict:
        """Extract metadata from conversation messages.
        
        Returns:
            Dict with start_time, end_time, message_count, participants
        """
        if not messages:
            return {}
        
        # Extract timestamps
        timestamps = []
        for msg in messages:
            if 'timestamp' in msg:
                try:
                    ts = datetime.fromisoformat(msg['timestamp'])
                    timestamps.append(ts)
                except:
                    pass
        
        metadata = {
            'message_count': len(messages),
            'participants': list({msg.get('type', 'unknown') for msg in messages}),
        }
        
        if timestamps:
            metadata['start_time'] = min(timestamps).isoformat()
            metadata['end_time'] = max(timestamps).isoformat()
            metadata['duration_hours'] = (max(timestamps) - min(timestamps)).total_seconds() / 3600
        
        return metadata
```

### Error Handling Patterns

Implement graceful degradation following existing patterns:

```python
class ChatParserError(Exception):
    """Base exception for chat parsing errors."""
    pass

class ChatParser:
    """Main chat parser with comprehensive error handling."""
    
    def __init__(self, config: IndexerConfig):
        self.config = config
        self.logger = get_logger()
        self.directory_mapper = ChatDirectoryMapper()
        self.jsonl_parser = JSONLStreamParser()
        
        # Track errors for reporting
        self.errors = []
        self.warnings = []
    
    def parse_project_chats(self, project_path: Path) -> ChatParsingResult:
        """Parse all chat files for a project.
        
        Returns:
            ChatParsingResult with conversations and metrics
        """
        result = ChatParsingResult(project_path=project_path)
        start_time = time.time()
        
        try:
            # Get chat directory
            chat_dir = self.directory_mapper.get_project_chat_directory(project_path)
            if not chat_dir:
                result.errors.append(f"No chat directory found for {project_path}")
                return result
            
            # List chat files
            chat_files = self.directory_mapper.list_chat_files(
                chat_dir, 
                min_age_hours=self.config.chat_inactivity_threshold
            )
            
            self.logger.info(f"Found {len(chat_files)} chat files to process")
            
            # Process each file
            for file_path in chat_files:
                try:
                    self._process_chat_file(file_path, result)
                except Exception as e:
                    self.logger.error(f"Failed to process {file_path}: {e}")
                    result.failed_files.append(str(file_path))
                    result.errors.append(str(e))
                    # Continue with other files
            
        except Exception as e:
            result.errors.append(f"Critical error: {e}")
            result.success = False
        
        result.processing_time = time.time() - start_time
        return result
    
    def _process_chat_file(self, file_path: Path, result: ChatParsingResult):
        """Process single chat file with error recovery."""
        
        conversations = []
        total_messages = 0
        
        try:
            # Parse in chunks to handle large files
            for message_chunk in self.jsonl_parser.parse_jsonl_stream(file_path):
                total_messages += len(message_chunk)
                
                # Group into conversations by time gaps
                conversations.extend(
                    self._group_into_conversations(message_chunk)
                )
            
            # Update result
            result.conversations.extend(conversations)
            result.files_processed += 1
            result.processed_files.append(str(file_path))
            
            self.logger.debug(
                f"Processed {file_path.name}: "
                f"{total_messages} messages, "
                f"{len(conversations)} conversations"
            )
            
        except MemoryError:
            # Handle out of memory gracefully
            self.logger.error(f"Out of memory processing {file_path}")
            result.warnings.append(
                f"File too large, partially processed: {file_path.name}"
            )
            # Return partial results
            if conversations:
                result.conversations.extend(conversations)
    
    def _group_into_conversations(self, messages: List[Dict], 
                                 gap_hours: float = 4) -> List[Conversation]:
        """Group messages into conversations by time gaps."""
        
        if not messages:
            return []
        
        conversations = []
        current_conv = []
        last_timestamp = None
        
        for msg in messages:
            # Extract timestamp
            timestamp = None
            if 'timestamp' in msg:
                try:
                    timestamp = datetime.fromisoformat(msg['timestamp'])
                except:
                    pass
            
            # Check for conversation break
            if timestamp and last_timestamp:
                gap = (timestamp - last_timestamp).total_seconds() / 3600
                if gap > gap_hours:
                    # Start new conversation
                    if current_conv:
                        conversations.append(Conversation(messages=current_conv))
                    current_conv = []
            
            current_conv.append(msg)
            last_timestamp = timestamp
        
        # Add final conversation
        if current_conv:
            conversations.append(Conversation(messages=current_conv))
        
        return conversations
```

### Performance Considerations

Optimize for large conversation histories:

```python
class PerformanceOptimizedChatIndexer:
    """Chat indexer with performance optimizations."""
    
    def __init__(self, config: IndexerConfig, embedder: Embedder, 
                 vector_store: VectorStore):
        self.config = config
        self.embedder = embedder
        self.vector_store = vector_store
        self.logger = get_logger()
        
        # Performance settings
        self.batch_size = config.get('chat_batch_size', 10)
        self.max_concurrent_summaries = config.get('max_concurrent_summaries', 3)
        self.cache_embeddings = config.get('cache_chat_embeddings', True)
        
        # Embedding cache
        self._embedding_cache = {}
    
    async def index_chats_batch(self, conversations: List[Conversation]) -> IndexingResult:
        """Index conversations in optimized batches.
        
        Key optimizations:
        1. Batch API calls to OpenAI
        2. Cache similar embeddings
        3. Concurrent processing with limits
        4. Memory-mapped file handling
        """
        result = IndexingResult(operation="chat_indexing")
        
        # Process in batches
        for i in range(0, len(conversations), self.batch_size):
            batch = conversations[i:i + self.batch_size]
            
            try:
                # Summarize batch concurrently
                summaries = await self._summarize_batch_concurrent(batch)
                
                # Generate embeddings with caching
                embeddings = await self._generate_embeddings_cached(summaries)
                
                # Store in vector database
                await self._store_batch(summaries, embeddings)
                
                result.entities_created += len(summaries)
                
            except Exception as e:
                self.logger.error(f"Batch processing failed: {e}")
                result.errors.append(str(e))
                # Continue with next batch
        
        return result
    
    def _get_embedding_cache_key(self, text: str) -> str:
        """Generate cache key for embedding."""
        # Use first 100 chars + hash for cache key
        prefix = text[:100]
        text_hash = hashlib.md5(text.encode()).hexdigest()[:8]
        return f"{prefix}_{text_hash}"
    
    async def _generate_embeddings_cached(self, summaries: List[Summary]) -> List[List[float]]:
        """Generate embeddings with caching."""
        
        embeddings = []
        texts_to_embed = []
        cache_indices = []
        
        for i, summary in enumerate(summaries):
            cache_key = self._get_embedding_cache_key(summary.content)
            
            if cache_key in self._embedding_cache:
                # Use cached embedding
                embeddings.append(self._embedding_cache[cache_key])
            else:
                # Mark for embedding generation
                texts_to_embed.append(summary.content)
                cache_indices.append(i)
                embeddings.append(None)  # Placeholder
        
        # Generate new embeddings if needed
        if texts_to_embed:
            new_embeddings = await self.embedder.embed_batch(texts_to_embed)
            
            # Fill in results and update cache
            for idx, embedding in zip(cache_indices, new_embeddings):
                embeddings[idx] = embedding
                cache_key = self._get_embedding_cache_key(summaries[idx].content)
                self._embedding_cache[cache_key] = embedding
        
        # Limit cache size
        if len(self._embedding_cache) > 1000:
            # Remove oldest entries (simple LRU)
            keys = list(self._embedding_cache.keys())
            for key in keys[:200]:  # Remove 20%
                del self._embedding_cache[key]
        
        return embeddings
```

### Incremental Parsing Approach

Track processed files to enable incremental updates:

```python
class IncrementalChatParser:
    """Chat parser with incremental update support."""

    def __init__(self, state_manager: StateManager):
        self.state_manager = state_manager
        self.logger = get_logger()

    def parse_incremental(self, project_path: Path,
                          collection_name: str) -> ChatParsingResult:
        """Parse only new/modified chat files.
        
        Uses file modification times and content hashes to detect changes.
        """
        # Load previous state
        state = self.state_manager.load_chat_state(project_path, collection_name)
        processed_files = state.get('processed_files', {})

        result = ChatParsingResult(project_path=project_path)

        # Get all chat files
        chat_dir = self.directory_mapper.get_project_chat_directory(project_path)
        all_files = self.directory_mapper.list_chat_files(chat_dir)

        # Identify files to process
        files_to_process = []
        for file_path in all_files:
            file_stat = file_path.stat()
            file_key = str(file_path)

            # Check if file is new or modified
            if file_key not in processed_files:
                files_to_process.append(file_path)
                self.logger.info(f"New file: {file_path.name}")
            else:
                prev_mtime = processed_files[file_key].get('mtime', 0)
                if file_stat.st_mtime > prev_mtime:
                    files_to_process.append(file_path)
                    self.logger.info(f"Modified file: {file_path.name}")

        # Process only changed files
        for file_path in files_to_process:
            try:
                conversations = self._process_file_incremental(
                    file_path,
                    processed_files.get(str(file_path), {})
                )

                result.conversations.extend(conversations)

                # Update state
                processed_files[str(file_path)] = {
                    'mtime': file_path.stat().st_mtime,
                    'size': file_path.stat().st_size,
                    'last_message_id': self._get_last_message_id(conversations),
                    'conversation_count': len(conversations)
                }

            except Exception as e:
                self.logger.error(f"Failed to process {file_path}: {e}")
                result.errors.append(str(e))

        # Save updated state
        state['processed_files'] = processed_files
        state['last_run'] = datetime.now().isoformat()
        self.state_manager.save_chat_state(project_path, collection_name, state)

        self.logger.info(
            f"Incremental parsing complete: "
            f"{len(files_to_process)} files processed, "
            f"{len(result.conversations)} new conversations"
        )

        return result

    def _process_file_incremental(self, file_path: Path,
                                  prev_state: Dict) -> List[Conversation]:
        """Process file incrementally from last position."""

        conversations = []
        last_id = prev_state.get('last_message_id')

        # If we have a last message ID, seek to that position
        start_processing = last_id is None

        for message_chunk in self.jsonl_parser.parse_jsonl_stream(file_path):
            # Skip already processed messages
            if not start_processing:
                for msg in message_chunk:
                    if msg.get('id') == last_id:
                        start_processing = True
                        break
                if not start_processing:
                    continue

            # Process new messages
            new_conversations = self._group_into_conversations(message_chunk)
            conversations.extend(new_conversations)

        return conversations


class StateManager:
    """Manages persistent state for incremental processing."""

    def __init__(self, state_dir: Path = None):
        if state_dir is None:
            state_dir = Path.home() / '.claude-indexer' / 'chat-state'
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _get_state_file(self, project_path: Path, collection_name: str) -> Path:
        """Get state file path for project/collection."""
        # Create deterministic filename
        project_hash = hashlib.md5(str(project_path).encode()).hexdigest()[:8]
        filename = f"chat-state-{collection_name}-{project_hash}.json"
        return self.state_dir / filename

    def load_chat_state(self, project_path: Path, collection_name: str) -> Dict:
        """Load chat indexing state."""
        state_file = self._get_state_file(project_path, collection_name)

        if state_file.exists():
            try:
                with open(state_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load state: {e}")

        return {}

    def save_chat_state(self, project_path: Path, collection_name: str,
                        state: Dict) -> None:
        """Save chat indexing state."""
        state_file = self._get_state_file(project_path, collection_name)

        try:
            with open(state_file, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")
```

## Implementation Notes

### File Locations & Project Mapping

- Claude projects: `~/.claude/projects/`
- Project paths encoded by replacing `/` with `-`
    - Example: `/home/user/projects/myapp` → `-home-user-projects-myapp`
- Each project has its own encoded directory
- Conversations stored as JSONL files: `[session-uuid].jsonl`
- **Only index chats for current project directory**

### JSONL Structure

```json
{
  "id": "msg_123",
  "type": "human",
  "content": "...",
  "timestamp": "..."
}
{
  "id": "msg_124",
  "type": "assistant",
  "content": "...",
  "timestamp": "..."
}
```

### Summarization Prompt Template

```
From this conversation, exract and categorize:

1. SUMMARIZE key points:
   - Problems solved
   - Solutions implemented
   - Debugging approaches used
   - Decisions made

2. CATEGORIZE into one of:
   - debugging_pattern (errors, fixes, troubleshooting)
   - implementation_pattern (new code, features, algorithms)
   - configuration_pattern (setup, environment, deployment)
   - integration_pattern (APIs, services, external systems)
   - architecture_pattern (design decisions, structure)
   - performance_pattern (optimization, speed, memory)
   - knowledge_insight (learnings, research, insights)

Output JSON:
{
  "summary": "...",
  "category": "debugging_pattern",
  "key_points": [...],
  "metadata": {...}
}
```

### Category Detection Keywords

- Use existing keyword mappings from manual entry system
- Apply weighted scoring based on content frequency
- Default to `knowledge_insight` for ambiguous content

## Chat Summarizer Implementation

### OpenAI Integration Best Practices

The chat summarizer leverages GPT-4o-mini for its balance of quality, speed, and cost-effectiveness:

```python
class OpenAISummarizer:
    """Summarize conversations using OpenAI GPT-4o-mini with retry logic."""
    
    # Model configurations with 2025 pricing
    MODELS = {
        "gpt-4o-mini": {
            "max_input_tokens": 128000,
            "max_output_tokens": 16384,
            "cost_per_1m_input": 0.15,   # $0.15 per 1M input tokens
            "cost_per_1m_output": 0.60,  # $0.60 per 1M output tokens
            "requests_per_minute": 5000,
            "tokens_per_minute": 1000000
        },
        "gpt-4o": {  # Alternative for complex conversations
            "max_input_tokens": 128000,
            "max_output_tokens": 16384,
            "cost_per_1m_input": 2.50,   # $2.50 per 1M input tokens
            "cost_per_1m_output": 10.00, # $10.00 per 1M output tokens
            "requests_per_minute": 500,
            "tokens_per_minute": 600000
        }
    }
    
    def __init__(self, api_key: str, model: str = "gpt-4o-mini",
                 max_retries: int = 3, base_delay: float = 1.0):
        """Initialize summarizer with OpenAI client and retry configuration.
        
        Args:
            api_key: OpenAI API key
            model: Model to use (default: gpt-4o-mini)
            max_retries: Maximum retry attempts
            base_delay: Base delay for exponential backoff
        """
        self.model = model
        self.model_config = self.MODELS[model]
        self.client = openai.OpenAI(api_key=api_key, timeout=30.0)
        
        # Retry configuration
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = 60.0
        self.backoff_factor = 2.0
        
        # Rate limiting tracking
        self._request_times: List[float] = []
        self._token_usage: List[tuple[float, int, int]] = []  # (time, input, output)
        
        # Cost tracking
        self.total_cost = 0.0
        self.total_conversations = 0
```

### Prompt Engineering for Accurate Categorization

Design prompts that extract maximum value while ensuring consistent categorization:

```python
class PromptTemplates:
    """Carefully engineered prompts for different conversation types."""
    
    # Main summarization prompt with JSON output
    SUMMARIZE_CONVERSATION = """Analyze this Claude Code conversation and extract actionable insights.

CONVERSATION:
{conversation_text}

Extract and structure the following information:

1. **SUMMARY** (2-3 sentences):
   - Core problem addressed
   - Solution implemented
   - Key outcomes

2. **CATEGORY** (select ONE primary category):
   - debugging_pattern: Error diagnosis, troubleshooting, root cause analysis
   - implementation_pattern: New code, features, algorithms, development
   - integration_pattern: APIs, services, databases, external systems
   - configuration_pattern: Setup, environment, deployment, tooling
   - architecture_pattern: Design decisions, structure, components
   - performance_pattern: Optimization, speed, memory, bottlenecks
   - knowledge_insight: Research, learnings, methodology, best practices

3. **KEY_INSIGHTS** (3-5 bullet points):
   - Specific solutions discovered
   - Patterns identified
   - Reusable knowledge gained

4. **TECHNICAL_CONTEXT**:
   - Languages/frameworks used
   - Tools mentioned
   - Error types encountered (if any)

Output as JSON:
{
  "summary": "...",
  "category": "debugging_pattern",
  "confidence": 0.95,
  "key_insights": ["...", "..."],
  "technical_context": {
    "languages": ["python"],
    "frameworks": ["fastapi"],
    "tools": ["docker"],
    "error_types": ["ImportError"]
  },
  "tokens_saved": 12500
}

Focus on ACTIONABLE knowledge that can be reused in future conversations."""

    # Specialized prompts for different scenarios
    SUMMARIZE_DEBUGGING = """Focus on the debugging process in this conversation:

{conversation_text}

Extract:
1. Initial error/problem description
2. Debugging steps taken
3. Root cause identified
4. Solution implemented
5. Lessons learned

Output as JSON with emphasis on reusable debugging patterns."""

    SUMMARIZE_IMPLEMENTATION = """Focus on the implementation details in this conversation:

{conversation_text}

Extract:
1. Feature/functionality implemented
2. Design decisions made
3. Code patterns used
4. Testing approach
5. Best practices followed

Output as JSON with emphasis on reusable implementation patterns."""
    
    @classmethod
    def get_prompt_for_category_hint(cls, detected_keywords: List[str]) -> str:
        """Select specialized prompt based on detected keywords."""
        debugging_keywords = {'error', 'bug', 'fix', 'debug', 'issue', 'problem'}
        implementation_keywords = {'implement', 'create', 'build', 'develop', 'feature'}
        
        if any(kw in detected_keywords for kw in debugging_keywords):
            return cls.SUMMARIZE_DEBUGGING
        elif any(kw in detected_keywords for kw in implementation_keywords):
            return cls.SUMMARIZE_IMPLEMENTATION
        
        return cls.SUMMARIZE_CONVERSATION
```

### Token Management and Chunking Strategies

Implement intelligent chunking to handle long conversations efficiently:

```python
class TokenManager:
    """Manage tokens for optimal API usage and cost control."""
    
    def __init__(self, model_config: Dict[str, Any]):
        self.max_input_tokens = model_config['max_input_tokens']
        self.max_output_tokens = model_config['max_output_tokens']
        
        # Reserve tokens for system prompt and response
        self.reserved_input_tokens = 1000
        self.reserved_output_tokens = 500
        
        # Token estimation (using tiktoken for accuracy)
        try:
            import tiktoken
            self.encoder = tiktoken.encoding_for_model("gpt-4")
        except:
            self.encoder = None
    
    def estimate_tokens(self, text: str) -> int:
        """Estimate token count for text."""
        if self.encoder:
            return len(self.encoder.encode(text))
        else:
            # Fallback: ~4 characters per token
            return len(text) // 4
    
    def chunk_conversation(self, messages: List[Dict[str, str]], 
                          target_chunk_tokens: int = 4000) -> List[List[Dict[str, str]]]:
        """Split conversation into token-aware chunks.
        
        Args:
            messages: List of message dictionaries
            target_chunk_tokens: Target tokens per chunk
            
        Returns:
            List of message chunks
        """
        chunks = []
        current_chunk = []
        current_tokens = 0
        
        for message in messages:
            message_text = f"{message.get('type', '')}: {message.get('content', '')}"
            message_tokens = self.estimate_tokens(message_text)
            
            # Start new chunk if adding this message would exceed target
            if current_tokens + message_tokens > target_chunk_tokens and current_chunk:
                chunks.append(current_chunk)
                current_chunk = []
                current_tokens = 0
            
            current_chunk.append(message)
            current_tokens += message_tokens
        
        # Add final chunk
        if current_chunk:
            chunks.append(current_chunk)
        
        return chunks
    
    def create_summary_context(self, chunks: List[List[Dict[str, str]]]) -> str:
        """Create context-aware summary from chunks."""
        if len(chunks) == 1:
            # Single chunk - full conversation
            return self._format_messages(chunks[0])
        
        # Multiple chunks - need intelligent merging
        chunk_summaries = []
        
        for i, chunk in enumerate(chunks):
            context = f"Part {i+1}/{len(chunks)}:\n"
            context += self._format_messages(chunk[:5])  # First 5 messages
            
            if len(chunk) > 10:
                context += f"\n... {len(chunk) - 10} messages omitted ...\n"
                context += self._format_messages(chunk[-5:])  # Last 5 messages
            else:
                context += self._format_messages(chunk[5:])
            
            chunk_summaries.append(context)
        
        return "\n\n---\n\n".join(chunk_summaries)
    
    def _format_messages(self, messages: List[Dict[str, str]]) -> str:
        """Format messages for prompt."""
        formatted = []
        for msg in messages:
            role = msg.get('type', 'unknown')
            content = msg.get('content', '')
            # Truncate very long messages
            if len(content) > 1000:
                content = content[:500] + "\n... content truncated ...\n" + content[-500:]
            formatted.append(f"{role}: {content}")
        
        return "\n\n".join(formatted)
```

### Retry Logic and Rate Limiting

Implement robust retry logic following the existing OpenAI embedder patterns:

```python
class OpenAISummarizer(RetryableSummarizer):
    """Full implementation with retry logic and rate limiting."""
    
    def summarize_conversation(self, messages: List[Dict[str, str]], 
                             metadata: Optional[Dict] = None) -> SummaryResult:
        """Summarize a conversation with automatic retry and rate limiting.
        
        Args:
            messages: List of conversation messages
            metadata: Optional metadata about the conversation
            
        Returns:
            SummaryResult with summary, category, and metadata
        """
        start_time = time.time()
        
        # Token management
        token_manager = TokenManager(self.model_config)
        chunks = token_manager.chunk_conversation(messages)
        
        # Detect keywords for prompt selection
        all_text = " ".join(msg.get('content', '') for msg in messages)
        detected_keywords = self._extract_keywords(all_text)
        prompt_template = PromptTemplates.get_prompt_for_category_hint(detected_keywords)
        
        # Prepare conversation context
        conversation_text = token_manager.create_summary_context(chunks)
        estimated_tokens = token_manager.estimate_tokens(conversation_text)
        
        def _summarize():
            # Check rate limits
            self._check_rate_limits(estimated_tokens)
            
            # Make API call
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert at analyzing technical conversations and extracting reusable knowledge patterns."
                    },
                    {
                        "role": "user",
                        "content": prompt_template.format(conversation_text=conversation_text)
                    }
                ],
                response_format={"type": "json_object"},
                temperature=0.3,  # Lower temperature for consistent categorization
                max_tokens=self.reserved_output_tokens
            )
            
            # Record usage
            current_time = time.time()
            self._request_times.append(current_time)
            
            usage = response.usage
            self._token_usage.append((
                current_time,
                usage.prompt_tokens,
                usage.completion_tokens
            ))
            
            # Calculate cost
            input_cost = (usage.prompt_tokens / 1_000_000) * self.model_config['cost_per_1m_input']
            output_cost = (usage.completion_tokens / 1_000_000) * self.model_config['cost_per_1m_output']
            total_cost = input_cost + output_cost
            
            self.total_cost += total_cost
            self.total_conversations += 1
            
            # Parse response
            try:
                result_data = json.loads(response.choices[0].message.content)
            except json.JSONDecodeError:
                # Fallback parsing
                result_data = self._parse_non_json_response(response.choices[0].message.content)
            
            return SummaryResult(
                summary=result_data.get('summary', ''),
                category=result_data.get('category', 'knowledge_insight'),
                confidence=result_data.get('confidence', 0.8),
                key_insights=result_data.get('key_insights', []),
                technical_context=result_data.get('technical_context', {}),
                metadata={
                    'message_count': len(messages),
                    'chunk_count': len(chunks),
                    'tokens_processed': estimated_tokens,
                    'tokens_saved': result_data.get('tokens_saved', estimated_tokens - 500),
                    'processing_time': time.time() - start_time,
                    'cost': total_cost,
                    'model': self.model,
                    **(metadata or {})
                }
            )
        
        try:
            return self._summarize_with_retry(_summarize)
        except Exception as e:
            self.logger.error(f"Summarization failed after retries: {e}")
            # Return fallback summary
            return self._create_fallback_summary(messages, error=str(e))
    
    def _check_rate_limits(self, estimated_tokens: int):
        """Check and enforce rate limits."""
        current_time = time.time()
        
        # Clean old entries
        self._request_times = [t for t in self._request_times if current_time - t < 60]
        self._token_usage = [(t, i, o) for t, i, o in self._token_usage if current_time - t < 60]
        
        # Check request rate
        if len(self._request_times) >= self.model_config['requests_per_minute']:
            sleep_time = 60 - (current_time - self._request_times[0]) + 1
            if sleep_time > 0:
                self.logger.info(f"Rate limit approaching. Sleeping {sleep_time:.1f}s...")
                time.sleep(sleep_time)
        
        # Check token rate
        total_tokens = sum(i + o for _, i, o in self._token_usage) + estimated_tokens
        if total_tokens >= self.model_config['tokens_per_minute']:
            sleep_time = 60 - (current_time - self._token_usage[0][0]) + 1
            if sleep_time > 0:
                self.logger.info(f"Token limit approaching. Sleeping {sleep_time:.1f}s...")
                time.sleep(sleep_time)
    
    def _create_fallback_summary(self, messages: List[Dict], error: str) -> SummaryResult:
        """Create basic summary when API fails."""
        # Extract basic information
        message_count = len(messages)
        first_message = messages[0].get('content', '')[:100] if messages else ''
        
        # Simple keyword-based categorization
        all_text = " ".join(msg.get('content', '')[:200] for msg in messages[:10])
        category = self._detect_category_keywords(all_text)
        
        return SummaryResult(
            summary=f"Conversation with {message_count} messages. Started with: {first_message}...",
            category=category,
            confidence=0.5,
            key_insights=["Failed to generate detailed summary due to API error"],
            technical_context={},
            metadata={
                'message_count': message_count,
                'error': error,
                'fallback': True
            }
        )
```

### Batch Processing for Multiple Conversations

Optimize for processing multiple conversations efficiently:

```python
class BatchConversationProcessor:
    """Process multiple conversations with optimal batching."""
    
    def __init__(self, summarizer: OpenAISummarizer, 
                 max_concurrent: int = 3,
                 batch_size: int = 10):
        self.summarizer = summarizer
        self.max_concurrent = max_concurrent
        self.batch_size = batch_size
        self.logger = get_logger()
    
    async def process_conversations_async(self, 
                                        conversations: List[Conversation],
                                        progress_callback: Optional[Callable] = None) -> List[SummaryResult]:
        """Process conversations concurrently with progress tracking.
        
        Args:
            conversations: List of conversations to process
            progress_callback: Optional callback for progress updates
            
        Returns:
            List of summary results
        """
        results = []
        semaphore = asyncio.Semaphore(self.max_concurrent)
        
        async def process_single(conv: Conversation, index: int) -> SummaryResult:
            async with semaphore:
                try:
                    # Run summarization in thread pool to avoid blocking
                    loop = asyncio.get_event_loop()
                    result = await loop.run_in_executor(
                        None,
                        self.summarizer.summarize_conversation,
                        conv.messages,
                        {'conversation_id': conv.id, 'index': index}
                    )
                    
                    if progress_callback:
                        progress_callback(index, len(conversations), result)
                    
                    return result
                    
                except Exception as e:
                    self.logger.error(f"Failed to process conversation {index}: {e}")
                    return self.summarizer._create_fallback_summary(
                        conv.messages, 
                        error=str(e)
                    )
        
        # Process in batches to avoid overwhelming the API
        for i in range(0, len(conversations), self.batch_size):
            batch = conversations[i:i + self.batch_size]
            batch_tasks = [
                process_single(conv, i + j) 
                for j, conv in enumerate(batch)
            ]
            
            batch_results = await asyncio.gather(*batch_tasks)
            results.extend(batch_results)
            
            # Brief pause between batches
            if i + self.batch_size < len(conversations):
                await asyncio.sleep(1.0)
        
        return results
    
    def process_conversations_sync(self, conversations: List[Conversation]) -> ProcessingReport:
        """Synchronous batch processing with detailed reporting."""
        report = ProcessingReport()
        report.start_time = time.time()
        
        # Group by estimated processing time
        small_convs = []  # < 50 messages
        medium_convs = []  # 50-200 messages
        large_convs = []  # > 200 messages
        
        for conv in conversations:
            msg_count = len(conv.messages)
            if msg_count < 50:
                small_convs.append(conv)
            elif msg_count < 200:
                medium_convs.append(conv)
            else:
                large_convs.append(conv)
        
        self.logger.info(
            f"Processing {len(conversations)} conversations: "
            f"{len(small_convs)} small, {len(medium_convs)} medium, {len(large_convs)} large"
        )
        
        # Process each group
        results = []
        
        # Small conversations can be processed more aggressively
        if small_convs:
            small_processor = BatchConversationProcessor(
                self.summarizer, 
                max_concurrent=5,
                batch_size=20
            )
            small_results = asyncio.run(
                small_processor.process_conversations_async(small_convs)
            )
            results.extend(small_results)
            report.small_processed = len(small_results)
        
        # Medium conversations with standard settings
        if medium_convs:
            medium_results = asyncio.run(
                self.process_conversations_async(medium_convs)
            )
            results.extend(medium_results)
            report.medium_processed = len(medium_results)
        
        # Large conversations processed carefully
        if large_convs:
            large_processor = BatchConversationProcessor(
                self.summarizer,
                max_concurrent=1,  # One at a time
                batch_size=3
            )
            large_results = asyncio.run(
                large_processor.process_conversations_async(large_convs)
            )
            results.extend(large_results)
            report.large_processed = len(large_results)
        
        # Generate report
        report.total_processed = len(results)
        report.successful = sum(1 for r in results if not r.metadata.get('error'))
        report.failed = sum(1 for r in results if r.metadata.get('error'))
        report.total_cost = sum(r.metadata.get('cost', 0) for r in results)
        report.total_tokens = sum(r.metadata.get('tokens_processed', 0) for r in results)
        report.processing_time = time.time() - report.start_time
        report.average_cost_per_conversation = report.total_cost / max(report.total_processed, 1)
        
        self.logger.info(
            f"Batch processing complete: {report.successful} successful, "
            f"{report.failed} failed, ${report.total_cost:.4f} total cost"
        )
        
        return report
```

### Cost Tracking and Reporting

Implement comprehensive cost tracking for budget management:

```python
class CostTracker:
    """Track and report API usage costs."""
    
    def __init__(self, budget_limit: Optional[float] = None):
        self.budget_limit = budget_limit
        self.usage_history: List[UsageRecord] = []
        self.daily_costs: Dict[str, float] = {}
        self.model_costs: Dict[str, ModelCost] = {}
    
    def record_usage(self, model: str, input_tokens: int, output_tokens: int, 
                    cost: float, timestamp: Optional[datetime] = None):
        """Record API usage for cost tracking."""
        if timestamp is None:
            timestamp = datetime.now()
        
        record = UsageRecord(
            timestamp=timestamp,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost
        )
        
        self.usage_history.append(record)
        
        # Update daily costs
        date_key = timestamp.strftime("%Y-%m-%d")
        self.daily_costs[date_key] = self.daily_costs.get(date_key, 0) + cost
        
        # Update model-specific costs
        if model not in self.model_costs:
            self.model_costs[model] = ModelCost(model=model)
        
        model_cost = self.model_costs[model]
        model_cost.total_input_tokens += input_tokens
        model_cost.total_output_tokens += output_tokens
        model_cost.total_cost += cost
        model_cost.request_count += 1
        
        # Check budget
        if self.budget_limit and self.get_total_cost() > self.budget_limit:
            self.logger.warning(
                f"Budget limit exceeded! Total cost: ${self.get_total_cost():.2f}, "
                f"Limit: ${self.budget_limit:.2f}"
            )
    
    def get_cost_report(self, days: int = 30) -> CostReport:
        """Generate comprehensive cost report."""
        cutoff_date = datetime.now() - timedelta(days=days)
        recent_usage = [r for r in self.usage_history if r.timestamp > cutoff_date]
        
        report = CostReport()
        report.period_days = days
        report.total_cost = sum(r.cost for r in recent_usage)
        report.total_requests = len(recent_usage)
        report.total_input_tokens = sum(r.input_tokens for r in recent_usage)
        report.total_output_tokens = sum(r.output_tokens for r in recent_usage)
        
        # Daily breakdown
        daily_stats = {}
        for record in recent_usage:
            date_key = record.timestamp.strftime("%Y-%m-%d")
            if date_key not in daily_stats:
                daily_stats[date_key] = {
                    'cost': 0, 'requests': 0, 
                    'input_tokens': 0, 'output_tokens': 0
                }
            
            stats = daily_stats[date_key]
            stats['cost'] += record.cost
            stats['requests'] += 1
            stats['input_tokens'] += record.input_tokens
            stats['output_tokens'] += record.output_tokens
        
        report.daily_breakdown = daily_stats
        report.average_daily_cost = report.total_cost / max(len(daily_stats), 1)
        report.average_cost_per_request = report.total_cost / max(report.total_requests, 1)
        
        # Model breakdown
        report.model_breakdown = {}
        for model, model_cost in self.model_costs.items():
            report.model_breakdown[model] = {
                'total_cost': model_cost.total_cost,
                'request_count': model_cost.request_count,
                'average_cost': model_cost.total_cost / max(model_cost.request_count, 1),
                'input_tokens': model_cost.total_input_tokens,
                'output_tokens': model_cost.total_output_tokens
            }
        
        # Projections
        if report.average_daily_cost > 0:
            report.projected_monthly_cost = report.average_daily_cost * 30
            report.projected_yearly_cost = report.average_daily_cost * 365
        
        return report
    
    def export_usage_csv(self, filepath: Path):
        """Export usage history to CSV for analysis."""
        import csv
        
        with open(filepath, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'Timestamp', 'Model', 'Input Tokens', 'Output Tokens', 
                'Cost', 'Cost per 1K Tokens'
            ])
            
            for record in self.usage_history:
                total_tokens = record.input_tokens + record.output_tokens
                cost_per_1k = (record.cost / total_tokens * 1000) if total_tokens > 0 else 0
                
                writer.writerow([
                    record.timestamp.isoformat(),
                    record.model,
                    record.input_tokens,
                    record.output_tokens,
                    f"${record.cost:.6f}",
                    f"${cost_per_1k:.6f}"
                ])
```

### JSON Output Validation

Ensure robust JSON parsing with fallback strategies:

```python
class OutputValidator:
    """Validate and sanitize API outputs."""
    
    @staticmethod
    def validate_summary_json(response_text: str) -> Dict[str, Any]:
        """Validate and parse summary JSON with fallbacks.
        
        Args:
            response_text: Raw response from API
            
        Returns:
            Validated dictionary with required fields
        """
        # Try direct JSON parsing
        try:
            data = json.loads(response_text)
            return OutputValidator._validate_schema(data)
        except json.JSONDecodeError:
            pass
        
        # Try extracting JSON from markdown code blocks
        json_match = re.search(r'```json\s*(.*?)\s*```', response_text, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(1))
                return OutputValidator._validate_schema(data)
            except:
                pass
        
        # Try finding JSON-like structure
        json_match = re.search(r'\{[^{}]*\}', response_text, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group())
                return OutputValidator._validate_schema(data)
            except:
                pass
        
        # Fallback: Extract key information using regex
        return OutputValidator._extract_fallback(response_text)
    
    @staticmethod
    def _validate_schema(data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and fill missing fields."""
        # Required fields with defaults
        validated = {
            'summary': data.get('summary', 'No summary available'),
            'category': data.get('category', 'knowledge_insight'),
            'confidence': float(data.get('confidence', 0.7)),
            'key_insights': data.get('key_insights', []),
            'technical_context': data.get('technical_context', {}),
            'tokens_saved': data.get('tokens_saved', 0)
        }
        
        # Validate category
        valid_categories = {
            'debugging_pattern', 'implementation_pattern', 'integration_pattern',
            'configuration_pattern', 'architecture_pattern', 'performance_pattern',
            'knowledge_insight'
        }
        
        if validated['category'] not in valid_categories:
            # Try to map common variations
            category_map = {
                'debug': 'debugging_pattern',
                'implement': 'implementation_pattern',
                'integrate': 'integration_pattern',
                'config': 'configuration_pattern',
                'architect': 'architecture_pattern',
                'perform': 'performance_pattern',
                'knowledge': 'knowledge_insight'
            }
            
            for key, mapped in category_map.items():
                if key in validated['category'].lower():
                    validated['category'] = mapped
                    break
            else:
                validated['category'] = 'knowledge_insight'
        
        # Ensure key_insights is a list
        if not isinstance(validated['key_insights'], list):
            validated['key_insights'] = [str(validated['key_insights'])]
        
        # Validate confidence range
        validated['confidence'] = max(0.0, min(1.0, validated['confidence']))
        
        return validated
    
    @staticmethod
    def _extract_fallback(text: str) -> Dict[str, Any]:
        """Extract information using patterns when JSON parsing fails."""
        result = {
            'summary': '',
            'category': 'knowledge_insight',
            'confidence': 0.5,
            'key_insights': [],
            'technical_context': {},
            'tokens_saved': 0
        }
        
        # Extract summary
        summary_match = re.search(r'summary[:\s]+(.*?)(?:category|$)', text, re.IGNORECASE | re.DOTALL)
        if summary_match:
            result['summary'] = summary_match.group(1).strip()[:500]
        
        # Extract category
        category_match = re.search(r'category[:\s]+(\w+_pattern|\w+_insight)', text, re.IGNORECASE)
        if category_match:
            result['category'] = category_match.group(1).lower()
        
        # Extract insights
        insights_match = re.search(r'insights?[:\s]+(.*?)(?:technical|$)', text, re.IGNORECASE | re.DOTALL)
        if insights_match:
            insights_text = insights_match.group(1)
            # Split by bullet points or numbers
            insights = re.split(r'[\n•\-\d]+\.?\s*', insights_text)
            result['key_insights'] = [i.strip() for i in insights if i.strip()][:5]
        
        return result
```

### Integration Example

Complete example showing how all components work together:

```python
# Initialize components
config = IndexerConfig.from_file("settings.txt")
cost_tracker = CostTracker(budget_limit=10.0)  # $10 budget

# Create summarizer with cost tracking
summarizer = OpenAISummarizer(
    api_key=config.openai_api_key,
    model="gpt-4o-mini"
)

# Wrap with cost tracking
class TrackedSummarizer(OpenAISummarizer):
    def __init__(self, *args, cost_tracker: CostTracker, **kwargs):
        super().__init__(*args, **kwargs)
        self.cost_tracker = cost_tracker
    
    def summarize_conversation(self, messages, metadata=None):
        result = super().summarize_conversation(messages, metadata)
        
        # Track costs
        if result.metadata.get('cost'):
            self.cost_tracker.record_usage(
                model=self.model,
                input_tokens=result.metadata.get('tokens_processed', 0),
                output_tokens=500,  # Estimate
                cost=result.metadata['cost']
            )
        
        return result

# Use the tracked summarizer
tracked_summarizer = TrackedSummarizer(
    api_key=config.openai_api_key,
    cost_tracker=cost_tracker
)

# Process conversations
batch_processor = BatchConversationProcessor(tracked_summarizer)
report = batch_processor.process_conversations_sync(conversations)

# Generate cost report
cost_report = cost_tracker.get_cost_report(days=7)
print(f"Weekly cost: ${cost_report.total_cost:.2f}")
print(f"Projected monthly: ${cost_report.projected_monthly_cost:.2f}")

# Export detailed usage
cost_tracker.export_usage_csv(Path("chat_summary_costs.csv"))
```

## Storage and Search Integration

### Qdrant Storage Patterns for Chat Summaries

Extend the existing VectorStore base class to support chat summaries with specialized metadata:

```python
class ChatVectorStore(VectorStore):
    """Specialized vector store for chat summaries."""
    
    def __init__(self, url: str, collection_name: str, api_key: Optional[str] = None,
                 grpc_port: int = 6334, prefer_grpc: bool = False, timeout: int = 30):
        """Initialize with chat-specific configurations.
        
        Inherits all connection management from base VectorStore.
        """
        super().__init__(url, collection_name, api_key, grpc_port, prefer_grpc, timeout)
        
        # Chat-specific configurations
        self.chat_entity_type = "chat_history"
        self.chat_metadata_schema = {
            # Required fields
            "entity_type": self.chat_entity_type,
            "entity_name": str,  # Format: "chat_[timestamp]_[hash]"
            "conversation_id": str,
            "project_path": str,
            "summary": str,
            "category": str,  # One of 7 categories
            
            # Temporal metadata
            "start_time": str,  # ISO format
            "end_time": str,    # ISO format
            "duration_hours": float,
            "message_count": int,
            
            # Technical context
            "languages": list,  # ["python", "javascript"]
            "frameworks": list,  # ["fastapi", "react"]
            "tools": list,      # ["docker", "git"]
            "error_types": list,  # ["ImportError", "SyntaxError"]
            
            # Search optimization
            "key_insights": list,  # Extracted actionable insights
            "participants": list,  # ["human", "assistant"]
            "confidence": float,   # 0.0 - 1.0
            
            # Cross-reference fields
            "related_files": list,  # Files mentioned in chat
            "related_functions": list,  # Functions discussed
            "related_classes": list,    # Classes referenced
            
            # Processing metadata
            "indexed_at": str,     # When indexed
            "tokens_processed": int,
            "tokens_saved": int,   # Compression ratio
            "processing_cost": float,
            "model_used": str
        }
    
    def store_chat_summary(self, summary_result: SummaryResult, 
                          embedder: Embedder) -> str:
        """Store a chat summary as a vector point.
        
        Args:
            summary_result: Processed summary from ChatSummarizer
            embedder: Embedder instance for vector generation
            
        Returns:
            Point ID of stored summary
        """
        # Generate unique entity name
        timestamp = summary_result.metadata.get('start_time', datetime.now().isoformat())
        content_hash = hashlib.md5(summary_result.summary.encode()).hexdigest()[:8]
        entity_name = f"chat_{timestamp}_{content_hash}"
        
        # Prepare searchable content
        searchable_content = self._prepare_searchable_content(summary_result)
        
        # Generate embedding
        embedding = embedder.embed(searchable_content)
        
        # Prepare metadata payload
        metadata = {
            "entity_type": self.chat_entity_type,
            "entity_name": entity_name,
            "conversation_id": summary_result.metadata.get('conversation_id', ''),
            "project_path": summary_result.metadata.get('project_path', ''),
            "summary": summary_result.summary,
            "category": summary_result.category,
            
            # Temporal data
            "start_time": summary_result.metadata.get('start_time', ''),
            "end_time": summary_result.metadata.get('end_time', ''),
            "duration_hours": summary_result.metadata.get('duration_hours', 0),
            "message_count": summary_result.metadata.get('message_count', 0),
            
            # Technical context
            "languages": summary_result.technical_context.get('languages', []),
            "frameworks": summary_result.technical_context.get('frameworks', []),
            "tools": summary_result.technical_context.get('tools', []),
            "error_types": summary_result.technical_context.get('error_types', []),
            
            # Search optimization
            "key_insights": summary_result.key_insights,
            "participants": summary_result.metadata.get('participants', []),
            "confidence": summary_result.confidence,
            
            # Cross-references (extracted during summarization)
            "related_files": self._extract_file_references(summary_result),
            "related_functions": self._extract_function_references(summary_result),
            "related_classes": self._extract_class_references(summary_result),
            
            # Processing metadata
            "indexed_at": datetime.now().isoformat(),
            "tokens_processed": summary_result.metadata.get('tokens_processed', 0),
            "tokens_saved": summary_result.metadata.get('tokens_saved', 0),
            "processing_cost": summary_result.metadata.get('cost', 0),
            "model_used": summary_result.metadata.get('model', '')
        }
        
        # Create vector point
        point = VectorPoint(
            id=str(uuid.uuid4()),
            vector=embedding,
            payload=metadata
        )
        
        # Store using parent's optimized batch storage
        self._points_buffer.append(point)
        if len(self._points_buffer) >= self.batch_size:
            self.flush_points()
        
        return point.id
    
    def _prepare_searchable_content(self, summary_result: SummaryResult) -> str:
        """Prepare optimized content for embedding generation.
        
        Combines summary, insights, and technical context for rich semantic search.
        """
        parts = [
            f"Category: {summary_result.category}",
            f"Summary: {summary_result.summary}",
        ]
        
        if summary_result.key_insights:
            parts.append("Key Insights:")
            parts.extend(f"- {insight}" for insight in summary_result.key_insights)
        
        # Add technical context for better search
        tech_context = summary_result.technical_context
        if tech_context.get('languages'):
            parts.append(f"Languages: {', '.join(tech_context['languages'])}")
        if tech_context.get('frameworks'):
            parts.append(f"Frameworks: {', '.join(tech_context['frameworks'])}")
        if tech_context.get('error_types'):
            parts.append(f"Errors: {', '.join(tech_context['error_types'])}")
        
        return "\n".join(parts)
    
    def _extract_file_references(self, summary_result: SummaryResult) -> List[str]:
        """Extract file paths mentioned in the conversation."""
        # Look for common file patterns
        text = f"{summary_result.summary} {' '.join(summary_result.key_insights)}"
        
        # Common patterns: .py, .js, .ts, .md, etc.
        file_pattern = r'[\w/\-\\.]+\.(?:py|js|ts|jsx|tsx|md|json|yaml|yml|txt|sh)'
        files = re.findall(file_pattern, text)
        
        # Also look for module imports
        import_pattern = r'(?:from|import)\s+([\w\.]+)'
        imports = re.findall(import_pattern, text)
        
        return list(set(files + [f"{imp}.py" for imp in imports]))
```

### Metadata Structure for Searchability

Implement a comprehensive metadata schema that enables multi-dimensional search:

```python
class ChatMetadataBuilder:
    """Build rich metadata for chat summaries."""
    
    def __init__(self, project_context: ProjectContext):
        """Initialize with project context for cross-referencing.
        
        Args:
            project_context: Contains indexed code entities for the project
        """
        self.project_context = project_context
        self.entity_index = self._build_entity_index()
    
    def _build_entity_index(self) -> Dict[str, Set[str]]:
        """Build lookup index for code entities."""
        index = {
            'functions': set(),
            'classes': set(),
            'files': set(),
            'modules': set()
        }
        
        # Populate from project context
        for entity in self.project_context.entities:
            if entity.type == "FUNCTION":
                index['functions'].add(entity.name)
            elif entity.type == "CLASS":
                index['classes'].add(entity.name)
            elif entity.type == "FILE":
                index['files'].add(entity.path)
            elif entity.type == "MODULE":
                index['modules'].add(entity.name)
        
        return index
    
    def build_metadata(self, summary_result: SummaryResult, 
                      conversation: Conversation) -> Dict[str, Any]:
        """Build comprehensive metadata for storage.
        
        Returns:
            Dictionary with all metadata fields populated
        """
        # Extract temporal metadata
        temporal_meta = self._extract_temporal_metadata(conversation)
        
        # Extract technical context with validation
        tech_context = self._extract_technical_context(summary_result)
        
        # Find cross-references to code entities
        cross_refs = self._find_cross_references(summary_result)
        
        # Calculate search scores
        search_scores = self._calculate_search_scores(summary_result)
        
        # Build complete metadata
        metadata = {
            **temporal_meta,
            **tech_context,
            **cross_refs,
            **search_scores,
            
            # Core fields from summary
            "summary": summary_result.summary,
            "category": summary_result.category,
            "key_insights": summary_result.key_insights,
            "confidence": summary_result.confidence,
            
            # Additional searchability fields
            "search_keywords": self._extract_search_keywords(summary_result),
            "problem_keywords": self._extract_problem_keywords(summary_result),
            "solution_keywords": self._extract_solution_keywords(summary_result),
            
            # Faceted search support
            "facets": self._build_search_facets(summary_result, tech_context)
        }
        
        return metadata
    
    def _find_cross_references(self, summary_result: SummaryResult) -> Dict[str, List[str]]:
        """Find references to existing code entities."""
        text = f"{summary_result.summary} {' '.join(summary_result.key_insights)}"
        references = {
            "related_functions": [],
            "related_classes": [],
            "related_files": [],
            "related_modules": []
        }
        
        # Use word boundaries for accurate matching
        for func in self.entity_index['functions']:
            if re.search(rf'\b{re.escape(func)}\b', text):
                references["related_functions"].append(func)
        
        for cls in self.entity_index['classes']:
            if re.search(rf'\b{re.escape(cls)}\b', text):
                references["related_classes"].append(cls)
        
        # File references with path normalization
        for file_ref in self._extract_file_references(summary_result):
            normalized = os.path.normpath(file_ref)
            if any(normalized.endswith(f) for f in self.entity_index['files']):
                references["related_files"].append(normalized)
        
        return references
    
    def _calculate_search_scores(self, summary_result: SummaryResult) -> Dict[str, float]:
        """Calculate relevance scores for different search types."""
        scores = {
            "technical_score": 0.0,
            "problem_solving_score": 0.0,
            "implementation_score": 0.0,
            "learning_score": 0.0
        }
        
        # Technical score based on technical context richness
        tech_items = sum([
            len(summary_result.technical_context.get('languages', [])),
            len(summary_result.technical_context.get('frameworks', [])),
            len(summary_result.technical_context.get('tools', [])),
            len(summary_result.technical_context.get('error_types', []))
        ])
        scores["technical_score"] = min(tech_items / 10.0, 1.0)
        
        # Problem-solving score for debugging patterns
        if summary_result.category == "debugging_pattern":
            scores["problem_solving_score"] = 0.9
        elif "error" in summary_result.summary.lower() or "fix" in summary_result.summary.lower():
            scores["problem_solving_score"] = 0.7
        
        # Implementation score
        if summary_result.category == "implementation_pattern":
            scores["implementation_score"] = 0.9
        elif any(kw in summary_result.summary.lower() for kw in ['implement', 'create', 'build']):
            scores["implementation_score"] = 0.7
        
        # Learning score based on insights
        scores["learning_score"] = min(len(summary_result.key_insights) / 5.0, 1.0)
        
        return scores
    
    def _build_search_facets(self, summary_result: SummaryResult, 
                           tech_context: Dict) -> Dict[str, List[str]]:
        """Build facets for faceted search functionality."""
        facets = {
            "time_period": self._get_time_period_facet(summary_result.metadata),
            "complexity": self._get_complexity_facet(summary_result),
            "tech_stack": tech_context.get('languages', []) + tech_context.get('frameworks', []),
            "problem_type": self._get_problem_type_facet(summary_result),
            "solution_type": self._get_solution_type_facet(summary_result)
        }
        
        # Remove empty facets
        return {k: v for k, v in facets.items() if v}
```

### Cross-Reference with Code Entities

Implement bidirectional linking between chat summaries and code entities:

```python
class CrossReferenceManager:
    """Manage cross-references between chat summaries and code entities."""
    
    def __init__(self, vector_store: VectorStore):
        self.vector_store = vector_store
        self.reference_cache = {}
    
    def create_cross_references(self, chat_point_id: str, 
                              references: Dict[str, List[str]]):
        """Create bidirectional references between chat and code.
        
        Args:
            chat_point_id: ID of the chat summary point
            references: Dict with related_functions, related_classes, etc.
        """
        # Store references in chat point
        chat_update = {
            "cross_references": {
                "functions": references.get('related_functions', []),
                "classes": references.get('related_classes', []),
                "files": references.get('related_files', [])
            }
        }
        
        # Update chat point
        self.vector_store.set_payload(
            points=[chat_point_id],
            payload=chat_update
        )
        
        # Update referenced code entities to point back to chat
        for entity_type, entity_names in references.items():
            for entity_name in entity_names:
                self._add_chat_reference_to_entity(
                    entity_name, entity_type, chat_point_id
                )
    
    def _add_chat_reference_to_entity(self, entity_name: str, 
                                    entity_type: str, chat_point_id: str):
        """Add chat reference to a code entity."""
        # Find the code entity
        filter_condition = models.Filter(
            must=[
                models.FieldCondition(
                    key="entity_name",
                    match=models.MatchValue(value=entity_name)
                ),
                models.FieldCondition(
                    key="entity_type",
                    match=models.MatchValue(value=self._map_entity_type(entity_type))
                )
            ]
        )
        
        # Search for the entity
        results = self.vector_store.search(
            query_vector=[0] * 1536,  # Dummy vector for metadata search
            limit=1,
            query_filter=filter_condition
        )
        
        if results:
            entity_point = results[0]
            # Get existing chat references
            chat_refs = entity_point.payload.get('chat_references', [])
            
            # Add new reference if not already present
            if chat_point_id not in chat_refs:
                chat_refs.append(chat_point_id)
                
                # Update entity with chat reference
                self.vector_store.set_payload(
                    points=[entity_point.id],
                    payload={"chat_references": chat_refs}
                )
    
    def get_related_chats(self, entity_name: str, 
                         entity_type: str) -> List[ChatSummary]:
        """Get all chat summaries related to a code entity."""
        # First get the entity
        entity = self._get_entity(entity_name, entity_type)
        if not entity:
            return []
        
        # Get chat references
        chat_ids = entity.payload.get('chat_references', [])
        if not chat_ids:
            return []
        
        # Retrieve chat summaries
        chat_summaries = []
        for chat_id in chat_ids:
            chat_point = self.vector_store.retrieve([chat_id])[0]
            if chat_point:
                chat_summaries.append(self._point_to_chat_summary(chat_point))
        
        return chat_summaries
    
    def get_code_context_for_chat(self, chat_point_id: str) -> CodeContext:
        """Get all code entities referenced in a chat."""
        # Retrieve chat point
        chat_point = self.vector_store.retrieve([chat_point_id])[0]
        if not chat_point:
            return CodeContext()
        
        # Extract cross-references
        cross_refs = chat_point.payload.get('cross_references', {})
        
        context = CodeContext()
        
        # Retrieve each referenced entity
        for func_name in cross_refs.get('functions', []):
            func_entity = self._get_entity(func_name, 'function')
            if func_entity:
                context.functions.append(func_entity)
        
        for class_name in cross_refs.get('classes', []):
            class_entity = self._get_entity(class_name, 'class')
            if class_entity:
                context.classes.append(class_entity)
        
        for file_path in cross_refs.get('files', []):
            file_entity = self._get_entity(file_path, 'file')
            if file_entity:
                context.files.append(file_entity)
        
        return context
```

### Search Query Optimization

Implement advanced search capabilities optimized for chat summaries:

```python
class ChatSearchOptimizer:
    """Optimize search queries for chat summaries."""
    
    def __init__(self, vector_store: ChatVectorStore, embedder: Embedder):
        self.vector_store = vector_store
        self.embedder = embedder
        self.query_cache = LRUCache(maxsize=1000)
    
    def search(self, query: str, filters: Optional[SearchFilters] = None,
               limit: int = 10) -> List[SearchResult]:
        """Perform optimized search across chat summaries.
        
        Args:
            query: Natural language search query
            filters: Optional filters for time, category, etc.
            limit: Maximum results to return
            
        Returns:
            List of search results with relevance scores
        """
        # Check cache
        cache_key = f"{query}:{str(filters)}:{limit}"
        if cache_key in self.query_cache:
            return self.query_cache[cache_key]
        
        # Enhance query for chat context
        enhanced_query = self._enhance_query(query)
        
        # Generate embedding
        query_embedding = self.embedder.embed(enhanced_query)
        
        # Build filter conditions
        filter_conditions = self._build_filter_conditions(filters)
        
        # Perform hybrid search
        results = self._hybrid_search(
            query_embedding=query_embedding,
            text_query=query,
            filter_conditions=filter_conditions,
            limit=limit * 2  # Over-fetch for re-ranking
        )
        
        # Re-rank results
        ranked_results = self._rerank_results(results, query, limit)
        
        # Cache results
        self.query_cache[cache_key] = ranked_results
        
        return ranked_results
    
    def _enhance_query(self, query: str) -> str:
        """Enhance query with chat-specific context."""
        # Add category hints based on keywords
        category_hints = {
            'debug': 'debugging_pattern error troubleshooting fix',
            'implement': 'implementation_pattern create build develop',
            'integrate': 'integration_pattern API service connect',
            'config': 'configuration_pattern setup environment deploy',
            'design': 'architecture_pattern structure component',
            'optimize': 'performance_pattern speed memory efficiency',
            'learn': 'knowledge_insight research understand'
        }
        
        enhanced_parts = [query]
        
        for keyword, hint in category_hints.items():
            if keyword in query.lower():
                enhanced_parts.append(hint)
        
        return ' '.join(enhanced_parts)
    
    def _hybrid_search(self, query_embedding: List[float], text_query: str,
                      filter_conditions: Optional[models.Filter], 
                      limit: int) -> List[ScoredPoint]:
        """Perform hybrid vector + text search."""
        # Vector search
        vector_results = self.vector_store.search(
            query_vector=query_embedding,
            limit=limit,
            query_filter=filter_conditions
        )
        
        # Text search on key fields
        text_conditions = self._build_text_search_conditions(text_query)
        if text_conditions and filter_conditions:
            combined_filter = models.Filter(
                must=[filter_conditions, text_conditions]
            )
        else:
            combined_filter = text_conditions or filter_conditions
        
        text_results = self.vector_store.client.scroll(
            collection_name=self.vector_store.collection_name,
            scroll_filter=combined_filter,
            limit=limit,
            with_payload=True,
            with_vectors=False
        )[0]
        
        # Combine results
        all_results = {}
        
        # Add vector results with scores
        for result in vector_results:
            all_results[result.id] = {
                'point': result,
                'vector_score': result.score,
                'text_score': 0.0
            }
        
        # Add text results
        for point in text_results:
            if point.id in all_results:
                all_results[point.id]['text_score'] = 1.0
            else:
                all_results[point.id] = {
                    'point': point,
                    'vector_score': 0.0,
                    'text_score': 1.0
                }
        
        return all_results
    
    def _build_text_search_conditions(self, query: str) -> Optional[models.Filter]:
        """Build text search conditions for relevant fields."""
        # Extract meaningful terms
        terms = [term.lower() for term in query.split() if len(term) > 2]
        if not terms:
            return None
        
        # Search in multiple fields
        search_fields = ['summary', 'key_insights', 'search_keywords', 
                        'problem_keywords', 'solution_keywords']
        
        conditions = []
        for field in search_fields:
            for term in terms:
                conditions.append(
                    models.FieldCondition(
                        key=field,
                        match=models.MatchText(text=term)
                    )
                )
        
        # Use OR for text matching
        return models.Filter(should=conditions) if conditions else None
```

### Result Ranking and Filtering

Implement sophisticated ranking algorithms for optimal result ordering:

```python
class ChatResultRanker:
    """Rank and filter chat search results."""
    
    def __init__(self, weight_config: Optional[Dict[str, float]] = None):
        """Initialize with customizable weight configuration.
        
        Args:
            weight_config: Weights for different ranking factors
        """
        self.weights = weight_config or {
            'vector_similarity': 0.4,
            'text_relevance': 0.2,
            'recency': 0.15,
            'category_match': 0.1,
            'technical_relevance': 0.1,
            'cross_reference_bonus': 0.05
        }
    
    def rerank_results(self, results: Dict[str, Dict], query: str, 
                      user_context: Optional[UserContext] = None,
                      limit: int = 10) -> List[SearchResult]:
        """Rerank results based on multiple factors.
        
        Args:
            results: Raw search results with scores
            query: Original search query
            user_context: Optional user context for personalization
            limit: Maximum results to return
            
        Returns:
            Reranked and filtered results
        """
        ranked_results = []
        
        for point_id, result_data in results.items():
            point = result_data['point']
            
            # Calculate component scores
            scores = {
                'vector_similarity': result_data['vector_score'],
                'text_relevance': result_data['text_score'],
                'recency': self._calculate_recency_score(point),
                'category_match': self._calculate_category_match(point, query),
                'technical_relevance': self._calculate_technical_relevance(point, query),
                'cross_reference_bonus': self._calculate_cross_reference_bonus(point)
            }
            
            # Apply user context adjustments if available
            if user_context:
                scores = self._apply_user_context(scores, point, user_context)
            
            # Calculate final score
            final_score = sum(score * self.weights[factor] 
                            for factor, score in scores.items())
            
            # Create search result
            search_result = SearchResult(
                id=point_id,
                score=final_score,
                payload=point.payload,
                explanation=self._generate_explanation(scores),
                highlights=self._extract_highlights(point, query)
            )
            
            ranked_results.append(search_result)
        
        # Sort by final score
        ranked_results.sort(key=lambda x: x.score, reverse=True)
        
        # Apply filters and limit
        filtered_results = self._apply_post_filters(ranked_results)
        
        return filtered_results[:limit]
    
    def _calculate_recency_score(self, point: VectorPoint) -> float:
        """Calculate score based on recency of the chat."""
        try:
            indexed_at = datetime.fromisoformat(point.payload.get('indexed_at', ''))
            days_old = (datetime.now() - indexed_at).days
            
            # Exponential decay with half-life of 30 days
            return math.exp(-days_old / 30)
        except:
            return 0.5  # Default for missing dates
    
    def _calculate_category_match(self, point: VectorPoint, query: str) -> float:
        """Score based on category relevance to query."""
        category = point.payload.get('category', '')
        query_lower = query.lower()
        
        # Direct category keyword matches
        category_keywords = {
            'debugging_pattern': ['debug', 'error', 'fix', 'troubleshoot'],
            'implementation_pattern': ['implement', 'create', 'build', 'develop'],
            'integration_pattern': ['integrate', 'api', 'service', 'connect'],
            'configuration_pattern': ['config', 'setup', 'deploy', 'environment'],
            'architecture_pattern': ['design', 'architect', 'structure', 'pattern'],
            'performance_pattern': ['perform', 'optimize', 'speed', 'memory'],
            'knowledge_insight': ['learn', 'understand', 'research', 'insight']
        }
        
        if category in category_keywords:
            keywords = category_keywords[category]
            matches = sum(1 for kw in keywords if kw in query_lower)
            return min(matches / len(keywords), 1.0)
        
        return 0.0
    
    def _calculate_technical_relevance(self, point: VectorPoint, query: str) -> float:
        """Score based on technical context matching."""
        query_lower = query.lower()
        score = 0.0
        
        # Check language matches
        languages = point.payload.get('languages', [])
        for lang in languages:
            if lang.lower() in query_lower:
                score += 0.3
        
        # Check framework matches
        frameworks = point.payload.get('frameworks', [])
        for framework in frameworks:
            if framework.lower() in query_lower:
                score += 0.3
        
        # Check tool matches
        tools = point.payload.get('tools', [])
        for tool in tools:
            if tool.lower() in query_lower:
                score += 0.2
        
        # Check error type matches
        error_types = point.payload.get('error_types', [])
        for error in error_types:
            if error.lower() in query_lower:
                score += 0.2
        
        return min(score, 1.0)
    
    def _calculate_cross_reference_bonus(self, point: VectorPoint) -> float:
        """Bonus score for chats with code cross-references."""
        cross_refs = point.payload.get('cross_references', {})
        
        # Count total references
        total_refs = sum(len(refs) for refs in cross_refs.values())
        
        # Logarithmic scaling to avoid over-weighting
        if total_refs > 0:
            return min(math.log(total_refs + 1) / 10, 1.0)
        
        return 0.0
    
    def _extract_highlights(self, point: VectorPoint, query: str) -> List[str]:
        """Extract relevant snippets to highlight."""
        highlights = []
        query_terms = query.lower().split()
        
        # Check summary
        summary = point.payload.get('summary', '')
        for term in query_terms:
            if term in summary.lower():
                # Extract sentence containing term
                sentences = summary.split('.')
                for sentence in sentences:
                    if term in sentence.lower():
                        highlights.append(sentence.strip())
                        break
        
        # Check key insights
        insights = point.payload.get('key_insights', [])
        for insight in insights:
            if any(term in insight.lower() for term in query_terms):
                highlights.append(insight)
        
        return highlights[:3]  # Limit to 3 highlights
```

### Integration with Existing Search Commands

Extend the CLI to seamlessly integrate chat search with code search:

```python
class UnifiedSearchCommand:
    """Unified search across code and chat summaries."""
    
    def __init__(self, code_vector_store: VectorStore, 
                 chat_vector_store: ChatVectorStore,
                 embedder: Embedder):
        self.code_store = code_vector_store
        self.chat_store = chat_vector_store
        self.embedder = embedder
        self.code_searcher = CodeSearcher(code_vector_store, embedder)
        self.chat_searcher = ChatSearchOptimizer(chat_vector_store, embedder)
    
    def search(self, query: str, search_type: str = "all",
               filters: Optional[Dict] = None, limit: int = 20) -> SearchResults:
        """Perform unified search across code and chats.
        
        Args:
            query: Search query
            search_type: "all", "code", "chat", or "cross-reference"
            filters: Optional filters
            limit: Maximum results
            
        Returns:
            Combined search results
        """
        results = SearchResults()
        
        if search_type in ["all", "code"]:
            # Search code entities
            code_results = self.code_searcher.search(
                query=query,
                entity_types=filters.get('entity_types') if filters else None,
                limit=limit if search_type == "code" else limit // 2
            )
            results.code_results = code_results
        
        if search_type in ["all", "chat"]:
            # Search chat summaries
            chat_filters = SearchFilters(
                categories=filters.get('categories') if filters else None,
                time_range=filters.get('time_range') if filters else None,
                min_confidence=filters.get('min_confidence', 0.7)
            )
            
            chat_results = self.chat_searcher.search(
                query=query,
                filters=chat_filters,
                limit=limit if search_type == "chat" else limit // 2
            )
            results.chat_results = chat_results
        
        if search_type == "cross-reference":
            # Search for items with cross-references
            results = self._search_cross_references(query, filters, limit)
        
        # Merge and rank combined results if searching all
        if search_type == "all":
            results = self._merge_results(results, limit)
        
        return results
    
    def _search_cross_references(self, query: str, filters: Optional[Dict],
                               limit: int) -> SearchResults:
        """Search specifically for cross-referenced items."""
        # First find relevant code entities
        code_results = self.code_searcher.search(query, limit=limit)
        
        results = SearchResults()
        cross_ref_manager = CrossReferenceManager(self.chat_store)
        
        # For each code result, find related chats
        for code_result in code_results[:10]:  # Limit to avoid explosion
            entity_name = code_result.payload.get('entity_name')
            entity_type = code_result.payload.get('entity_type')
            
            related_chats = cross_ref_manager.get_related_chats(
                entity_name, entity_type
            )
            
            # Add to results with boosted scores
            for chat in related_chats:
                chat.score *= 1.5  # Boost cross-referenced items
                results.chat_results.append(chat)
        
        # Deduplicate and sort
        seen = set()
        unique_results = []
        for result in sorted(results.chat_results, 
                           key=lambda x: x.score, reverse=True):
            if result.id not in seen:
                seen.add(result.id)
                unique_results.append(result)
        
        results.chat_results = unique_results[:limit]
        return results
    
    def _merge_results(self, results: SearchResults, limit: int) -> SearchResults:
        """Merge code and chat results with intelligent ranking."""
        merged = []
        
        # Normalize scores across different result types
        for code_result in results.code_results:
            merged.append({
                'type': 'code',
                'result': code_result,
                'normalized_score': code_result.score * 0.6  # Slight bias to code
            })
        
        for chat_result in results.chat_results:
            merged.append({
                'type': 'chat',
                'result': chat_result,
                'normalized_score': chat_result.score * 0.4
            })
        
        # Sort by normalized score
        merged.sort(key=lambda x: x['normalized_score'], reverse=True)
        
        # Ensure diversity in top results
        diverse_results = self._ensure_diversity(merged, limit)
        
        # Rebuild SearchResults
        final_results = SearchResults()
        for item in diverse_results:
            if item['type'] == 'code':
                final_results.code_results.append(item['result'])
            else:
                final_results.chat_results.append(item['result'])
        
        return final_results
```

### CLI Integration Example

Update the CLI to support the new unified search capabilities:

```python
# In cli_full.py - add to search command
@app.command()
def search(
    query: str = typer.Argument(..., help="Search query"),
    project_path: Path = typer.Option(None, "--project", "-p"),
    collection_name: str = typer.Option(None, "--collection", "-c"),
    search_type: str = typer.Option(
        "all", "--type", "-t",
        help="Search type: all, code, chat, cross-reference"
    ),
    category: Optional[str] = typer.Option(
        None, "--category",
        help="Filter by category (for chat results)"
    ),
    entity_type: Optional[str] = typer.Option(
        None, "--entity-type",
        help="Filter by entity type (for code results)"
    ),
    limit: int = typer.Option(20, "--limit", "-l"),
    export_json: Optional[Path] = typer.Option(
        None, "--export-json",
        help="Export results to JSON file"
    )
):
    """Search across code and chat summaries."""
    # Setup stores
    config = IndexerConfig.from_file("settings.txt")
    embedder = OpenAIEmbedder(config.openai_api_key)
    
    code_store = VectorStore(
        url=config.qdrant_url,
        collection_name=collection_name,
        api_key=config.qdrant_api_key
    )
    
    chat_store = ChatVectorStore(
        url=config.qdrant_url,
        collection_name=collection_name,
        api_key=config.qdrant_api_key
    )
    
    # Build filters
    filters = {}
    if category:
        filters['categories'] = [category]
    if entity_type:
        filters['entity_types'] = [entity_type]
    
    # Perform search
    searcher = UnifiedSearchCommand(code_store, chat_store, embedder)
    results = searcher.search(
        query=query,
        search_type=search_type,
        filters=filters,
        limit=limit
    )
    
    # Display results
    console = Console()
    
    if results.code_results:
        console.print("\n[bold blue]Code Results:[/bold blue]")
        for i, result in enumerate(results.code_results, 1):
            console.print(f"\n{i}. {result.payload['entity_name']} "
                         f"({result.payload['entity_type']})")
            console.print(f"   Score: {result.score:.3f}")
            console.print(f"   File: {result.payload.get('file_path', 'N/A')}")
    
    if results.chat_results:
        console.print("\n[bold green]Chat Results:[/bold green]")
        for i, result in enumerate(results.chat_results, 1):
            console.print(f"\n{i}. {result.payload['category']} - "
                         f"{result.payload.get('start_time', 'N/A')}")
            console.print(f"   Score: {result.score:.3f}")
            console.print(f"   Summary: {result.payload['summary'][:200]}...")
            
            # Show highlights
            if result.highlights:
                console.print("   [italic]Highlights:[/italic]")
                for highlight in result.highlights:
                    console.print(f"   • {highlight}")
    
    # Export if requested
    if export_json:
        results_dict = {
            "query": query,
            "search_type": search_type,
            "timestamp": datetime.now().isoformat(),
            "code_results": [
                {"score": r.score, "payload": r.payload} 
                for r in results.code_results
            ],
            "chat_results": [
                {"score": r.score, "payload": r.payload, "highlights": r.highlights} 
                for r in results.chat_results
            ]
        }
        
        with open(export_json, 'w') as f:
            json.dump(results_dict, f, indent=2)
        
        console.print(f"\n[green]Results exported to {export_json}[/green]")
```

## Next Steps

1. Review and approve implementation plan
2. Set up development branch
3. Begin Phase 1 implementation
4. Weekly progress reviews
5. Incremental integration testing

This plan ensures the chat history feature integrates seamlessly with the existing Claude Code Memory Indexer while
maintaining code quality, performance, and user experience standards.