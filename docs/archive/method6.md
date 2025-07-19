# Method 6 Implementation Plan: Local Vector Database with Sentence-Transformers

## Executive Summary

This document outlines the complete implementation plan for Method 6 - Local Vector Database using sentence-transformers to replace OpenAI embeddings. The solution provides zero-cost semantic search with 90-95% accuracy retention while maintaining full compatibility with the existing claude-indexer and mcp-qdrant-memory architecture.

## Goals & Requirements

### Primary Goals
- **Eliminate OpenAI embedding costs** (100% cost reduction)
- **Maintain search quality** (90-95% of OpenAI accuracy)
- **Preserve existing architecture** (no breaking changes)
- **Enable offline operation** (no internet dependency)

### Technical Requirements
- Replace OpenAI embeddings with sentence-transformers
- Use Qdrant for unified vector storage (not SQLite)
- Maintain modularity with clean abstractions
- Support incremental migration path
- Zero code duplication

## Architecture Overview

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Claude Code   │◄──►│  MCP Server      │◄──►│   Qdrant DB     │
│                 │    │  (Modified)      │    │   (Vectors)     │
└─────────────────┘    └──────────────────┘    └─────────────────┘
                                                        ▲
                       ┌────────────────┐               │
                       │ claude-indexer │───────────────┘
                       │ (Modified)     │        Local Embeddings
                       └────────────────┘

Embedding Flow:
Text → SentenceTransformer → 384-dim vectors → Qdrant
```

## Part 1: Claude-Indexer Modifications

### 1.1 Create Local Embedder Module

**File**: `claude_indexer/embeddings/local.py`

```python
from typing import List, Optional, Dict, Any
from sentence_transformers import SentenceTransformer
import numpy as np
from .base import Embedder, EmbeddingResult

