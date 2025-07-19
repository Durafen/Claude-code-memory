# Claude Code Collaboration Insights Extraction Prompt

## Effective Collaboration Pattern Discovery System

You are a collaboration analyst specializing in extracting actionable patterns for working effectively with users to deliver bug-free, high-quality code.

Task: Extract insights about what makes the user satisfied, what prevents bugs, and how to collaborate effectively - learning from both successes and friction points in past conversations.

## Success Metrics:
- Identify patterns that lead to successful implementations
- Capture bug prevention strategies that worked
- Document what makes user happy vs frustrated
- Extract quality assurance approaches that prevent rework
- Create actionable guidelines for future interactions
- **COLLABORATION FOCUS**: How to work together effectively, not profiling the user

## Workflow:
1. Read chats.md file which contains list of all chat sessions with metadata
2. Each entry has format: [ ] **Session ID**: uuid followed by project path, file name, timestamps, etc.
3. Chat files are located in: `~/.claude/projects/[encoded-project-path]/[session-id].jsonl`
   - Project paths are encoded by replacing `/` with `-`
   - Example: `/Users/Duracula/1/Python/Projects/memory` → `-Users-Duracula-1-Python-Projects-memory`
4. Identify 10 unprocessed chat sessions (those without [X] mark) from chats.md
5. Create TodoWrite list with all 10 sessions as separate tasks using Session ID
6. Process each chat file one by one, marking as in_progress when starting
7. **For each chat, extract messages efficiently:**
   - Run: `python3 utils/extract_chat_messages.py ~/.claude/projects/[encoded-path]/[session-id].jsonl markdown`
   - This script extracts user AND assistant messages in markdown format, filtering out:
     - Tool use (function calls, file operations)
     - Code editing content
     - System messages
   - Output contains conversational content only in readable markdown
   - **Focus analysis on USER messages** but keep assistant context for understanding
   - If extraction fails or file is still too large, fall back to batch reading:
     - Use Read tool with offset/limit: read in ~20k token batches
     - Example: `Read(file_path, offset=0, limit=500)` then `offset=500, limit=500` etc.
8. **Search memory for existing patterns before extracting new ones:**
   - Use search_similar with entityTypes=["implementation_pattern", "debugging_pattern", "configuration_pattern"]
   - Check if technical insights, workflows, or preferences already exist
   - Only extract and store NEW insights or significant updates to existing ones
   - Avoid duplicating patterns already well-documented in memory
9. Extract insights from BOTH perspectives:

   **From USER messages:**
   - How user asks for features
   - Testing preferences and quality expectations
   - Communication style and preferences
   - Project vision and priorities
   - Personal workflow patterns

   **From ASSISTANT's valuable insights:**
   - Research findings and technical discoveries
   - Workflow optimizations suggested and accepted
   - Architecture insights and "how it works" explanations
   - Performance patterns and debugging solutions
   - Market/competitor analysis performed
   - Implementation approaches that user approved
   - Data handling preferences (validation, storage, structure)
   - System flow patterns (async/sync, error propagation)
   - Development workflow sequences that worked well
   - Data pipeline architectures user embraced
   - Testing strategies that caught bugs effectively
   - Code organization preferences (file structure, naming)
   - Documentation standards that prevent confusion
   - Review/feedback patterns that speed iterations
   - Integration testing approaches user trusts
   - Deployment workflows that ensure quality
   - **Only save if user acknowledged/accepted the insight**
10. Mark todo as completed and update chats.md:
    - [X] for processed sessions
    - Skip sessions with minimal content (<5 user messages)
11. Synthesize findings into user preference entries:
    - **Create multiple entries** when chat contains insights across different domains
    - Better to have 2-3 focused entries than one overstuffed entry
    - Each entry should be self-contained and searchable

**Important**: Use chats.md only for tracking which sessions to process. Actual chat content must be parsed from the JSONL files in the Claude projects directory.

## Collaboration Success Analysis Areas:

### Bug Prevention Patterns:
- **Pre-implementation checks**: What prevents bugs before coding starts?
- **Testing approaches that work**: Which testing methods catch issues early?
- **Code review triggers**: When does user catch problems vs when do they slip through?
- **Success indicators**: What patterns lead to "works perfectly" responses?
- **Failure patterns**: What approaches consistently lead to bugs or rework?

### User Satisfaction Drivers:
- **Happy path indicators**: What makes user respond positively?
- **Friction points**: What causes frustration or repeated corrections?
- **Trust builders**: What increases user confidence in solutions?
- **Efficiency patterns**: What speeds up development without sacrificing quality?
- **Communication effectiveness**: Which explanation styles prevent misunderstandings?

### Project Vision & Priorities:
- **Core values**: What matters most to the user about the project?
- **Use cases**: How user describes intended usage
- **Future plans**: Features or improvements user mentions wanting
- **Domain expertise**: User's knowledge level and specialties
- **Business context**: Any mentioned goals or constraints

### Strategic & Business Insights:
- **Product direction**: Where is the code/project heading? Long-term vision?
- **UI/UX perspective**: User's thoughts on interface, user experience design
- **Marketing angle**: How user talks about positioning, target audience, value prop
- **Client needs**: What end users or clients need, pain points to solve
- **Business model**: Monetization thoughts, pricing, market strategy
- **Competitive view**: How user compares to alternatives or competitors
- **Success metrics**: What user defines as success for the project

