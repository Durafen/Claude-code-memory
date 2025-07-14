#!/bin/bash
# Claude Code Memory Solution - Cross-Platform Installer
# Works on Windows (Git Bash/WSL), Linux, and macOS

set -ex  # Exit on error, show commands for debugging

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
REPO_URL="https://github.com/Durafen/Claude-code-memory.git"
MCP_REPO_URL="https://github.com/Durafen/mcp-qdrant-memory.git"
INSTALL_DIR="$HOME/Claude-code-memory"
MCP_SERVER_DIR="$INSTALL_DIR/mcp-qdrant-memory"

# Detect operating system
detect_os() {
    if [[ "$OSTYPE" == "msys"* ]] || [[ "$OSTYPE" == "cygwin"* ]] || [[ "$OSTYPE" == "win32"* ]]; then
        echo "windows"
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        echo "macos"
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        echo "linux"
    else
        echo "unknown"
    fi
}

# Detect Python command
detect_python() {
    local python_cmd=""
    local python_version=""
    
    # Try different Python commands
    for cmd in python3.12 python3.11 python3.10 python3.9 python3 python py; do
        if command -v "$cmd" &> /dev/null; then
            python_version=$("$cmd" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+' | head -1)
            if [[ $(echo "$python_version" | cut -d. -f1) -ge 3 ]] && [[ $(echo "$python_version" | cut -d. -f2) -ge 9 ]]; then
                python_cmd="$cmd"
                break
            fi
        fi
    done
    
    if [[ -z "$python_cmd" ]]; then
        echo ""
    else
        echo "$python_cmd"
    fi
}

# Get bin directory for the OS
get_bin_dir() {
    local os="$1"
    case "$os" in
        "windows")
            # Use user's local bin directory on Windows
            echo "$HOME/bin"
            ;;
        "macos"|"linux")
            # Use /usr/local/bin on Unix-like systems
            echo "/usr/local/bin"
            ;;
        *)
            echo "$HOME/bin"
            ;;
    esac
}

# Create directory (with or without sudo)
create_dir() {
    local dir="$1"
    local os="$2"
    
    if [[ "$os" == "windows" ]]; then
        mkdir -p "$dir"
    else
        if [[ -w "$(dirname "$dir")" ]]; then
            mkdir -p "$dir"
        else
            echo -e "${YELLOW}Need sudo permissions to create $dir${NC}"
            sudo mkdir -p "$dir"
        fi
    fi
}

# Install file (with or without sudo)
install_file() {
    local file="$1"
    local dest="$2"
    local os="$3"
    
    if [[ "$os" == "windows" ]]; then
        cp "$file" "$dest"
        chmod +x "$dest"
    else
        if [[ -w "$(dirname "$dest")" ]]; then
            cp "$file" "$dest"
            chmod +x "$dest"
        else
            sudo cp "$file" "$dest"
            sudo chmod +x "$dest"
        fi
    fi
}

echo -e "${BLUE}Claude Code Memory Solution - Cross-Platform Installer${NC}"
echo "============================================================"

# Detect operating system
OS=$(detect_os)
echo -e "${BLUE}Detected OS: $OS${NC}"

# Check prerequisites
echo -e "${BLUE}Checking prerequisites...${NC}"

# Check Python
PYTHON_CMD=$(detect_python)
if [[ -z "$PYTHON_CMD" ]]; then
    echo -e "${RED}Error: Python 3.9+ is required but not found${NC}"
    echo -e "${YELLOW}Please install Python 3.9 or higher${NC}"
    echo -e "${YELLOW}Tried: python3.12, python3.11, python3.10, python3.9, python3, python, py${NC}"
    exit 1
