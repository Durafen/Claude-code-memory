#!/usr/bin/env python3
import json
import sys
import os

def extract_user_messages(file_path, output_format='full'):
    """Extract all user messages from Claude Code JSONL chat file"""
    user_messages = []
    
    with open(file_path, 'r') as f:
        for line_num, line in enumerate(f, 1):
            if line.strip():
                try:
                    data = json.loads(line)
                    if data.get('type') == 'user' and 'message' in data:
                        msg = data['message']
                        if isinstance(msg, dict) and msg.get('role') == 'user':
                            content = msg.get('content', '')
                            timestamp = data.get('timestamp', '')
                            
                            # Process content - could be string or list
                            if isinstance(content, list):
                                # Skip tool results
                                if content and isinstance(content[0], dict) and 'tool_use_id' in content[0]:
                                    continue
                                content = str(content)
                            
                            # Skip system messages and empty content
                            if (isinstance(content, str) and 
                                len(content) > 10 and 
                                not content.startswith('Caveat:') and
                                '<command-name>' not in content and
                                '<local-command-stdout>' not in content and
                                'This session is being continued' not in content):
                                
                                user_messages.append({
                                    'line': line_num,
                                    'timestamp': timestamp,
                                    'content': content
                                })
                except Exception as e:
                    pass
    
    return user_messages

def print_usage():
    print("Usage: python extract_user_messages.py <jsonl_file> [output_format]")
    print("Output formats: full (default), summary, markdown")
    print("Example: python extract_user_messages.py chat.jsonl summary")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print_usage()
        sys.exit(1)
    
    file_path = sys.argv[1]
    output_format = sys.argv[2] if len(sys.argv) > 2 else 'full'
    
    if not os.path.exists(file_path):
        print(f"Error: File '{file_path}' not found")
        sys.exit(1)
    
    messages = extract_user_messages(file_path, output_format)
    
    print(f"Total user messages found: {len(messages)}\n")
    
    if output_format == 'summary':
        # Just show first line of each message
        for i, msg in enumerate(messages, 1):
            first_line = msg['content'].split('\n')[0]
            print(f"{i}. {first_line}")
    
    elif output_format == 'markdown':
        # Output as markdown for analysis
        print("# User Chat Analysis\n")
        for i, msg in enumerate(messages, 1):
            print(f"## Message {i}")
            print(f"**Timestamp**: {msg['timestamp']}")
            print(f"**Content**:\n```\n{msg['content']}\n```\n")
    
    else:  # full format
        for i, msg in enumerate(messages, 1):
            print(f"Message {i} (Line {msg['line']}):")
            print(f"Timestamp: {msg['timestamp']}")
            print(f"Content: {msg['content']}")
            print("-" * 80)