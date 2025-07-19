#!/usr/bin/env python3
"""
Database Entity Bloat Analysis for claude-memory collection
Comprehensive statistics and optimization opportunities identification
"""

from collections import Counter

from qdrant_client import QdrantClient

from claude_indexer.config.config_loader import ConfigLoader


def analyze_chunk_types():
    """Analyze chunk type distribution and storage efficiency"""
    print("🧩 CHUNK TYPE ANALYSIS & STORAGE EFFICIENCY")
    print("=" * 55)

    config = ConfigLoader().load()
    client = QdrantClient(url=config.qdrant_url, api_key=config.qdrant_api_key)
    collection_name = "claude-memory"

    # Get all points
    all_points = []
    scroll_result = client.scroll(
        collection_name=collection_name,
        limit=1000,
        with_payload=True,
        with_vectors=False,
    )

    points, next_page_offset = scroll_result
    all_points.extend(points)

    while next_page_offset:
        scroll_result = client.scroll(
            collection_name=collection_name,
            limit=1000,
            offset=next_page_offset,
            with_payload=True,
            with_vectors=False,
        )
        points, next_page_offset = scroll_result
        all_points.extend(points)

    # Chunk analysis by entity type
    entity_chunk_analysis = {}
    metadata_vs_impl = Counter()
    content_size_analysis = {}

    for point in all_points:
        if hasattr(point, "payload") and point.payload:
            entity_type = point.payload.get("entity_type", "unknown")
            chunk_type = point.payload.get("chunk_type", "unknown")

            # Skip relations for this analysis
            is_relation = (
                "entity_name" in point.payload
                and "relation_target" in point.payload
                and "relation_type" in point.payload
            )
            if is_relation:
                continue

            # Track chunk types per entity type
            if entity_type not in entity_chunk_analysis:
                entity_chunk_analysis[entity_type] = Counter()
            entity_chunk_analysis[entity_type][chunk_type] += 1

            # Overall metadata vs implementation
            metadata_vs_impl[chunk_type] += 1

            # Content size analysis
            content = point.payload.get("content", "")
            if content:
                content_length = len(str(content))
                if chunk_type not in content_size_analysis:
                    content_size_analysis[chunk_type] = []
                content_size_analysis[chunk_type].append(content_length)

    print("📊 METADATA VS IMPLEMENTATION RATIOS")
    print("-" * 45)
    total_non_relation = sum(metadata_vs_impl.values())
    for chunk_type, count in sorted(
        metadata_vs_impl.items(), key=lambda x: x[1], reverse=True
    ):
        if chunk_type != "unknown":
            percentage = (
                (count / total_non_relation * 100) if total_non_relation > 0 else 0
            )
            print(f"{chunk_type:<20} {count:>6,} ({percentage:>5.1f}%)")

    print()
    print("🔍 CHUNK EFFICIENCY BY ENTITY TYPE")
    print("-" * 45)

    # Focus on major entity types
    major_types = ["function", "class", "documentation", "file"]
    for entity_type in major_types:
        if entity_type in entity_chunk_analysis:
            chunks = entity_chunk_analysis[entity_type]
            total_chunks = sum(chunks.values())
            metadata_count = chunks.get("metadata", 0)
            impl_count = chunks.get("implementation", 0)

            if total_chunks > 0:
                print(f"{entity_type.upper():<15}:")
                print(f"  Total chunks:     {total_chunks:>6,}")
                print(
                    f"  Metadata:         {metadata_count:>6,} ({metadata_count / total_chunks * 100:>5.1f}%)"
                )
                print(
                    f"  Implementation:   {impl_count:>6,} ({impl_count / total_chunks * 100:>5.1f}%)"
                )
                if impl_count > 0:
                    ratio = metadata_count / impl_count
                    print(f"  Metadata/Impl:    {ratio:>6.1f}:1")
                print()

    print("📏 CONTENT SIZE ANALYSIS (Average Characters)")
    print("-" * 50)
    for chunk_type, sizes in content_size_analysis.items():
        if sizes and chunk_type != "unknown":
            avg_size = sum(sizes) / len(sizes)
            max_size = max(sizes)
            min_size = min(sizes)
            print(
                f"{chunk_type:<20}: Avg {avg_size:>6.0f}, Max {max_size:>7,}, Min {min_size:>4}"
            )

    print()
    print("🚨 BLOAT EFFICIENCY INDICATORS")
    print("-" * 40)
    metadata_total = metadata_vs_impl.get("metadata", 0)
    impl_total = metadata_vs_impl.get("implementation", 0)
    if impl_total > 0:
        overall_ratio = metadata_total / impl_total
        print(f"Overall Metadata:Impl Ratio:  {overall_ratio:.1f}:1")

        if overall_ratio > 2.0:
            print("⚠️  HIGH RATIO: Consider reducing metadata bloat")
        elif overall_ratio > 1.5:
            print("🔶 MODERATE: Metadata dominance - review necessity")
        else:
            print("✅ BALANCED: Reasonable metadata to implementation ratio")

    print(
        f"Metadata Storage Efficiency:  {metadata_total:,} metadata chunks for {impl_total:,} implementations"
    )


