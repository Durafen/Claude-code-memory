#!/usr/bin/env python3
"""Final validation of has_implementation fix"""

from claude_indexer.config.config_loader import ConfigLoader
from qdrant_client import QdrantClient
import os

config = ConfigLoader().load()
client = QdrantClient(url=config.qdrant_url, api_key=config.qdrant_api_key)

print('=== FINAL VALIDATION REPORT ===')


# Get file entities
file_result = client.scroll(
    collection_name='debug-flags-test-fixed',
    limit=50,
    with_payload=True,
    scroll_filter={
        'must': [
            {'key': 'chunk_type', 'match': {'value': 'metadata'}},
            {'key': 'entity_type', 'match': {'value': 'file'}}
        ]
    }
)

# Get implementation chunks
impl_result = client.scroll(
    collection_name='debug-flags-test-fixed',
    limit=200,
    with_payload=True,
    scroll_filter={
        'must': [
            {'key': 'chunk_type', 'match': {'value': 'implementation'}}
        ]
    }
)

files_with_impl = set()
for point in impl_result[0]:
    file_path = point.payload.get('file_path')
    if file_path:
        files_with_impl.add(file_path)

print(f'Files with implementation chunks: {len(files_with_impl)}')

# Check each file
critical_errors = 0
results = []
for point in file_result[0]:
    entity_name = point.payload.get('entity_name')
    has_impl_flag = point.payload.get('has_implementation', False)
    should_have_impl = entity_name in files_with_impl
    
    is_correct = has_impl_flag == should_have_impl
    if not is_correct:
        critical_errors += 1
    
    filename = entity_name.split('/')[-1]
    ext = os.path.splitext(filename)[1] or 'no_ext'
    
    results.append({
        'filename': filename,
        'extension': ext,
        'flag': has_impl_flag,
        'expected': should_have_impl,
        'correct': is_correct
    })

# Sort by extension
results.sort(key=lambda x: x['extension'])

print()
print('=== DETAILED RESULTS ===')
print(f"{'Filename':<35} {'Ext':<8} {'Flag':<6} {'Expected':<8} {'Status':<8}")
print("-" * 75)

for result in results:
    flag_str = "True" if result['flag'] else "False"
    exp_str = "True" if result['expected'] else "False"
    status = "✅" if result['correct'] else "❌"
    
    print(f"{result['filename']:<35} {result['extension']:<8} {flag_str:<6} {exp_str:<8} {status:<8}")

# Summary by extension
print()
print('=== SUMMARY BY EXTENSION ===')
by_extension = {}
for result in results:
    ext = result['extension']
    if ext not in by_extension:
        by_extension[ext] = {'total': 0, 'correct': 0, 'with_impl': 0}
    
    by_extension[ext]['total'] += 1
    if result['correct']:
        by_extension[ext]['correct'] += 1
    if result['flag']:
        by_extension[ext]['with_impl'] += 1

for ext, stats in sorted(by_extension.items()):
    pct = (stats['correct'] / stats['total']) * 100 if stats['total'] > 0 else 0
    print(f'{ext:<8}: {stats["correct"]}/{stats["total"]} correct ({pct:.1f}%), {stats["with_impl"]} with impl flags')

# Overall result
total_files = len(results)
correct_files = sum(1 for r in results if r['correct'])
overall_pct = (correct_files / total_files) * 100

print()
print('=== FINAL VERDICT ===')
print(f'Total files tested: {total_files}')
print(f'Correct flags: {correct_files}')
print(f'Incorrect flags: {total_files - correct_files}')
print(f'Accuracy: {overall_pct:.1f}%')

if critical_errors == 0:
    print('✅ SUCCESS: All file entities have correct has_implementation flags!')
    print('✅ Progressive disclosure fix working perfectly!')
else:
    print(f'❌ FAILURE: {critical_errors} file entities have incorrect flags')
    print('❌ Progressive disclosure fix needs more work')