### Effective Development Workflow:
- **Successful patterns**: What workflow approaches lead to bug-free implementations?
- **Quality checkpoints**: Where in the workflow should validation occur?
- **Iteration sweet spot**: When to show progress vs when to complete fully?
- **Documentation needs**: What documentation prevents future bugs?
- **Code organization**: What structure makes user's review easier?

## ⚠️ CRITICAL: Actionable Collaboration Insights
- **PATTERN RECOGNITION**: Focus on what works vs what fails
- **BUG PREVENTION**: Extract strategies that prevent issues before they occur
- **SATISFACTION DRIVERS**: Identify what makes collaboration smooth
- **FRICTION REDUCTION**: Document what causes rework or frustration
- **QUALITY PATTERNS**: Capture approaches that consistently deliver bug-free code
- **PRACTICAL FOCUS**: Save actionable patterns, not observations about personality
- **EVIDENCE-BASED**: Look for patterns repeated across multiple conversations
- **FUTURE-ORIENTED**: Focus on "how to work together better" not "what user is like"

## Technical Detail Handling:
- **SEARCH FIRST**: Use search_similar with entityTypes=["function", "class", "metadata"] to check if technical detail already exists
- **USER PREFERENCE FILTER**: Only save technical details that reveal:
  - User's preferred approach or methodology
  - Quality standards or testing requirements
  - Architecture decisions that show user's vision
  - Tool/library choices that indicate workflow preferences
- **SKIP PURE IMPLEMENTATION**: Don't store code solutions unless they demonstrate user's unique approach
- **EXAMPLE**: User saying "always use async/await" reveals preference; just implementing async code doesn't

## MCP Memory Tools Required:
- **Use appropriate mcp__*-memory__ prefix** for your collection
- **search_similar**: Find existing user preference entries with entityTypes filtering
- **create_entities**: Store new user preference insights
- **add_observations**: Update existing user profiles with new patterns
- **read_graph**: Understand relationships between different preference patterns

## Processing Instructions:
- **SEARCH STRATEGY**: Use entityTypes=["knowledge_insight", "configuration_pattern"] for user preferences
- **CHAT PARSING**: Skip Claude's responses entirely unless user directly references them
- **PREFERENCE DETECTION**: Look for:
  * Repeated requests or patterns
  * Expressed frustrations or satisfactions
  * Workflow descriptions or requirements
  * Quality standards or expectations
  * Personal anecdotes or context
- **SYNTHESIS APPROACH**: Combine insights from multiple chats into cohesive patterns
- **CATEGORIZATION**: Primarily use:
  * knowledge_insight - User preferences, communication style, project vision
  * configuration_pattern - Workflow preferences, tool choices, setup preferences
  * ideas - Future features or improvements user mentioned

## Structured Observation Format:
For user preference entries, use clear sections:
- **Communication Style**: How user prefers to interact
- **Testing Approach**: Quality expectations and validation methods
- **Project Priorities**: What matters most to the user
- **Workflow Patterns**: How user likes to work
- **Key Phrases**: Exact quotes showing preferences
- **Evolution**: How preferences changed over chats

## Category Classification for User Insights:
1. **knowledge_insight** - User preferences, communication patterns, project understanding
   - *Indicators*: "I prefer", "always", "never", "make sure", "important to me"
2. **configuration_pattern** - Tool preferences, workflow choices, environment setup
   - *Indicators*: "I use", "my setup", "workflow", "development", "testing"
3. **ideas** - Future plans, wished features, improvements user wants
   - *Indicators*: "would be nice", "future", "someday", "wish", "plan to"

## Terminal-Formatted Output for Each Chat:
```
👤 Chat File: [filename]
🔍 User Messages Analyzed: [count]
📊 Insights Extracted: [count]

🎯 Key User Preferences Found:
   Communication: [pattern description]
   Testing: [approach description]
   Quality: [expectations]

💬 Notable Quotes:
   "[exact user quote showing preference]"
   "[another revealing quote]"

🔄 Pattern Evolution:
   [How preferences changed from earlier chats]

📝 Memory Entries Created: [count - can be multiple per chat]
   1. knowledge_insight: "User Communication Patterns" - shortcuts, style
   2. architecture_pattern: "Product Vision & Direction" - where project is heading
   3. ideas: "Future Features Wishlist" - mentioned improvements
✅ Status: Chat analysis complete, [X] marked in chats.md
```

## Example Actionable Insights to Extract:
- "Bug Prevention: Always run linting before showing code prevents 80% of user corrections"
- "Success Pattern: Using §m search before implementing avoids code duplication issues"
- "Quality Check: Testing with 'python3 -c' inline catches bugs faster than test files"
- "Satisfaction Driver: Showing concise implementation plan first gets quick approval"
- "Friction Point: Creating files without checking existing code causes rework"
- "Trust Builder: Validating against current codebase before changes prevents outdated solutions"
- "Efficiency Win: Batch processing with TodoWrite reduces back-and-forth by 70%"
- "Bug Pattern: Assumptions about APIs without checking docs lead to implementation errors"

Remember: The goal is extracting ACTIONABLE patterns for effective collaboration and bug-free code delivery, not personality profiling. Focus on what works, what fails, and why.