class LocalEmbedder(Embedder):
    """Local sentence-transformers embedder with zero API costs."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        self.model = None  # Lazy loading
        self._dimension = 384  # all-MiniLM-L6-v2 dimension

    def _load_model(self):
        """Lazy load the model on first use."""
        if self.model is None:
            self.model = SentenceTransformer(self.model_name)
            # Update dimension based on loaded model
            self._dimension = self.model.get_sentence_embedding_dimension()

    def embed(self, text: str) -> EmbeddingResult:
        """Generate embedding for single text."""
        self._load_model()

        # Generate embedding
        embedding = self.model.encode([text], show_progress_bar=False)[0]

        # Simulate token counting for compatibility
        estimated_tokens = len(text.split()) * 1.3

        return EmbeddingResult(
            embedding=embedding.tolist(),
            model=self.model_name,
            total_tokens=int(estimated_tokens),
            embedding_tokens=int(estimated_tokens),
            total_cost=0.0  # Free!
        )

    def embed_batch(self, texts: List[str]) -> List[EmbeddingResult]:
        """Generate embeddings for multiple texts efficiently."""
        self._load_model()

        # Batch encode for efficiency
        embeddings = self.model.encode(
            texts,
            batch_size=32,
            show_progress_bar=len(texts) > 100
        )

        results = []
        for text, embedding in zip(texts, embeddings):
            estimated_tokens = len(text.split()) * 1.3
            results.append(EmbeddingResult(
                embedding=embedding.tolist(),
                model=self.model_name,
                total_tokens=int(estimated_tokens),
                embedding_tokens=int(estimated_tokens),
                total_cost=0.0
            ))

        return results

    @property
    def dimension(self) -> int:
        """Return embedding dimension."""
        return self._dimension
```

### 1.2 Update Embeddings Registry

**File**: `claude_indexer/embeddings/registry.py`

```python
# Add to imports
from .local import LocalEmbedder

# Update EMBEDDERS dictionary
EMBEDDERS = {
    "openai": OpenAIEmbedder,
    "dummy": DummyEmbedder,
    "local": LocalEmbedder,  # New addition
}

def create_embedder(provider: str = "openai", **kwargs) -> Embedder:
    """Create embedder with automatic fallback to local if no API key."""
    if provider == "openai" and not kwargs.get("api_key"):
        # Auto-fallback to local embeddings
        print("No OpenAI API key found, using local embeddings (free)")
        provider = "local"

    embedder_class = EMBEDDERS.get(provider)
    if not embedder_class:
        raise ValueError(f"Unknown embedder provider: {provider}")

    return embedder_class(**kwargs)
```

### 1.3 Configuration Updates

**File**: `claude_indexer/config.py`

```python
class IndexerConfig(BaseModel):
    # Existing fields...

    # New embedding configuration
    embedding_provider: str = Field(
        default="openai",
        description="Embedding provider: openai, local, or dummy"
    )
    local_model_name: str = Field(
        default="all-MiniLM-L6-v2",
        description="Model name for local embeddings"
    )

    @validator("embedding_provider")
    def validate_provider(cls, v):
        valid_providers = ["openai", "local", "dummy"]
        if v not in valid_providers:
            raise ValueError(f"Invalid provider: {v}. Must be one of {valid_providers}")
        return v
```

### 1.4 Settings File Update

**File**: `settings.template.txt`

```ini
# Embedding Configuration
embedding_provider=local  # Options: openai, local, dummy
local_model_name=all-MiniLM-L6-v2

# API Keys (optional for local embeddings)
openai_api_key=  # Leave empty for local embeddings
qdrant_api_key=your-qdrant-api-key
qdrant_url=http://localhost:6333
```

### 1.5 CLI Display Updates

**File**: `claude_indexer/cli_full.py`

```python
# Update display_results function
def display_results(result: IndexingResult, verbose: bool = False):
    """Display indexing results with provider info."""
    # ... existing code ...

    # Show embedding provider info
    if result.embedding_provider == "local":
        print(f"\n📊 [green]Embedding Info:[/green]")
        print(f"   Provider: Local ({result.embedding_model})")
        print(f"   Cost: $0.00 (Free)")
        print(f"   Dimensions: {result.embedding_dimension}")
    elif result.total_tokens > 0:
        # Existing OpenAI cost display
        print(f"\n💰 [green]Cost Tracking:[/green]")
        # ... existing cost display ...
```

## Part 2: MCP-Qdrant-Memory Modifications

### 2.1 Create Embedding Abstraction

**File**: `mcp-qdrant-memory/src/providers/types.ts`

```typescript
export interface EmbeddingProvider {
  /**
   * Generate embedding for a single text
   */
  embed(text: string): Promise<number[]>;

  /**
   * Generate embeddings for multiple texts (batch)
   */
  embedBatch(texts: string[]): Promise<number[][]>;

  /**
   * Get the dimension of embeddings produced
   */
  getDimensions(): number;

  /**
   * Get the model name for logging
   */
  getModelName(): string;

  /**
   * Initialize the provider (load models, etc)
   */
  initialize(): Promise<void>;
}

export interface EmbeddingProviderConfig {
  provider: 'openai' | 'local';
  modelName?: string;
  apiKey?: string;
}
```

### 2.2 OpenAI Provider Implementation

**File**: `mcp-qdrant-memory/src/providers/openai.ts`

```typescript
import OpenAI from 'openai';
import { EmbeddingProvider } from './types';

export class OpenAIEmbeddingProvider implements EmbeddingProvider {
  private client: OpenAI;
  private modelName: string;

  constructor(apiKey: string, modelName: string = 'text-embedding-ada-002') {
    this.client = new OpenAI({ apiKey });
    this.modelName = modelName;
  }

  async initialize(): Promise<void> {
    // No initialization needed for OpenAI
  }

  async embed(text: string): Promise<number[]> {
    const response = await this.client.embeddings.create({
      model: this.modelName,
      input: text,
    });
    return response.data[0].embedding;
  }

  async embedBatch(texts: string[]): Promise<number[][]> {
    const response = await this.client.embeddings.create({
      model: this.modelName,
      input: texts,
    });
    return response.data.map(d => d.embedding);
  }

  getDimensions(): number {
    return 1536; // ada-002 dimension
  }

  getModelName(): string {
    return this.modelName;
  }
}
```

### 2.3 Local Provider Implementation

**File**: `mcp-qdrant-memory/src/providers/local.ts`

```typescript
import { pipeline } from '@xenova/transformers';
import { EmbeddingProvider } from './types';

export class LocalEmbeddingProvider implements EmbeddingProvider {
  private modelName: string;
  private extractor: any;
  private dimensions: number;

  constructor(modelName: string = 'Xenova/all-MiniLM-L6-v2') {
    this.modelName = modelName;
    this.dimensions = 384; // Default for MiniLM
  }

  async initialize(): Promise<void> {
    // Load the model
    this.extractor = await pipeline(
      'feature-extraction',
      this.modelName,
      { quantized: true }  // Use quantized model for efficiency
    );
  }

  async embed(text: string): Promise<number[]> {
    const output = await this.extractor(text, {
      pooling: 'mean',
      normalize: true
    });
    return Array.from(output.data);
  }

  async embedBatch(texts: string[]): Promise<number[][]> {
    // Process in parallel for efficiency
    const promises = texts.map(text => this.embed(text));
    return Promise.all(promises);
  }

  getDimensions(): number {
    return this.dimensions;
  }

  getModelName(): string {
    return this.modelName;
  }
}
```

### 2.4 Provider Factory

**File**: `mcp-qdrant-memory/src/providers/factory.ts`

```typescript
import { EmbeddingProvider, EmbeddingProviderConfig } from './types';
import { OpenAIEmbeddingProvider } from './openai';
import { LocalEmbeddingProvider } from './local';

export async function createEmbeddingProvider(
  config: EmbeddingProviderConfig
): Promise<EmbeddingProvider> {
  let provider: EmbeddingProvider;

  switch (config.provider) {
    case 'openai':
      if (!config.apiKey) {
        throw new Error('OpenAI API key required');
      }
      provider = new OpenAIEmbeddingProvider(
        config.apiKey,
        config.modelName
      );
      break;

    case 'local':
      provider = new LocalEmbeddingProvider(config.modelName);
      break;

    default:
      throw new Error(`Unknown provider: ${config.provider}`);
  }

  await provider.initialize();
  return provider;
}
```

### 2.5 Update QdrantPersistence

**File**: `mcp-qdrant-memory/src/qdrant.ts`

```typescript
import { EmbeddingProvider } from './providers/types';
import { createEmbeddingProvider } from './providers/factory';

export class QdrantPersistence {
  private embeddingProvider: EmbeddingProvider;

  constructor(config: QdrantConfig) {
    // ... existing Qdrant setup ...
  }

  async initialize() {
    // Create embedding provider based on config
    this.embeddingProvider = await createEmbeddingProvider({
      provider: process.env.EMBEDDING_PROVIDER as 'openai' | 'local' || 'openai',
      modelName: process.env.EMBEDDING_MODEL,
      apiKey: process.env.OPENAI_API_KEY,
    });

    // Update collection creation with dynamic dimensions
    const requiredVectorSize = this.embeddingProvider.getDimensions();

    // ... rest of initialization ...
  }

  private async generateEmbedding(text: string): Promise<number[]> {
    try {
      return await this.embeddingProvider.embed(text);
    } catch (error) {
      console.error('Embedding generation failed:', error);
      throw error;
    }
  }
}
```

### 2.6 Configuration Updates

**File**: `mcp-qdrant-memory/src/config.ts`

```typescript
// Update environment validation
const envSchema = z.object({
  QDRANT_URL: z.string().default("http://localhost:6333"),
  QDRANT_API_KEY: z.string().optional(),
  QDRANT_COLLECTION_NAME: z.string(),

  // Embedding configuration
  EMBEDDING_PROVIDER: z.enum(['openai', 'local']).default('openai'),
  EMBEDDING_MODEL: z.string().optional(),

  // OpenAI (optional for local embeddings)
  OPENAI_API_KEY: z.string().optional(),
});

// Validate based on provider
export function validateConfig(env: any) {
  const config = envSchema.parse(env);

  // Only require OpenAI key if using OpenAI provider
  if (config.EMBEDDING_PROVIDER === 'openai' && !config.OPENAI_API_KEY) {
    throw new Error('OPENAI_API_KEY required when using OpenAI provider');
  }

  return config;
}
```

## Part 3: Integration & Testing

### 3.1 Package Dependencies

**claude-indexer requirements.txt**:
```txt
sentence-transformers>=2.2.0
torch>=2.0.0  # CPU version by default
numpy>=1.24.0
```

**mcp-qdrant-memory package.json**:
```json
{
  "dependencies": {
    "@xenova/transformers": "^2.6.0",
    "onnxruntime-node": "^1.16.0"
  }
}
```

### 3.2 Testing Script

**File**: `test_local_embeddings.py`

```python
#!/usr/bin/env python3
"""Test local embeddings implementation."""

import time
import numpy as np
from claude_indexer.embeddings.local import LocalEmbedder
from claude_indexer.embeddings.openai import OpenAIEmbedder

def test_embedding_quality():
    """Compare local vs OpenAI embeddings."""

    # Test texts
    test_texts = [
        "def calculate_fibonacci(n): return fibonacci sequence",
        "class UserAuthentication handles login and logout",
        "async function fetchDataFromAPI(endpoint) { ... }",
    ]

    # Initialize embedders
    local = LocalEmbedder()
    # openai = OpenAIEmbedder(api_key="...")  # Optional comparison

    # Test local embeddings
    print("Testing local embeddings...")
    start = time.time()

    for text in test_texts:
        result = local.embed(text)
        print(f"Text: {text[:50]}...")
        print(f"Dimension: {len(result.embedding)}")
        print(f"Cost: ${result.total_cost}")
        print(f"Time: {time.time() - start:.2f}s\n")

    # Test batch processing
    print("\nTesting batch processing...")
    start = time.time()
    results = local.embed_batch(test_texts * 10)
    print(f"Batch of {len(results)} embeddings in {time.time() - start:.2f}s")

    # Test semantic similarity
    print("\nTesting semantic similarity...")
    similar_texts = [
        ("function add(a, b)", "def sum(x, y)"),
        ("class Car", "class Vehicle"),
        ("error handling", "exception management"),
    ]

    for text1, text2 in similar_texts:
        emb1 = local.embed(text1).embedding
        emb2 = local.embed(text2).embedding

        # Cosine similarity
        similarity = np.dot(emb1, emb2) / (np.linalg.norm(emb1) * np.linalg.norm(emb2))
        print(f"{text1} <-> {text2}: {similarity:.3f}")

if __name__ == "__main__":
    test_embedding_quality()
```

### 3.3 Integration Test Commands

```bash
# Test claude-indexer with local embeddings
claude-indexer -p /path/to/project -c test-local-384 \
  --embedding-provider local \
  --verbose

# Test MCP server with local embeddings
EMBEDDING_PROVIDER=local \
EMBEDDING_MODEL=Xenova/all-MiniLM-L6-v2 \
QDRANT_URL=http://localhost:6333 \
QDRANT_COLLECTION_NAME=test-local \
node dist/index.js

# Compare search results
claude-indexer search "authentication function" -c test-local-384
```

## Part 4: Migration Strategy

### 4.1 Parallel Collection Approach

```bash
# 1. Keep existing OpenAI collections
project-memory       # 1536-dim OpenAI vectors
project-memory-local # 384-dim local vectors

# 2. Index with both providers
claude-indexer -p /project -c project-memory        # OpenAI
claude-indexer -p /project -c project-memory-local  # Local

# 3. Compare search quality
# 4. Switch MCP configuration when satisfied
```

### 4.2 MCP Configuration Migration

**Before** (OpenAI):
```json
{
  "project-memory": {
    "command": "node",
    "args": ["dist/index.js"],
    "env": {
      "EMBEDDING_PROVIDER": "openai",
      "OPENAI_API_KEY": "sk-...",
      "QDRANT_COLLECTION_NAME": "project-memory"
    }
  }
}
```

**After** (Local):
```json
{
  "project-memory": {
    "command": "node",
    "args": ["dist/index.js"],
    "env": {
      "EMBEDDING_PROVIDER": "local",
      "EMBEDDING_MODEL": "Xenova/all-MiniLM-L6-v2",
      "QDRANT_COLLECTION_NAME": "project-memory-local"
    }
  }
}
```

### 4.3 Rollback Plan

1. Keep OpenAI collections intact
2. Switch back via environment variable
3. No code changes required
4. Instant rollback capability

## Part 5: Performance Optimization

### 5.1 Model Caching

```python
# Global model cache for claude-indexer
_model_cache = {}

def get_cached_model(model_name: str):
    if model_name not in _model_cache:
        _model_cache[model_name] = SentenceTransformer(model_name)
    return _model_cache[model_name]
```

### 5.2 GPU Acceleration

```python
# Auto-detect GPU availability
import torch

device = "cuda" if torch.cuda.is_available() else "cpu"
model = SentenceTransformer(model_name, device=device)
```

### 5.3 Batch Size Tuning

```python
# Optimize batch size based on available memory
def get_optimal_batch_size():
    if torch.cuda.is_available():
        return 64  # GPU can handle larger batches
    else:
        return 32  # CPU-friendly batch size
```

## Part 6: Monitoring & Metrics

### 6.1 Performance Tracking

```python
# Add to IndexingResult
@dataclass
class IndexingResult:
    # ... existing fields ...
    embedding_time: float = 0.0
    embedding_provider: str = "openai"
    embedding_dimension: int = 1536
```

### 6.2 Quality Metrics

```python
def calculate_search_metrics(query: str, results: List[SearchResult]):
    """Track search quality metrics."""
    metrics = {
        "query": query,
        "result_count": len(results),
        "avg_similarity": np.mean([r.similarity for r in results]),
        "provider": "local",
        "model": "all-MiniLM-L6-v2",
    }
    return metrics
```

## Part 7: Documentation Updates

### 7.1 README.md Addition

```markdown
## Embedding Providers

Claude-indexer supports multiple embedding providers:

- **OpenAI** (default): High accuracy, requires API key
- **Local**: Free sentence-transformers, 90-95% accuracy
- **Dummy**: For testing without embeddings

### Using Local Embeddings (Free)

```bash
# Set in settings.txt
embedding_provider=local

# Or via command line
claude-indexer -p /project -c my-collection --embedding-provider local
```

Benefits:
- ✅ Zero API costs
- ✅ Works offline
- ✅ Privacy-friendly
- ✅ 90-95% search accuracy
```

### 7.2 CLAUDE.md Update

Add to configuration section:
```markdown
### Local Embeddings Configuration

For zero-cost operation, configure local embeddings:

1. **claude-indexer**: Set `embedding_provider=local` in settings.txt
2. **mcp-qdrant-memory**: Set `EMBEDDING_PROVIDER=local` environment variable
3. **Collection naming**: Add dimension suffix (e.g., `project-384` for local)

Performance characteristics:
- First run: ~30s model download
- Subsequent runs: ~2s model loading
- Embedding speed: ~100-200 texts/second (CPU)
- Memory usage: ~500MB
```

## Implementation Timeline

### Week 1: Claude-Indexer Implementation
- Day 1-2: Implement LocalEmbedder class
- Day 3-4: Update registry and configuration
- Day 5: Testing and optimization

### Week 2: MCP-Qdrant-Memory Modifications
- Day 1-2: Create embedding abstraction
- Day 3-4: Implement providers
- Day 5: Integration testing

### Week 3: Integration & Testing
- Day 1-2: End-to-end testing
- Day 3-4: Performance optimization
- Day 5: Documentation

### Week 4: Migration & Deployment
- Day 1-2: Parallel collection setup
- Day 3-4: A/B testing
- Day 5: Production rollout

## Success Criteria

✅ **Cost Reduction**: $0 embedding costs
✅ **Search Quality**: >90% accuracy vs OpenAI
✅ **Performance**: <2s for typical file embedding
✅ **Compatibility**: No breaking changes
✅ **Offline Operation**: Full functionality without internet

## Conclusion

This implementation provides a complete zero-cost alternative to OpenAI embeddings while maintaining the successful architecture of claude-indexer and mcp-qdrant-memory. The modular design allows for easy rollback and supports hybrid deployments where some collections use OpenAI and others use local embeddings.