fi
PYTHON_VERSION=$("$PYTHON_CMD" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')
echo -e "${GREEN}✅ Python found: $PYTHON_CMD (version $PYTHON_VERSION)${NC}"

# Check Node.js
if ! command -v node &> /dev/null; then
    echo -e "${RED}Error: Node.js is not installed${NC}"
    echo -e "${YELLOW}Please install Node.js from https://nodejs.org${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Node.js found ($(node --version))${NC}"

# Check npm
if ! command -v npm &> /dev/null; then
    echo -e "${RED}Error: npm is not installed${NC}"
    echo -e "${YELLOW}Please install npm (usually comes with Node.js)${NC}"
    exit 1
fi
echo -e "${GREEN}✅ npm found ($(npm --version))${NC}"

# Check git
if ! command -v git &> /dev/null; then
    echo -e "${RED}Error: git is not installed${NC}"
    echo -e "${YELLOW}Please install git${NC}"
    exit 1
fi
echo -e "${GREEN}✅ git found${NC}"

# Clone or update main repository
if [[ -d "$INSTALL_DIR" ]]; then
    echo -e "${YELLOW}Directory $INSTALL_DIR already exists${NC}"
    echo -e "${BLUE}Updating existing repository...${NC}"
    cd "$INSTALL_DIR"
    git pull origin master
else
    echo -e "${BLUE}Cloning main repository to $INSTALL_DIR...${NC}"
    git clone "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# Clone or update MCP server repository
if [[ -d "$MCP_SERVER_DIR" ]]; then
    echo -e "${YELLOW}MCP directory $MCP_SERVER_DIR already exists${NC}"
    echo -e "${BLUE}Updating existing MCP repository...${NC}"
    cd "$MCP_SERVER_DIR"
    git pull origin master
    cd "$INSTALL_DIR"
else
    echo -e "${BLUE}Cloning MCP server repository to $MCP_SERVER_DIR...${NC}"
    git clone "$MCP_REPO_URL" "$MCP_SERVER_DIR"
fi

# Verify successful clone/update
echo -e "${BLUE}=== REPOSITORY VERIFICATION ===${NC}"
echo -e "${BLUE}Main repository: $(pwd)${NC}"
echo -e "${BLUE}Main repository contents:${NC}"
ls -la

echo -e "${BLUE}MCP repository: $MCP_SERVER_DIR${NC}"
echo -e "${BLUE}MCP repository contents:${NC}"
ls -la "$MCP_SERVER_DIR"

# ============================================
# PYTHON INDEXER SETUP
# ============================================
echo -e "${BLUE}Setting up Python indexer...${NC}"

# Set up virtual environment
VENV_PATH="$INSTALL_DIR/.venv"
if [[ ! -d "$VENV_PATH" ]]; then
    echo -e "${BLUE}Creating Python virtual environment...${NC}"
    "$PYTHON_CMD" -m venv "$VENV_PATH"
else
    echo -e "${GREEN}✅ Python virtual environment already exists${NC}"
fi

# Get Python executable path (OS-specific)
if [[ "$OS" == "windows" ]]; then
    PYTHON_BIN="$VENV_PATH/Scripts/python.exe"
    PIP_BIN="$VENV_PATH/Scripts/pip.exe"
else
    PYTHON_BIN="$VENV_PATH/bin/python"
    PIP_BIN="$VENV_PATH/bin/pip"
fi

# Install Python dependencies
echo -e "${BLUE}Installing Python dependencies...${NC}"
"$PIP_BIN" install --upgrade pip
"$PIP_BIN" install -r requirements.txt

# Install claude_indexer package in editable mode
echo -e "${BLUE}Installing claude_indexer package...${NC}"
"$PIP_BIN" install -e .

# Verify Python installation
if ! "$PYTHON_BIN" -c "import claude_indexer" 2>/dev/null; then
    echo -e "${RED}Error: Failed to install claude_indexer package${NC}"
    exit 1
fi
echo -e "${GREEN}✅ claude_indexer package installed successfully${NC}"

# ============================================
# NODE.JS MCP SERVER SETUP
# ============================================
echo -e "${BLUE}Setting up Node.js MCP server...${NC}"

# Install Node.js dependencies
echo -e "${BLUE}Installing Node.js dependencies...${NC}"

# Validate MCP server directory exists
if [[ ! -d "$MCP_SERVER_DIR" ]]; then
    echo -e "${RED}ERROR: MCP server directory not found at $MCP_SERVER_DIR${NC}"
    echo -e "${YELLOW}The MCP server repository may not have been cloned properly.${NC}"
    exit 1
fi

echo -e "${GREEN}✅ MCP server directory verified${NC}"
cd "$MCP_SERVER_DIR"
npm install

# Build TypeScript
echo -e "${BLUE}Building TypeScript...${NC}"
npm run build

# Verify build
if [[ ! -f "$MCP_SERVER_DIR/dist/index.js" ]]; then
    echo -e "${RED}Error: TypeScript build failed${NC}"
    exit 1
fi
echo -e "${GREEN}✅ MCP server built successfully${NC}"

# Return to root directory
cd "$INSTALL_DIR"

# ============================================
# GLOBAL COMMAND WRAPPERS
# ============================================
echo -e "${BLUE}Setting up global command wrappers...${NC}"

# Get bin directory for the OS
BIN_DIR=$(get_bin_dir "$OS")
WRAPPER_PATH="$BIN_DIR/claude-indexer"
MCP_WRAPPER_PATH="$BIN_DIR/mcp-qdrant-memory"

# Create bin directory if it doesn't exist
if [[ ! -d "$BIN_DIR" ]]; then
    echo -e "${YELLOW}Creating bin directory: $BIN_DIR${NC}"
    create_dir "$BIN_DIR" "$OS"
fi

# Add to PATH on Windows if needed
if [[ "$OS" == "windows" ]] && [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo -e "${YELLOW}Adding $BIN_DIR to PATH for this session${NC}"
    export PATH="$BIN_DIR:$PATH"
    echo -e "${YELLOW}Note: Add $BIN_DIR to your system PATH permanently${NC}"
fi

# Create temporary wrapper scripts
TEMP_DIR=$(mktemp -d)
TEMP_WRAPPER="$TEMP_DIR/claude-indexer"
TEMP_MCP_WRAPPER="$TEMP_DIR/mcp-qdrant-memory"

# Create Python indexer wrapper
echo -e "${BLUE}Creating Python indexer wrapper...${NC}"
cat > "$TEMP_WRAPPER" << EOF
#!/bin/bash
# Claude Code Memory Solution - Python Indexer Wrapper

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

MEMORY_DIR="$INSTALL_DIR"
PACKAGE_PATH="$INSTALL_DIR/claude_indexer"
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

# Use absolute python path (OS-specific)
if [[ "\$OSTYPE" == "msys"* ]] || [[ "\$OSTYPE" == "cygwin"* ]] || [[ "\$OSTYPE" == "win32"* ]]; then
    PYTHON_BIN="\$VENV_PATH/Scripts/python.exe"
else
    PYTHON_BIN="\$VENV_PATH/bin/python"
fi

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

# Create MCP server wrapper
echo -e "${BLUE}Creating MCP server wrapper...${NC}"
cat > "$TEMP_MCP_WRAPPER" << EOF
#!/bin/bash
# Claude Code Memory Solution - MCP Server Wrapper

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

MCP_SERVER_DIR="$MCP_SERVER_DIR"

# Check if files still exist
if [[ ! -f "\$MCP_SERVER_DIR/dist/index.js" ]]; then
    echo -e "\${RED}Error: MCP server not found at \$MCP_SERVER_DIR/dist/index.js\${NC}"
    echo -e "\${YELLOW}The MCP server may have been moved or not built.\${NC}"
    exit 1
fi

# Run the MCP server with all passed arguments
exec node "\$MCP_SERVER_DIR/dist/index.js" "\$@"
EOF

# Install wrapper scripts
chmod +x "$TEMP_WRAPPER" "$TEMP_MCP_WRAPPER"
install_file "$TEMP_WRAPPER" "$WRAPPER_PATH" "$OS"
install_file "$TEMP_MCP_WRAPPER" "$MCP_WRAPPER_PATH" "$OS"

# Clean up temp files
rm -rf "$TEMP_DIR"

echo -e "${GREEN}✅ Python indexer wrapper created at $WRAPPER_PATH${NC}"
echo -e "${GREEN}✅ MCP server wrapper created at $MCP_WRAPPER_PATH${NC}"

# Remove conflicting venv binary
if [[ "$OS" != "windows" ]]; then
    VENV_CLAUDE_INDEXER="$VENV_PATH/bin/claude-indexer"
    if [[ -f "$VENV_CLAUDE_INDEXER" ]]; then
        echo -e "${BLUE}Removing conflicting venv binary...${NC}"
        rm "$VENV_CLAUDE_INDEXER"
    fi
fi

# ============================================
# ENVIRONMENT SETUP
# ============================================
echo -e "${BLUE}Setting up environment configuration...${NC}"

# Create settings file from template if it doesn't exist
SETTINGS_FILE="$INSTALL_DIR/settings.txt"
TEMPLATE_FILE="$INSTALL_DIR/settings.template.txt"

if [[ ! -f "$SETTINGS_FILE" && -f "$TEMPLATE_FILE" ]]; then
    echo -e "${BLUE}Creating settings.txt from template...${NC}"
    cp "$TEMPLATE_FILE" "$SETTINGS_FILE"
    echo -e "${YELLOW}Please configure your API keys in $SETTINGS_FILE${NC}"
fi

# Create MCP server .env file
MCP_ENV_FILE="$MCP_SERVER_DIR/.env"
if [[ ! -f "$MCP_ENV_FILE" ]]; then
    echo -e "${BLUE}Creating MCP server .env file...${NC}"
    cat > "$MCP_ENV_FILE" << EOF
# Qdrant Configuration
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=
QDRANT_COLLECTION_NAME=memory-project

# API Keys
OPENAI_API_KEY=
VOYAGE_API_KEY=
EOF
    echo -e "${YELLOW}Please configure your API keys in $MCP_ENV_FILE${NC}"
fi

# Final verification
if [[ -x "$WRAPPER_PATH" && -x "$MCP_WRAPPER_PATH" ]]; then
    echo -e "${GREEN}✅ Cross-platform installation successful!${NC}"
    echo ""
    echo -e "${BLUE}Installation Summary:${NC}"
    echo "  OS: $OS"
    echo "  Python: $PYTHON_CMD ($PYTHON_VERSION)"
    echo "  Main repository: $INSTALL_DIR"
    echo "  MCP repository: $MCP_SERVER_DIR"
    echo "  Python env: $VENV_PATH"
    echo "  Bin directory: $BIN_DIR"
    echo "  Python indexer: $WRAPPER_PATH"
    echo "  MCP server: $MCP_WRAPPER_PATH"
    echo ""
    if [[ "$OS" == "windows" ]]; then
        echo -e "${YELLOW}Windows users: Make sure $BIN_DIR is in your PATH${NC}"
        echo -e "${YELLOW}You can add it to your system PATH in System Properties > Environment Variables${NC}"
        echo ""
    fi
    echo -e "${BLUE}Python Indexer Usage:${NC}"
    echo "  claude-indexer --project /path/to/project --collection project-name"
    echo "  claude-indexer --project . --collection current-project"
    echo "  claude-indexer --help"
    echo ""
    echo -e "${BLUE}Setup Steps:${NC}"
    echo "1. Start Qdrant database:"
    if [[ "$OS" == "windows" ]]; then
        echo "   docker run -p 6333:6333 -v \${PWD}/qdrant_storage:/qdrant/storage qdrant/qdrant"
    else
        echo "   docker run -p 6333:6333 -v \$(pwd)/qdrant_storage:/qdrant/storage qdrant/qdrant"
    fi
    echo ""
    echo "2. Configure API keys:"
    echo "   - Edit $SETTINGS_FILE"
    echo "   - Edit $MCP_ENV_FILE"
    echo ""
    echo "3. Test installation:"
    echo "   claude-indexer --help"
    echo "   mcp-qdrant-memory"
    echo ""
    echo -e "${GREEN}Cross-platform installation complete!${NC}"
else
    echo -e "${RED}❌ Installation failed${NC}"
    exit 1
fi