def analyze_file_level_bloat():
    """Identify files generating excessive entities (>50-100 entities)"""
    print("\n📁 FILE-LEVEL BLOAT DETECTION")
    print("=" * 45)

    config = ConfigLoader().load()
    client = QdrantClient(url=config.qdrant_url, api_key=config.qdrant_api_key)
    collection_name = "claude-memory"

    # Get all points
    all_points = []
    scroll_result = client.scroll(
        collection_name=collection_name,
        limit=1000,
        with_payload=True,
        with_vectors=False,
    )

    points, next_page_offset = scroll_result
    all_points.extend(points)

    while next_page_offset:
        scroll_result = client.scroll(
            collection_name=collection_name,
            limit=1000,
            offset=next_page_offset,
            with_payload=True,
            with_vectors=False,
        )
        points, next_page_offset = scroll_result
        all_points.extend(points)

    # File analysis
    file_entity_counts = Counter()
    file_type_breakdown = {}

    for point in all_points:
        if hasattr(point, "payload") and point.payload:
            file_path = point.payload.get("file_path", "")

            # Skip relations and manual entries
            is_relation = (
                "entity_name" in point.payload
                and "relation_target" in point.payload
                and "relation_type" in point.payload
            )

            if file_path and not is_relation:
                file_entity_counts[file_path] += 1

                # Track entity types per file
                entity_type = point.payload.get("entity_type", "unknown")
                if file_path not in file_type_breakdown:
                    file_type_breakdown[file_path] = Counter()
                file_type_breakdown[file_path][entity_type] += 1

    print(f"📊 Files with Entities: {len(file_entity_counts):,}")
    print()

    # High entity count files (>50 entities)
    high_count_files = [(f, c) for f, c in file_entity_counts.items() if c > 50]

    if high_count_files:
        print("🚨 FILES WITH >50 ENTITIES (POTENTIAL BLOAT)")
        print("-" * 55)
        high_count_files.sort(key=lambda x: x[1], reverse=True)

        for file_path, count in high_count_files[:15]:  # Top 15
            file_name = file_path.split("/")[-1] if "/" in file_path else file_path
            print(f"{count:>4} entities | {file_name}")

            # Show entity type breakdown for this file
            if file_path in file_type_breakdown:
                types = file_type_breakdown[file_path]
                top_types = sorted(types.items(), key=lambda x: x[1], reverse=True)[:3]
                type_summary = ", ".join([f"{t}({c})" for t, c in top_types])
                print(f"     Types: {type_summary}")
            print()

    # Very high entity count files (>100 entities)
    very_high_files = [f for f, c in file_entity_counts.items() if c > 100]

    if very_high_files:
        print("⚠️  CRITICAL: FILES WITH >100 ENTITIES")
        print("-" * 40)
        for file_path in very_high_files:
            count = file_entity_counts[file_path]
            file_name = file_path.split("/")[-1] if "/" in file_path else file_path
            print(f"{count:>4} entities | {file_name}")

    # File extension analysis
    print()
    print("📄 ENTITIES PER FILE EXTENSION")
    print("-" * 35)

    ext_entity_counts = Counter()
    ext_file_counts = Counter()

    for file_path, entity_count in file_entity_counts.items():
        if "." in file_path:
            ext = "." + file_path.split(".")[-1].lower()
            ext_entity_counts[ext] += entity_count
            ext_file_counts[ext] += 1

    for ext in sorted(ext_entity_counts.keys()):
        total_entities = ext_entity_counts[ext]
        file_count = ext_file_counts[ext]
        avg_per_file = total_entities / file_count if file_count > 0 else 0
        print(
            f"{ext:<8}: {total_entities:>5,} entities in {file_count:>3} files (avg {avg_per_file:>5.1f}/file)"
        )

    # Markdown analysis (since they dominate)
    md_files = [(f, c) for f, c in file_entity_counts.items() if f.endswith(".md")]
    if md_files:
        print()
        print("📝 MARKDOWN FILE ANALYSIS")
        print("-" * 30)
        md_entity_total = sum(c for f, c in md_files)
        md_file_count = len(md_files)
        avg_entities_per_md = md_entity_total / md_file_count

        print(f"Total MD files:        {md_file_count:>3}")
        print(f"Total MD entities:     {md_entity_total:>5,}")
        print(f"Average per MD file:   {avg_entities_per_md:>5.1f}")

        # Top markdown files by entity count
        md_files.sort(key=lambda x: x[1], reverse=True)
        print("Top 5 MD files by entities:")
        for file_path, count in md_files[:5]:
            file_name = file_path.split("/")[-1] if "/" in file_path else file_path
            print(f"  {count:>3} | {file_name}")


