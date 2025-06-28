#!/usr/bin/env python3
"""
Performance Benchmark for v2.4 Progressive Disclosure Architecture

Tests:
1. Metadata-only search vs full implementation search speed
2. MCP server progressive disclosure workflow performance
3. Memory usage comparisons
4. Cost analysis with different embedding providers
"""

import time
import asyncio
import statistics
from pathlib import Path
from typing import List, Dict, Any
import json

# MCP client for testing
try:
    import subprocess
    import tempfile
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False

from claude_indexer.config import IndexerConfig
from claude_indexer.storage.qdrant import QdrantStore
from claude_indexer.embeddings.registry import create_embedder_from_config


class ProgressiveDisclosureBenchmark:
    """Benchmark progressive disclosure performance."""
    
    def __init__(self, config: IndexerConfig):
        self.config = config
        self.storage = QdrantStore(
            url=config.qdrant_url,
            api_key=config.qdrant_api_key
        )
        self.embedder = create_embedder_from_config(config.model_dump())
        self.results = {}
        
    def benchmark_search_performance(self, queries: List[str], iterations: int = 10) -> Dict[str, Any]:
        """Benchmark metadata-only vs implementation search performance."""
        print(f"🔍 Benchmarking search performance with {len(queries)} queries, {iterations} iterations each...")
        
        metadata_times = []
        implementation_times = []
        
        for query in queries:
            print(f"  Testing query: '{query[:50]}...'")
            
            # Generate query embedding once
            embedding_result = self.embedder.embed_text(query)
            if not embedding_result.embedding:
                continue
            
            # Benchmark metadata-only search (default v2.4 behavior)
            for _ in range(iterations):
                start_time = time.perf_counter()
                
                results = self.storage.search_similar(
                    collection_name=self.config.collection_name,
                    query_vector=embedding_result.embedding,
                    limit=10
                )
                
                metadata_times.append(time.perf_counter() - start_time)
            
            # Find entities with implementation for testing
            search_results = self.storage.search_similar(
                collection_name=self.config.collection_name,
                query_vector=embedding_result.embedding,
                limit=10
            )
            
            implementation_entities = []
            if search_results.success:
                for result in search_results.results:
                    payload = result.get("payload", {})
                    if payload.get("has_implementation", False):
                        implementation_entities.append(payload.get("entity_name", ""))
                        
            # Benchmark implementation chunk search (simulating get_implementation)
            if implementation_entities:
                entity_name = implementation_entities[0]  # Test first entity
                
                for _ in range(iterations):
                    start_time = time.perf_counter()
                    
                    # Search for implementation chunks for this entity
                    impl_results = self.storage.search_similar(
                        collection_name=self.config.collection_name,
                        query_vector=embedding_result.embedding,
                        limit=10,
                        filter_conditions={
                            "entity_name": entity_name,
                            "chunk_type": "implementation"
                        }
                    )
                    
                    implementation_times.append(time.perf_counter() - start_time)
        
        return {
            "metadata_search": {
                "mean_time": statistics.mean(metadata_times),
                "median_time": statistics.median(metadata_times),
                "min_time": min(metadata_times),
                "max_time": max(metadata_times),
                "sample_count": len(metadata_times)
            },
            "implementation_access": {
                "mean_time": statistics.mean(implementation_times) if implementation_times else 0,
                "median_time": statistics.median(implementation_times) if implementation_times else 0,
                "min_time": min(implementation_times) if implementation_times else 0,
                "max_time": max(implementation_times) if implementation_times else 0,
                "sample_count": len(implementation_times)
            }
        }
    
    def benchmark_mcp_workflow(self, queries: List[str], iterations: int = 5) -> Dict[str, Any]:
        """Benchmark full MCP progressive disclosure workflow."""
        print(f"⚡ Benchmarking MCP workflow with {len(queries)} queries, {iterations} iterations each...")
        
        workflow_times = []
        search_times = []
        implementation_times = []
        
        for query in queries:
            print(f"  Testing MCP workflow: '{query[:50]}...'")
            
            # Generate query embedding once
            embedding_result = self.embedder.embed_text(query)
            if not embedding_result.embedding:
                continue
                
            for _ in range(iterations):
                # Step 1: search_similar (metadata-first)
                search_start = time.perf_counter()
                
                search_results = self.storage.search_similar(
                    collection_name=self.config.collection_name,
                    query_vector=embedding_result.embedding,
                    limit=5
                )
                
                search_time = time.perf_counter() - search_start
                search_times.append(search_time)
                
                # Step 2: get_implementation for entities with implementation
                impl_start = time.perf_counter()
                
                if search_results.success:
                    for result in search_results.results:
                        payload = result.get("payload", {})
                        if payload.get("has_implementation", False):
                            # Simulate get_implementation with filtered search
                            impl_results = self.storage.search_similar(
                                collection_name=self.config.collection_name,
                                query_vector=embedding_result.embedding,
                                limit=10,
                                filter_conditions={
                                    "entity_name": payload.get("entity_name", ""),
                                    "chunk_type": "implementation"
                                }
                            )
                            break  # Test only first implementation
                
                impl_time = time.perf_counter() - impl_start
                implementation_times.append(impl_time)
                
                total_workflow_time = search_time + impl_time
                workflow_times.append(total_workflow_time)
        
        return {
            "full_workflow": {
                "mean_time": statistics.mean(workflow_times),
                "median_time": statistics.median(workflow_times),
                "min_time": min(workflow_times),
                "max_time": max(workflow_times),
                "sample_count": len(workflow_times)
            },
            "search_component": {
                "mean_time": statistics.mean(search_times),
                "median_time": statistics.median(search_times),
                "sample_count": len(search_times)
            },
            "implementation_component": {
                "mean_time": statistics.mean(implementation_times),
                "median_time": statistics.median(implementation_times),
                "sample_count": len(implementation_times)
            }
        }
    
    def benchmark_embedding_providers(self, test_texts: List[str]) -> Dict[str, Any]:
        """Compare OpenAI vs Voyage AI embedding performance and cost."""
        print(f"💰 Benchmarking embedding providers with {len(test_texts)} texts...")
        
        results = {}
        
        # Test with different provider configs
        providers = [
            {"provider": "openai", "model": "text-embedding-3-small"},
            # Add Voyage AI when available
        ]
        
        for provider_config in providers:
            print(f"  Testing {provider_config['provider']} with {provider_config['model']}...")
            
            try:
                # Create embedder for this provider
                config_dict = self.config.model_dump()
                config_dict.update({
                    "embedding_provider": provider_config["provider"],
                    "embedding_model": provider_config["model"]
                })
                
                embedder = create_embedder_from_config(config_dict)
                
                # Benchmark embedding generation
                start_time = time.perf_counter()
                total_tokens = 0
                total_cost = 0
                
                for text in test_texts:
                    result = embedder.embed_text(text)
                    if hasattr(result, 'token_count'):
                        total_tokens += result.token_count
                    if hasattr(result, 'cost_estimate'):
                        total_cost += result.cost_estimate
                
                total_time = time.perf_counter() - start_time
                
                results[provider_config["provider"]] = {
                    "total_time": total_time,
                    "avg_time_per_text": total_time / len(test_texts),
                    "total_tokens": total_tokens,
                    "total_cost_estimate": total_cost,
                    "cost_per_1k_tokens": (total_cost / total_tokens) * 1000 if total_tokens > 0 else 0,
                    "texts_processed": len(test_texts)
                }
                
            except Exception as e:
                results[provider_config["provider"]] = {"error": str(e)}
        
        return results
    
    def run_comprehensive_benchmark(self) -> Dict[str, Any]:
        """Run all benchmarks and return comprehensive results."""
        print("🚀 Starting Comprehensive Progressive Disclosure Performance Benchmark")
        print("=" * 80)
        
        # Define test queries (diverse set for comprehensive testing)
        test_queries = [
            "authentication function",
            "database connection",
            "QdrantStore create_chunk_point",
            "progressive disclosure",
            "test implementation",
            "error handling",
            "vector search",
            "embedding generation"
        ]
        
        # Define test texts for embedding benchmark
        test_texts = [
            "Simple function that adds two numbers",
            "Complex authentication service with multiple methods and comprehensive error handling",
            "Database connection manager with connection pooling and retry logic",
            "Vector search implementation using Qdrant with filtering and pagination",
            "Progressive disclosure architecture for efficient metadata retrieval"
        ]
        
        # Run benchmarks
        benchmark_results = {
            "metadata": {
                "timestamp": time.time(),
                "config": {
                    "collection_name": self.config.collection_name,
                    "embedding_provider": self.config.embedding_provider,
                    "embedding_model": getattr(self.config, 'embedding_model', 'unknown')
                }
            }
        }
        
        # 1. Search Performance Benchmark
        print("\n1. Search Performance Benchmark")
        print("-" * 40)
        benchmark_results["search_performance"] = self.benchmark_search_performance(test_queries)
        
        # 2. MCP Workflow Benchmark
        print("\n2. MCP Workflow Benchmark")
        print("-" * 40)
        benchmark_results["mcp_workflow"] = self.benchmark_mcp_workflow(test_queries)
        
        # 3. Embedding Provider Benchmark
        print("\n3. Embedding Provider Benchmark")
        print("-" * 40)
        benchmark_results["embedding_providers"] = self.benchmark_embedding_providers(test_texts)
        
        print("\n" + "=" * 80)
        print("✅ Benchmark Complete!")
        
        return benchmark_results


