# MCP-Qdrant-Memory Update Plan: Qdrant as Single Source of Truth

## Problem Summary

The mcp-qdrant-memory server maintains dual storage (JSON file + Qdrant vectors), but our indexer writes directly to Qdrant, bypassing the JSON file. This causes `read_graph` to return empty results while `search_similar` works correctly.

**Root Cause**: `read_graph` reads from the empty JSON file while all data exists in Qdrant (2930 vectors).

## Solution: Update read_graph to Query Qdrant Directly

### Implementation Steps

### 1. Add Scroll Method to QdrantPersistence

**File**: `src/persistence/qdrant.ts` (after line ~307)

```typescript
async scrollAll(): Promise<{ entities: Entity[], relations: Relation[] }> {
  await this.connect();
  if (!COLLECTION_NAME) {
    throw new Error("COLLECTION_NAME environment variable is required");
  }

  const entities: Entity[] = [];
  const relations: Relation[] = [];
  let offset: string | number | undefined = undefined;
  const limit = 100;

  do {
    const scrollResult = await this.client.scroll(COLLECTION_NAME, {
      limit,
      offset,
      with_payload: true,
      with_vector: false,
    });

    for (const point of scrollResult.points) {
      if (!point.payload) continue;
      const payload = point.payload as unknown as Payload;

      if (isEntity(payload)) {
        const { type, ...entity } = payload;
        entities.push(entity);
      } else if (isRelation(payload)) {
        const { type, ...relation } = payload;
        relations.push(relation);
      }
    }

    offset = scrollResult.next_page_offset;
  } while (offset !== null && offset !== undefined);

  return { entities, relations };
}
```

### 2. Update KnowledgeGraphManager

**File**: `src/index.ts` (update `getGraph` method ~line 150)

```typescript
async getGraph(useQdrant: boolean = false): Promise<KnowledgeGraph> {
  if (useQdrant) {
    try {
      return await this.qdrant.scrollAll();
    } catch (error) {
      console.error('Failed to read from Qdrant, falling back to JSON:', error);
      return this.graph;
    }
  }
  return this.graph;
}
```

### 3. Update read_graph Handler

**File**: `src/index.ts` (update case "read_graph" ~line 411)

```typescript
case "read_graph": {
  const useQdrant = request.params.arguments?.useQdrant !== false;
  const graph = await this.graphManager.getGraph(useQdrant);
  return {
    content: [
      {
        type: "text",
        text: JSON.stringify(graph, null, 2),
      },
    ],
  };
}
```

### 4. Update Tool Schema

**File**: `src/index.ts` (update read_graph tool definition ~line 323)

```typescript
{
  name: "read_graph",
  description: "Read the entire knowledge graph",
  inputSchema: {
    type: "object",
    properties: {
      useQdrant: {
        type: "boolean",
        description: "Read from Qdrant directly instead of JSON file (default: true)",
        default: true
      }
    }
  }
}
```

## Key Benefits

1. **Immediate Fix**: Resolves sync issue without breaking changes
2. **Backward Compatible**: JSON fallback preserves existing functionality
3. **Clean Architecture**: Qdrant becomes single source of truth
4. **Performance**: Efficient batch retrieval via scroll API
5. **Minimal Changes**: ~50 lines of code, no duplication

## Testing Plan

1. **Before Update**: Verify `read_graph` returns empty
2. **After Update**: Confirm `read_graph` returns 2930 vectors
3. **Fallback Test**: Disconnect Qdrant, verify JSON fallback works
4. **Performance**: Test with large collections (10k+ vectors)

## Migration Path

- **Phase 1**: Deploy with `useQdrant` defaulting to `true`
- **Phase 2**: Monitor and validate data consistency
- **Phase 3**: (Future) Remove JSON storage entirely

This solution elegantly fixes the immediate problem while setting up for a cleaner architecture where Qdrant is the sole storage backend.