def analyze_content_quality():
    """Assess content quality - identify generic/duplicate names and minimal information value"""
    print("\n🔍 CONTENT QUALITY ASSESSMENT")
    print("=" * 40)

    config = ConfigLoader().load()
    client = QdrantClient(url=config.qdrant_url, api_key=config.qdrant_api_key)
    collection_name = "claude-memory"

    # Get all points
    all_points = []
    scroll_result = client.scroll(
        collection_name=collection_name,
        limit=1000,
        with_payload=True,
        with_vectors=False,
    )

    points, next_page_offset = scroll_result
    all_points.extend(points)

    while next_page_offset:
        scroll_result = client.scroll(
            collection_name=collection_name,
            limit=1000,
            offset=next_page_offset,
            with_payload=True,
            with_vectors=False,
        )
        points, next_page_offset = scroll_result
        all_points.extend(points)

    # Content quality analysis
    entity_names = Counter()
    generic_patterns = Counter()
    content_lengths = []
    empty_content_count = 0
    duplicate_content = Counter()

    # Common generic patterns
    generic_name_patterns = [
        "test_",
        "temp_",
        "tmp_",
        "debug_",
        "sample_",
        "example_",
        "function_",
        "method_",
        "class_",
        "var_",
        "item_",
        "data_",
        "new_",
        "old_",
        "backup_",
        "copy_",
        "draft_",
    ]

    for point in all_points:
        if hasattr(point, "payload") and point.payload:
            entity_name = point.payload.get("entity_name", "")
            content = point.payload.get("content", "")

            # Skip relations
            is_relation = (
                "relation_target" in point.payload and "relation_type" in point.payload
            )
            if is_relation:
                continue

            if entity_name:
                entity_names[entity_name] += 1

                # Check for generic patterns
                entity_name_lower = entity_name.lower()
                for pattern in generic_name_patterns:
                    if pattern in entity_name_lower:
                        generic_patterns[pattern] += 1
                        break

            # Content analysis
            if content:
                content_str = str(content)
                content_lengths.append(len(content_str))

                # Check for duplicated content
                if len(content_str) > 20:  # Only check meaningful content
                    content_hash = content_str[:100]  # First 100 chars as signature
                    duplicate_content[content_hash] += 1
            else:
                empty_content_count += 1

    print("📊 ENTITY NAME ANALYSIS")
    print("-" * 30)

    # Duplicate entity names
    duplicate_names = [
        (name, count) for name, count in entity_names.items() if count > 1
    ]
    duplicate_names.sort(key=lambda x: x[1], reverse=True)

    print(f"Total unique entity names: {len(entity_names):,}")
    print(f"Duplicate entity names:    {len(duplicate_names):,}")

    if duplicate_names:
        print("Top 10 duplicated names:")
        for name, count in duplicate_names[:10]:
            print(f"  {count:>2}x | {name}")

    print()
    print("🔤 GENERIC NAME PATTERNS")
    print("-" * 30)

    if generic_patterns:
        total_generic = sum(generic_patterns.values())
        print(f"Total entities with generic patterns: {total_generic:,}")

        for pattern, count in sorted(
            generic_patterns.items(), key=lambda x: x[1], reverse=True
        ):
            percentage = (count / total_generic * 100) if total_generic > 0 else 0
            print(f"{pattern:<12}: {count:>4,} ({percentage:>5.1f}%)")
    else:
        print("✅ No generic name patterns detected")

    print()
    print("📝 CONTENT QUALITY METRICS")
    print("-" * 35)

    if content_lengths:
        avg_length = sum(content_lengths) / len(content_lengths)
        min_length = min(content_lengths)
        max_length = max(content_lengths)

        # Count very short content (potentially low value)
        very_short = len([length for length in content_lengths if length < 20])
        short_content = len([length for length in content_lengths if 20 <= length < 50])
        medium_content = len(
            [length for length in content_lengths if 50 <= length < 200]
        )
        long_content = len([length for length in content_lengths if length >= 200])

        print(f"Entities with content:     {len(content_lengths):,}")
        print(f"Empty content:             {empty_content_count:,}")
        print(f"Average content length:    {avg_length:>6.1f} chars")
        print(f"Content range:             {min_length} - {max_length:,} chars")
        print()
        print("Content Length Distribution:")
        print(
            f"  Very short (<20 chars):  {very_short:>5,} ({very_short / len(content_lengths) * 100:>5.1f}%)"
        )
        print(
            f"  Short (20-49 chars):     {short_content:>5,} ({short_content / len(content_lengths) * 100:>5.1f}%)"
        )
        print(
            f"  Medium (50-199 chars):   {medium_content:>5,} ({medium_content / len(content_lengths) * 100:>5.1f}%)"
        )
        print(
            f"  Long (200+ chars):       {long_content:>5,} ({long_content / len(content_lengths) * 100:>5.1f}%)"
        )

    print()
    print("🔄 CONTENT DUPLICATION")
    print("-" * 25)

    # Find potential duplicate content
    potential_duplicates = [
        (sig, count) for sig, count in duplicate_content.items() if count > 1
    ]
    potential_duplicates.sort(key=lambda x: x[1], reverse=True)

    if potential_duplicates:
        print(
            f"Potentially duplicated content signatures: {len(potential_duplicates):,}"
        )
        print("Top duplicated content patterns:")
        for signature, count in potential_duplicates[:5]:
            preview = signature[:50] + "..." if len(signature) > 50 else signature
            print(f"  {count:>2}x | {preview}")
    else:
        print("✅ No significant content duplication detected")

    print()
    print("🚨 QUALITY ISSUES SUMMARY")
    print("-" * 30)

    quality_issues = []

    if len(duplicate_names) > 100:
        quality_issues.append(f"HIGH: {len(duplicate_names):,} duplicate entity names")
    elif len(duplicate_names) > 50:
        quality_issues.append(
            f"MEDIUM: {len(duplicate_names):,} duplicate entity names"
        )

    if generic_patterns:
        total_generic = sum(generic_patterns.values())
        if total_generic > 500:
            quality_issues.append(
                f"HIGH: {total_generic:,} entities with generic names"
            )
        elif total_generic > 200:
            quality_issues.append(
                f"MEDIUM: {total_generic:,} entities with generic names"
            )

    if content_lengths:
        very_short_pct = (
            len([length for length in content_lengths if length < 20])
            / len(content_lengths)
            * 100
        )
        if very_short_pct > 20:
            quality_issues.append(
                f"HIGH: {very_short_pct:.1f}% entities have very short content"
            )
        elif very_short_pct > 10:
            quality_issues.append(
                f"MEDIUM: {very_short_pct:.1f}% entities have very short content"
            )

    if quality_issues:
        for issue in quality_issues:
            print(f"⚠️  {issue}")
    else:
        print("✅ No major content quality issues detected")