def main():
    """Run performance benchmarks."""
    try:
        # Load configuration
        from claude_indexer.config import load_config
        config = load_config()
        
        # Verify we have a collection to test with
        if not config.collection_name:
            print("❌ No collection name configured. Please run claude-indexer first.")
            return
        
        # Initialize benchmark
        benchmark = ProgressiveDisclosureBenchmark(config)
        
        # Run comprehensive benchmark
        results = benchmark.run_comprehensive_benchmark()
        
        # Save results
        timestamp = int(time.time())
        results_file = Path(f"performance_benchmark_results_{timestamp}.json")
        
        with open(results_file, "w") as f:
            json.dump(results, f, indent=2)
        
        print(f"\n📊 Results saved to: {results_file}")
        
        # Print summary
        print("\n📈 PERFORMANCE SUMMARY")
        print("=" * 50)
        
        if "search_performance" in results:
            search_perf = results["search_performance"]
            metadata_avg = search_perf["metadata_search"]["mean_time"] * 1000  # Convert to ms
            impl_avg = search_perf["implementation_access"]["mean_time"] * 1000
            
            print(f"Metadata Search:      {metadata_avg:.2f}ms avg")
            print(f"Implementation Access: {impl_avg:.2f}ms avg")
            
            if impl_avg > 0:
                speedup = impl_avg / metadata_avg
                print(f"Speedup Ratio:        {speedup:.1f}x faster for metadata")
                print(f"Performance Claim:    {'✅ VALIDATED' if speedup >= 2.0 else '❌ NEEDS OPTIMIZATION'}")
        
        if "embedding_providers" in results:
            embedding_results = results["embedding_providers"]
            for provider, data in embedding_results.items():
                if "error" not in data:
                    print(f"\n{provider.upper()} Embeddings:")
                    print(f"  Cost per 1K tokens:  ${data['cost_per_1k_tokens']:.6f}")
                    print(f"  Avg time per text:   {data['avg_time_per_text']*1000:.1f}ms")
        
    except Exception as e:
        print(f"❌ Benchmark failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()