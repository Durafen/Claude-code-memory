#!/usr/bin/env python3
import json
import sys
import os

def extract_chat_messages(file_path, output_format='full'):
    """Extract user and assistant text messages from Claude Code JSONL chat file"""
    messages = []
    
    with open(file_path, 'r') as f:
        for line_num, line in enumerate(f, 1):
            if line.strip():
                try:
                    data = json.loads(line)
                    
                    # Extract user messages
                    if data.get('type') == 'user' and 'message' in data:
                        msg = data['message']
                        if isinstance(msg, dict) and msg.get('role') == 'user':
                            content = msg.get('content', '')
                            timestamp = data.get('timestamp', '')
                            
                            # Process content - could be string or list
                            if isinstance(content, list):
                                # Extract text from content list
                                text_parts = []
                                for item in content:
                                    if isinstance(item, dict) and item.get('type') == 'text':
                                        text_parts.append(item.get('text', ''))
                                content = ' '.join(text_parts)
                            
                            # Skip system messages and empty content
                            if (isinstance(content, str) and 
                                len(content.strip()) > 0 and 
                                not content.startswith('Caveat:') and
                                '<command-name>' not in content and
                                '<local-command-stdout>' not in content and
                                'This session is being continued' not in content):
                                
                                messages.append({
                                    'role': 'user',
                                    'line': line_num,
                                    'timestamp': timestamp,
                                    'content': content.strip()
                                })
                    
                    # Extract assistant messages
                    elif data.get('type') == 'assistant' and 'message' in data:
                        msg = data['message']
                        if isinstance(msg, dict) and msg.get('role') == 'assistant':
                            content = msg.get('content', '')
                            timestamp = data.get('timestamp', '')
                            
                            # Process content - could be string or list
                            if isinstance(content, list):
                                # Extract only text content, skip tool use
                                text_parts = []
                                for item in content:
                                    if isinstance(item, dict):
                                        if item.get('type') == 'text':
                                            text_parts.append(item.get('text', ''))
                                        # Skip tool_use and other types
                                    elif isinstance(item, str):
                                        text_parts.append(item)
                                content = '\n\n'.join(text_parts)
                            
                            # Only add if there's actual text content
                            if isinstance(content, str) and len(content.strip()) > 0:
                                messages.append({
                                    'role': 'assistant',
                                    'line': line_num,
                                    'timestamp': timestamp,
                                    'content': content.strip()
                                })
                    
                except Exception as e:
                    pass
    
    return messages

def print_usage():
    print("Usage: python extract_chat_messages.py <jsonl_file> [output_format]")
    print("Output formats: full (default), conversation, markdown")
    print("Example: python extract_chat_messages.py chat.jsonl conversation")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print_usage()
        sys.exit(1)
    
    file_path = sys.argv[1]
    output_format = sys.argv[2] if len(sys.argv) > 2 else 'full'
    
    if not os.path.exists(file_path):
        print(f"Error: File '{file_path}' not found")
        sys.exit(1)
    
    messages = extract_chat_messages(file_path, output_format)
    
    user_count = sum(1 for m in messages if m['role'] == 'user')
    assistant_count = sum(1 for m in messages if m['role'] == 'assistant')
    
    print(f"Total messages found: {len(messages)}")
    print(f"User messages: {user_count}")
    print(f"Assistant messages: {assistant_count}\n")
    
    if output_format == 'conversation':
        # Show as conversation flow
        for msg in messages:
            role = "USER" if msg['role'] == 'user' else "ASSISTANT"
            print(f"[{role}]:")
            print(msg['content'])
            print("\n" + "-" * 80 + "\n")
    
    elif output_format == 'markdown':
        # Output as markdown for analysis
        print("# Chat Conversation\n")
        for i, msg in enumerate(messages, 1):
            role = "User" if msg['role'] == 'user' else "Assistant"
            print(f"## {role} Message {i}")
            print(f"**Timestamp**: {msg['timestamp']}")
            print(f"**Content**:\n```\n{msg['content']}\n```\n")
    
    else:  # full format
        for i, msg in enumerate(messages, 1):
            role = msg['role'].upper()
            print(f"{role} Message {i} (Line {msg['line']}):")
            print(f"Timestamp: {msg['timestamp']}")
            print(f"Content: {msg['content']}")
            print("-" * 80)