# Voyage-3-lite Integration Plan for Claude Memory Solution

## Executive Summary

This document outlines the complete implementation plan for integrating Voyage-3-lite embeddings into the existing claude-indexer and mcp-qdrant-memory architecture. The solution provides 85% cost reduction compared to OpenAI while delivering 10% better performance.

## Goals & Benefits

### Primary Goals
- **85% cost reduction** ($0.02/M vs OpenAI's $0.13/M tokens)
- **10% better accuracy** than OpenAI embeddings
- **3x smaller storage** (512 dims vs 1536)
- **Drop-in replacement** with minimal code changes
- **32K context window** (vs OpenAI's 8K)

### Expected Outcomes
- Monthly costs: ~$2 for 100K files (vs $13 with OpenAI)
- Better semantic search quality
- Faster vector operations due to smaller dimensions
- Reduced Qdrant storage requirements

## Architecture Overview

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Claude Code   │◄──►│  MCP Server      │◄──►│   Qdrant DB     │
│                 │    │  (Modified)      │    │   (512-dim)     │
└─────────────────┘    └──────────────────┘    └─────────────────┘
                                                        ▲
                       ┌────────────────┐               │
                       │ claude-indexer │───────────────┘
                       │ (Voyage API)   │        
                       └────────────────┘
```

## Implementation Plan

### Phase 1: Claude-Indexer Modifications (Day 1-2)

#### 1.1 Create Voyage Embedder Module

**File**: `claude_indexer/embeddings/voyage.py`

```python
from typing import List, Optional
import voyageai
from .base import Embedder, EmbeddingResult

class VoyageEmbedder(Embedder):
    """Voyage AI embedder with superior performance and lower costs."""
    
    MODELS = {
        "voyage-3": {
            "dimensions": 1024,
            "max_tokens": 32000,
            "cost_per_1k_tokens": 0.00006
        },
        "voyage-3-lite": {
            "dimensions": 512,
            "max_tokens": 32000,
            "cost_per_1k_tokens": 0.00002
        }
    }
    
    def __init__(self, api_key: str, model: str = "voyage-3-lite"):
        self.client = voyageai.Client(api_key=api_key)
        self.model = model
        self.model_info = self.MODELS[model]
        
    def embed(self, text: str) -> EmbeddingResult:
        """Generate embedding for single text."""
        result = self.client.embed(
            texts=[text],
            model=self.model,
            input_type="document"
        )
        
        # Calculate token count (Voyage provides this)
        token_count = result.total_tokens
        cost = (token_count / 1000) * self.model_info["cost_per_1k_tokens"]
        
        return EmbeddingResult(
            embedding=result.embeddings[0],
            model=self.model,
            total_tokens=token_count,
            embedding_tokens=token_count,
            total_cost=cost
        )
    
    def embed_batch(self, texts: List[str]) -> List[EmbeddingResult]:
        """Generate embeddings for multiple texts efficiently."""
        # Voyage supports up to 128 texts per batch
        batch_size = 128
        all_results = []
        
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            result = self.client.embed(
                texts=batch,
                model=self.model,
                input_type="document"
            )
            
            # Calculate costs
            token_count = result.total_tokens
            cost_per_text = (token_count / len(batch) / 1000) * self.model_info["cost_per_1k_tokens"]
            
            for j, embedding in enumerate(result.embeddings):
                all_results.append(EmbeddingResult(
                    embedding=embedding,
                    model=self.model,
                    total_tokens=token_count // len(batch),
                    embedding_tokens=token_count // len(batch),
                    total_cost=cost_per_text
                ))
        
        return all_results
    
    @property
    def dimension(self) -> int:
        """Return embedding dimension."""
        return self.model_info["dimensions"]
```

#### 1.2 Update Embeddings Registry

**File**: `claude_indexer/embeddings/registry.py`

```python
# Add to imports
from .voyage import VoyageEmbedder

# Update EMBEDDERS dictionary
EMBEDDERS = {
    "openai": OpenAIEmbedder,
    "voyage": VoyageEmbedder,  # New addition
    "local": LocalEmbedder,
    "dummy": DummyEmbedder,
}

def create_embedder(provider: str = "openai", **kwargs) -> Embedder:
    """Create embedder with support for Voyage."""
    embedder_class = EMBEDDERS.get(provider)
    if not embedder_class:
        raise ValueError(f"Unknown embedder provider: {provider}")
    
    return embedder_class(**kwargs)
```

#### 1.3 Configuration Updates

**File**: `claude_indexer/config.py`

```python
class IndexerConfig(BaseModel):
    # Existing fields...
    
    # Updated embedding configuration
    embedding_provider: str = Field(
        default="openai",
        description="Embedding provider: openai, voyage, local, or dummy"
    )
    voyage_api_key: Optional[str] = Field(
        default=None,
        description="Voyage AI API key"
    )
    voyage_model: str = Field(
        default="voyage-3-lite",
        description="Voyage model: voyage-3 or voyage-3-lite"
    )
    
    @validator("embedding_provider")
    def validate_provider(cls, v):
        valid_providers = ["openai", "voyage", "local", "dummy"]
        if v not in valid_providers:
            raise ValueError(f"Invalid provider: {v}. Must be one of {valid_providers}")
        return v
```

#### 1.4 Settings File Update

**File**: `settings.template.txt`

```ini
# Embedding Configuration
embedding_provider=voyage  # Options: openai, voyage, local, dummy

# API Keys
openai_api_key=  # Leave empty if using Voyage
voyage_api_key=your-voyage-api-key-here
qdrant_api_key=your-qdrant-api-key
qdrant_url=http://localhost:6333

# Voyage Settings
voyage_model=voyage-3-lite  # Options: voyage-3, voyage-3-lite
```

### Phase 2: MCP-Qdrant-Memory Modifications (Day 3-4)

#### 2.1 Create Voyage Provider

**File**: `mcp-qdrant-memory/src/providers/voyage.ts`

```typescript
import axios from 'axios';
import { EmbeddingProvider } from './types';

export class VoyageEmbeddingProvider implements EmbeddingProvider {
  private apiKey: string;
  private modelName: string;
  private baseUrl = 'https://api.voyageai.com/v1';
  
  constructor(apiKey: string, modelName: string = 'voyage-3-lite') {
    this.apiKey = apiKey;
    this.modelName = modelName;
  }
  
  async initialize(): Promise<void> {
    // Voyage doesn't require initialization
  }
  
  async embed(text: string): Promise<number[]> {
    const response = await axios.post(
      `${this.baseUrl}/embeddings`,
      {
        input: [text],
        model: this.modelName,
        input_type: 'document'
      },
      {
        headers: {
          'Authorization': `Bearer ${this.apiKey}`,
          'Content-Type': 'application/json'
        }
      }
    );
    
    return response.data.data[0].embedding;
  }
  
  async embedBatch(texts: string[]): Promise<number[][]> {
    // Voyage supports up to 128 texts per batch
    const batchSize = 128;
    const allEmbeddings: number[][] = [];
    
    for (let i = 0; i < texts.length; i += batchSize) {
      const batch = texts.slice(i, i + batchSize);
      const response = await axios.post(
        `${this.baseUrl}/embeddings`,
        {
          input: batch,
          model: this.modelName,
          input_type: 'document'
        },
        {
          headers: {
            'Authorization': `Bearer ${this.apiKey}`,
            'Content-Type': 'application/json'
          }
        }
      );
      
      allEmbeddings.push(...response.data.data.map((d: any) => d.embedding));
    }
    
    return allEmbeddings;
  }
  
  getDimensions(): number {
    return this.modelName === 'voyage-3' ? 1024 : 512;
  }
  
  getModelName(): string {
    return this.modelName;
  }
}
```

#### 2.2 Update Provider Factory

**File**: `mcp-qdrant-memory/src/providers/factory.ts`

```typescript
import { VoyageEmbeddingProvider } from './voyage';

export async function createEmbeddingProvider(
  config: EmbeddingProviderConfig
): Promise<EmbeddingProvider> {
  let provider: EmbeddingProvider;
  
  switch (config.provider) {
    case 'openai':
      // ... existing code ...
      break;
      
    case 'voyage':
      if (!config.apiKey) {
        throw new Error('Voyage API key required');
      }
      provider = new VoyageEmbeddingProvider(
        config.apiKey,
        config.modelName || 'voyage-3-lite'
      );
      break;
      
    case 'local':
      // ... existing code ...
      break;
      
    default:
      throw new Error(`Unknown provider: ${config.provider}`);
  }
  
  await provider.initialize();
  return provider;
}
```

#### 2.3 Configuration Schema Update

**File**: `mcp-qdrant-memory/src/config.ts`

```typescript
const envSchema = z.object({
  QDRANT_URL: z.string().default("http://localhost:6333"),
  QDRANT_API_KEY: z.string().optional(),
  QDRANT_COLLECTION_NAME: z.string(),
  
  // Embedding configuration
  EMBEDDING_PROVIDER: z.enum(['openai', 'voyage', 'local']).default('openai'),
  EMBEDDING_MODEL: z.string().optional(),
  
  // API Keys (provider-specific)
  OPENAI_API_KEY: z.string().optional(),
  VOYAGE_API_KEY: z.string().optional(),
});

export function validateConfig(env: any) {
  const config = envSchema.parse(env);
  
  // Provider-specific validation
  if (config.EMBEDDING_PROVIDER === 'openai' && !config.OPENAI_API_KEY) {
    throw new Error('OPENAI_API_KEY required when using OpenAI provider');
  }
  
  if (config.EMBEDDING_PROVIDER === 'voyage' && !config.VOYAGE_API_KEY) {
    throw new Error('VOYAGE_API_KEY required when using Voyage provider');
  }
  
  return config;
}
```

### Phase 3: Integration & Testing (Day 5)

#### 3.1 Test Script

**File**: `test_voyage_integration.py`

```python
#!/usr/bin/env python3
"""Test Voyage integration end-to-end."""

import os
from claude_indexer.embeddings.voyage import VoyageEmbedder
from claude_indexer.config import IndexerConfig
import numpy as np

def test_voyage_embeddings():
    """Test Voyage embeddings functionality."""
    
    # Initialize embedder
    voyage_key = os.getenv("VOYAGE_API_KEY")
    if not voyage_key:
        print("⚠️  Set VOYAGE_API_KEY environment variable")
        return
    
    embedder = VoyageEmbedder(api_key=voyage_key, model="voyage-3-lite")
    
    # Test single embedding
    print("Testing single embedding...")
    result = embedder.embed("def calculate_fibonacci(n): return fibonacci sequence")
    
    print(f"✓ Model: {result.model}")
    print(f"✓ Dimensions: {len(result.embedding)}")
    print(f"✓ Tokens: {result.total_tokens}")
    print(f"✓ Cost: ${result.total_cost:.6f}")
    
    # Test batch embedding
    print("\nTesting batch embedding...")
    test_texts = [
        "class UserAuthentication handles login",
        "async function fetchData from API",
        "def validate_email with regex pattern"
    ] * 10  # 30 texts
    
    results = embedder.embed_batch(test_texts)
    total_cost = sum(r.total_cost for r in results)
    
    print(f"✓ Batch size: {len(results)}")
    print(f"✓ Total cost: ${total_cost:.6f}")
    print(f"✓ Avg cost per text: ${total_cost/len(results):.6f}")
    
    # Test semantic similarity
    print("\nTesting semantic similarity...")
    texts = [
        ("authenticate user", "user login"),
        ("calculate sum", "add numbers"),
        ("fetch API data", "get server response")
    ]
    
    for text1, text2 in texts:
        emb1 = embedder.embed(text1).embedding
        emb2 = embedder.embed(text2).embedding
        
        similarity = np.dot(emb1, emb2) / (np.linalg.norm(emb1) * np.linalg.norm(emb2))
        print(f"{text1} <-> {text2}: {similarity:.3f}")

def test_full_indexing():
    """Test full project indexing with Voyage."""
    
    print("\nTesting full indexing...")
    os.system("""
        claude-indexer -p /path/to/test/project \
            -c test-voyage-512 \
            --embedding-provider voyage \
            --verbose
    """)

if __name__ == "__main__":
    test_voyage_embeddings()
    # test_full_indexing()  # Uncomment to test full indexing
```

#### 3.2 MCP Configuration Examples

**Development Setup:**
```json
{
  "mcpServers": {
    "project-memory-voyage": {
      "command": "node",
      "args": ["/path/to/mcp-qdrant-memory/dist/index.js"],
      "env": {
        "EMBEDDING_PROVIDER": "voyage",
        "EMBEDDING_MODEL": "voyage-3-lite",
        "VOYAGE_API_KEY": "your-voyage-api-key",
        "QDRANT_URL": "http://localhost:6333",
        "QDRANT_COLLECTION_NAME": "project-voyage-512"
      }
    }
  }
}
```

**Production Setup:**
```json
{
  "mcpServers": {
    "memory-voyage-prod": {
      "command": "node",
      "args": ["/path/to/mcp-qdrant-memory/dist/index.js"],
      "env": {
        "EMBEDDING_PROVIDER": "voyage",
        "EMBEDDING_MODEL": "voyage-3",
        "VOYAGE_API_KEY": "${VOYAGE_API_KEY}",
        "QDRANT_URL": "${QDRANT_URL}",
        "QDRANT_API_KEY": "${QDRANT_API_KEY}",
        "QDRANT_COLLECTION_NAME": "production-voyage-1024"
      }
    }
  }
}
```

### Phase 4: Migration Strategy (Day 6-7)

#### 4.1 Parallel Testing Approach

```bash
# 1. Create new Voyage collections alongside existing OpenAI
project-memory          # Existing OpenAI (1536-dim)
project-memory-voyage   # New Voyage (512-dim)

# 2. Index same project with both providers
claude-indexer -p /project -c project-memory         # OpenAI
claude-indexer -p /project -c project-memory-voyage  # Voyage

# 3. Compare search quality
claude-indexer search "authentication function" -c project-memory
claude-indexer search "authentication function" -c project-memory-voyage
```

#### 4.2 Quality Comparison Script

**File**: `compare_providers.py`

```python
#!/usr/bin/env python3
"""Compare search quality between OpenAI and Voyage."""

import time
from collections import defaultdict

def compare_search_quality(query: str, collections: List[str]):
    """Compare search results across collections."""
    results = {}
    timings = {}
    
    for collection in collections:
        start = time.time()
        # Run search
        result = run_search(query, collection)
        timings[collection] = time.time() - start
        results[collection] = result
    
    # Compare results
    print(f"\nQuery: {query}")
    print("-" * 50)
    
    for collection in collections:
        print(f"\n{collection}:")
        print(f"  Time: {timings[collection]:.3f}s")
        print(f"  Results: {len(results[collection])}")
        print(f"  Top match: {results[collection][0]['name'] if results[collection] else 'None'}")
        print(f"  Score: {results[collection][0]['score'] if results[collection] else 0:.3f}")
```

### Phase 5: Production Rollout (Week 2)

#### 5.1 Monitoring & Metrics

```python
# Add to IndexingResult for tracking
@dataclass
class IndexingResult:
    # ... existing fields ...
    embedding_provider: str = "openai"
    embedding_model: str = ""
    embedding_dimension: int = 1536
    provider_response_time: float = 0.0
```

#### 5.2 Cost Tracking Dashboard

```python
def display_cost_comparison(result: IndexingResult):
    """Display cost savings with Voyage."""
    if result.embedding_provider == "voyage":
        # Calculate savings vs OpenAI
        openai_cost = (result.total_tokens / 1000) * 0.00013  # OpenAI 3-large
        voyage_cost = result.total_cost
        savings = openai_cost - voyage_cost
        
        print(f"\n💰 [green]Cost Analysis:[/green]")
        print(f"   Voyage cost: ${voyage_cost:.6f}")
        print(f"   OpenAI cost would be: ${openai_cost:.6f}")
        print(f"   You saved: ${savings:.6f} ({(savings/openai_cost)*100:.1f}%)")
```

## Rollback Plan

If issues arise, rollback is simple:

```bash
# 1. Change environment variable
EMBEDDING_PROVIDER=openai  # Switch back

# 2. Use existing OpenAI collections
project-memory  # Still intact

# 3. No code changes needed
```

## Success Metrics

### Week 1 Goals
- ✅ Voyage integration complete
- ✅ Cost reduction verified (85% savings)
- ✅ Search quality improved (10% better)
- ✅ Response times acceptable (<100ms)

### Month 1 Goals  
- ✅ All projects migrated to Voyage
- ✅ $100+ monthly savings achieved
- ✅ User satisfaction maintained/improved
- ✅ Zero downtime during migration

## Conclusion

Voyage-3-lite provides the optimal balance of cost, performance, and ease of integration. The 5-minute implementation time and drop-in compatibility make it the clear choice over complex local embedding solutions while delivering superior results at 85% lower cost than OpenAI.

**Next Steps:**
1. Sign up for Voyage API key at https://www.voyageai.com
2. Run test script to verify integration
3. Start parallel testing with small project
4. Monitor quality metrics for 1 week
5. Full rollout if metrics are positive