def analyze_cross_category_bloat():
    """Compare entity counts across categories, ratios, and relation density"""
    print("\n📊 CROSS-CATEGORY BLOAT ANALYSIS")
    print("=" * 45)

    config = ConfigLoader().load()
    client = QdrantClient(url=config.qdrant_url, api_key=config.qdrant_api_key)
    collection_name = "claude-memory"

    # Get all points
    all_points = []
    scroll_result = client.scroll(
        collection_name=collection_name,
        limit=1000,
        with_payload=True,
        with_vectors=False,
    )

    points, next_page_offset = scroll_result
    all_points.extend(points)

    while next_page_offset:
        scroll_result = client.scroll(
            collection_name=collection_name,
            limit=1000,
            offset=next_page_offset,
            with_payload=True,
            with_vectors=False,
        )
        points, next_page_offset = scroll_result
        all_points.extend(points)

    # Cross-category analysis
    doc_vs_code_entities = Counter()
    manual_vs_auto_by_type = {}
    relation_sources = Counter()
    relation_targets = Counter()

    total_relations = 0
    total_entities = 0

    for point in all_points:
        if hasattr(point, "payload") and point.payload:
            # Check if relation
            is_relation = (
                "relation_target" in point.payload
                and "relation_type" in point.payload
                and "entity_name" in point.payload
            )

            if is_relation:
                total_relations += 1
                # Track relation patterns
                entity_name = point.payload.get("entity_name", "")
                relation_target = point.payload.get("relation_target", "")

                relation_sources[entity_name] += 1
                relation_targets[relation_target] += 1
            else:
                total_entities += 1
                entity_type = point.payload.get("entity_type", "unknown")

                # Categorize as documentation vs code
                if entity_type in ["documentation", "text_chunk"]:
                    doc_vs_code_entities["documentation"] += 1
                elif entity_type in [
                    "function",
                    "class",
                    "variable",
                    "import",
                    "interface",
                ]:
                    doc_vs_code_entities["code"] += 1
                elif entity_type in [
                    "implementation_pattern",
                    "debugging_pattern",
                    "performance_pattern",
                    "configuration_pattern",
                    "architecture_pattern",
                    "integration_pattern",
                    "knowledge_insight",
                    "active_issue",
                    "ideas",
                    "automation_pattern",
                ]:
                    doc_vs_code_entities["manual_patterns"] += 1
                else:
                    doc_vs_code_entities["other"] += 1

                # Manual vs auto by entity type
                has_file_path = (
                    "file_path" in point.payload and point.payload["file_path"]
                )
                classification = "auto" if has_file_path else "manual"

                if entity_type not in manual_vs_auto_by_type:
                    manual_vs_auto_by_type[entity_type] = Counter()
                manual_vs_auto_by_type[entity_type][classification] += 1

    print("📈 DOCUMENTATION VS CODE ANALYSIS")
    print("-" * 40)

    total_categorized = sum(doc_vs_code_entities.values())
    for category, count in sorted(
        doc_vs_code_entities.items(), key=lambda x: x[1], reverse=True
    ):
        percentage = (count / total_categorized * 100) if total_categorized > 0 else 0
        print(f"{category:<20}: {count:>6,} ({percentage:>5.1f}%)")

    print()
    print("🔗 RELATION DENSITY ANALYSIS")
    print("-" * 35)

    overall_relation_ratio = (
        total_relations / total_entities if total_entities > 0 else 0
    )
    print(f"Total Relations:         {total_relations:,}")
    print(f"Total Entities:          {total_entities:,}")
    print(f"Relations per Entity:    {overall_relation_ratio:.2f}")

    # Analyze which entities have the most relations
    high_relation_entities = [
        (entity, count) for entity, count in relation_sources.items() if count >= 5
    ]
    high_relation_entities.sort(key=lambda x: x[1], reverse=True)

    if high_relation_entities:
        print(f"Entities with 5+ relations: {len(high_relation_entities):,}")
        print("Top 10 most connected entities:")
        for entity, count in high_relation_entities[:10]:
            print(f"  {count:>2} relations | {entity}")

    print()
    print("⚖️  MANUAL VS AUTO DISTRIBUTION BY TYPE")
    print("-" * 45)

    # Focus on major entity types
    major_types = [
        "documentation",
        "function",
        "class",
        "variable",
        "implementation_pattern",
        "debugging_pattern",
    ]

    for entity_type in major_types:
        if entity_type in manual_vs_auto_by_type:
            type_data = manual_vs_auto_by_type[entity_type]
            total_type = sum(type_data.values())
            manual_count = type_data.get("manual", 0)
            auto_count = type_data.get("auto", 0)

            manual_pct = (manual_count / total_type * 100) if total_type > 0 else 0
            auto_pct = (auto_count / total_type * 100) if total_type > 0 else 0

            print(
                f"{entity_type:<20}: {total_type:>5,} total | {manual_count:>4,} manual ({manual_pct:>4.1f}%) | {auto_count:>4,} auto ({auto_pct:>4.1f}%)"
            )

    print()
    print("🚨 BLOAT RATIOS & THRESHOLDS")
    print("-" * 35)

    bloat_indicators = []

    # Documentation bloat check
    doc_count = doc_vs_code_entities.get("documentation", 0)
    code_count = doc_vs_code_entities.get("code", 0)
    if code_count > 0:
        doc_to_code_ratio = doc_count / code_count
        print(f"Documentation:Code Ratio: {doc_to_code_ratio:.2f}:1")
        if doc_to_code_ratio > 2.0:
            bloat_indicators.append(
                f"HIGH: Documentation entities {doc_to_code_ratio:.1f}x more than code"
            )
        elif doc_to_code_ratio > 1.5:
            bloat_indicators.append(
                f"MEDIUM: Documentation dominance ({doc_to_code_ratio:.1f}:1 ratio)"
            )

    # Relation bloat check
    if overall_relation_ratio > 1.0:
        print(
            f"Relation Density:         {overall_relation_ratio:.2f} relations/entity"
        )
        if overall_relation_ratio > 1.5:
            bloat_indicators.append(
                f"HIGH: Excessive relations ({overall_relation_ratio:.1f} per entity)"
            )
        elif overall_relation_ratio > 1.2:
            bloat_indicators.append(
                f"MEDIUM: High relation density ({overall_relation_ratio:.1f} per entity)"
            )

    # Manual pattern bloat
    manual_patterns = doc_vs_code_entities.get("manual_patterns", 0)
    manual_pattern_pct = (
        (manual_patterns / total_categorized * 100) if total_categorized > 0 else 0
    )
    print(f"Manual Pattern Content:   {manual_pattern_pct:.1f}% of all entities")

    if manual_pattern_pct > 5.0:
        bloat_indicators.append(
            f"MEDIUM: High manual pattern content ({manual_pattern_pct:.1f}%)"
        )

    print()
    if bloat_indicators:
        print("⚠️  CROSS-CATEGORY BLOAT ISSUES:")
        for indicator in bloat_indicators:
            print(f"   {indicator}")
    else:
        print("✅ Balanced cross-category distribution")


if __name__ == "__main__":
    analyze_chunk_types()
    analyze_file_level_bloat()
    analyze_content_quality()
    analyze_cross_category_bloat()
