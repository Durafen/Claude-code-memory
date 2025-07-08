#!/usr/bin/env python3
"""Fix state file desync by removing orphaned entries."""

import json
from pathlib import Path

def main():
    # Remove the 2 orphaned entries from state file
    state_file = Path.home() / '.claude-indexer' / 'state' / 'ef8b4e06_memory-project.json'
    
    with open(state_file) as f:
        data = json.load(f)
    
    # Remove the orphaned entries
    orphaned_files = ['chat_reports/fix_memory_sections.py', 'utils/add_mcp_project.py']
    removed = 0
    
    for file in orphaned_files:
        if file in data:
            del data[file]
            removed += 1
            print(f'Removed: {file}')
    
    print(f'Total removed: {removed}')
    print(f'Files remaining: {len(data)}')
    
    # Write back the cleaned state
    with open(state_file, 'w') as f:
        json.dump(data, f, indent=2)
    
    print('State file cleaned!')

if __name__ == '__main__':
    main()