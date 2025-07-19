#!/bin/bash
# Claude Code Memory Solution - Global Installer
# Creates a global wrapper script for the indexer

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get the absolute path to the memory project directory
MEMORY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_PATH="$MEMORY_DIR/claude_indexer"
VENV_PATH="$MEMORY_DIR/.venv"
WRAPPER_PATH="/usr/local/bin/claude-indexer"

echo -e "${BLUE}Claude Code Memory Solution - Global Installer${NC}"
echo "========================================"

# Check if claude_indexer package exists
if [[ ! -d "$PACKAGE_PATH" ]]; then
    echo -e "${RED}Error: claude_indexer package not found at $PACKAGE_PATH${NC}"
    exit 1
fi

# Check if virtual environment exists
if [[ ! -d "$VENV_PATH" ]]; then
    echo -e "${RED}Error: Virtual environment not found at $VENV_PATH${NC}"
    echo -e "${YELLOW}Please run: python3.12 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt${NC}"
    exit 1
fi

# Check if /usr/local/bin exists
if [[ ! -d "/usr/local/bin" ]]; then
    echo -e "${YELLOW}Creating /usr/local/bin directory...${NC}"
    if ! mkdir -p /usr/local/bin 2>/dev/null; then
        echo -e "${YELLOW}Need sudo permissions to create /usr/local/bin${NC}"
        sudo mkdir -p /usr/local/bin
    fi
fi

# Create the wrapper script
echo -e "${BLUE}Creating global wrapper script at $WRAPPER_PATH${NC}"

sudo tee "$WRAPPER_PATH" > /dev/null << EOF
#!/bin/bash
# Claude Code Memory Solution - Global Wrapper
# Auto-activates virtual environment and runs indexer

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

MEMORY_DIR="$MEMORY_DIR"
PACKAGE_PATH="$PACKAGE_PATH"
VENV_PATH="$VENV_PATH"

# Check if files still exist
if [[ ! -d "\$PACKAGE_PATH" ]]; then
    echo -e "\${RED}Error: claude_indexer package not found at \$PACKAGE_PATH\${NC}"
    echo -e "\${YELLOW}The memory project may have been moved or deleted.\${NC}"
    exit 1
fi

if [[ ! -d "\$VENV_PATH" ]]; then
    echo -e "\${RED}Error: Virtual environment not found at \$VENV_PATH\${NC}"
    exit 1
fi

# Use absolute python path instead of activating venv to avoid PATH conflicts
PYTHON_BIN="\$VENV_PATH/bin/python"

# Check if we're in a Python project (has .py files)
if [[ "\$1" == "--project" && "\$2" == "." ]]; then
    if [[ ! \$(find . -name "*.py" -type f 2>/dev/null | head -1) ]]; then
        echo -e "\${YELLOW}Warning: No Python files found in current directory${NC}"
        echo -e "\${YELLOW}Make sure you're in a Python project directory${NC}"
    fi
fi

# Run the indexer with all passed arguments
# Smart command detection and routing
if [[ "\$1" =~ ^(hooks|watch|service|search|file|add-mcp|chat|init)$ ]]; then
    # Advanced commands - pass through directly
    exec "\$PYTHON_BIN" -m claude_indexer "\$@"
elif [[ "\$1" == "index" ]]; then
    # Explicit index command - pass through
    exec "\$PYTHON_BIN" -m claude_indexer "\$@"
elif [[ "\$1" =~ ^--(help|version)$ ]]; then
    # Help and version commands
    exec "\$PYTHON_BIN" -m claude_indexer "\$@"
elif [[ "\$1" == "--help" || "\$1" == "-h" || "\$1" == "help" ]]; then
    # Help command variations
    exec "\$PYTHON_BIN" -m claude_indexer --help
elif [[ \$# -eq 0 ]]; then
    # No arguments - show help
    exec "\$PYTHON_BIN" -m claude_indexer --help
else
    # Basic indexing - use default index command for backward compatibility
    exec "\$PYTHON_BIN" -m claude_indexer index "\$@"
fi
EOF

# Make the wrapper executable
sudo chmod +x "$WRAPPER_PATH"

# Remove problematic venv binary that conflicts with global wrapper
VENV_CLAUDE_INDEXER="$VENV_PATH/bin/claude-indexer"
if [[ -f "$VENV_CLAUDE_INDEXER" ]]; then
    echo -e "${BLUE}Removing conflicting venv binary...${NC}"
    rm "$VENV_CLAUDE_INDEXER"
fi

# Verify installation
if [[ -x "$WRAPPER_PATH" ]]; then
    echo -e "${GREEN}✅ Installation successful!${NC}"
    echo ""
    echo -e "${BLUE}Usage:${NC}"
    echo "  claude-indexer --project /path/to/project --collection project-name"
    echo "  claude-indexer --project . --collection current-project"
    echo "  claude-indexer --help"
    echo ""
    echo -e "${BLUE}Basic Examples:${NC}"
    echo "  # Index current directory"
    echo "  claude-indexer --project . --collection my-project"
    echo ""
    echo "  # Index with incremental updates"
    echo "  claude-indexer --project /path/to/project --collection name --incremental"
    echo ""
    echo "  # Generate commands for debugging"
    echo "  claude-indexer --project . --collection test --generate-commands"
    echo ""
    echo -e "${BLUE}Advanced Commands:${NC}"
    echo "  # File watching"
    echo "  claude-indexer watch start --project . --collection my-project"
    echo ""
    echo "  # Git hooks"
    echo "  claude-indexer hooks install --project . --collection my-project"
    echo ""
    echo "  # Search collections"
    echo "  claude-indexer search \"query\" --project . --collection my-project"
    echo ""
    echo "  # Add MCP server"
    echo "  claude-indexer add-mcp my-project"
    echo ""
    echo "  # Chat processing"
    echo "  claude-indexer chat index --project . --collection my-project"
    echo ""
    echo -e "${GREEN}You can now use 'claude-indexer' from any directory!${NC}"
else
    echo -e "${RED}❌ Installation failed${NC}"
    exit 1
fi
