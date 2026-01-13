#!/bin/bash
#
# Token Pin Client - Setup Script
# Automatically installs dependencies and sets up the environment
#

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo "=========================================="
echo "Token Pin Client - Setup"
echo "=========================================="
echo ""

# Check Python version
echo -e "${BLUE}Checking Python version...${NC}"
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}ERROR: Python 3 is not installed.${NC}"
    echo "Please install Python 3.8 or higher."
    exit 1
fi

PYTHON_VERSION=$(python3 --version | cut -d' ' -f2 | cut -d'.' -f1,2)
PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d'.' -f1)
PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d'.' -f2)

if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 8 ]); then
    echo -e "${RED}ERROR: Python 3.8+ required. Found Python $PYTHON_VERSION${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Python $PYTHON_VERSION found${NC}"
echo ""

# Check pip
echo -e "${BLUE}Checking pip...${NC}"
if ! command -v pip3 &> /dev/null; then
    echo -e "${YELLOW}pip3 not found. Installing pip...${NC}"
    python3 -m ensurepip --upgrade
fi
echo -e "${GREEN}✓ pip3 found${NC}"
echo ""

# Install Python dependencies
echo -e "${BLUE}Installing Python dependencies...${NC}"
if [ -f "requirements.txt" ]; then
    pip3 install -r requirements.txt
    echo -e "${GREEN}✓ Dependencies installed${NC}"
else
    echo -e "${YELLOW}WARNING: requirements.txt not found. Installing requests manually...${NC}"
    pip3 install requests
fi
echo ""

# Check for IPFS binary
echo -e "${BLUE}Checking for IPFS binary...${NC}"
IPFS_FOUND=false

# Check common locations
if [ -f "./ipfs" ]; then
    echo -e "${GREEN}✓ Found IPFS binary: ./ipfs${NC}"
    IPFS_FOUND=true
elif command -v ipfs &> /dev/null; then
    IPFS_PATH=$(which ipfs)
    echo -e "${GREEN}✓ Found IPFS binary: $IPFS_PATH${NC}"
    IPFS_FOUND=true
else
    echo -e "${YELLOW}⚠ IPFS binary not found in current directory or PATH${NC}"
    echo "  You may need to:"
    echo "  1. Download IPFS and place it in this directory as './ipfs'"
    echo "  2. Or install IPFS system-wide and ensure it's in PATH"
    echo "  3. Or specify --ipfs-command when running the script"
fi
echo ""

# Check for audit folder (optional, for auto-discovery)
echo -e "${BLUE}Checking for audit tools (optional, for auto-discovery)...${NC}"
AUDIT_REPO_URL="https://github.com/gklps/audit-tools.git"
AUDIT_NEEDED=false

if [ -d "audit" ]; then
    if [ -f "audit/sync_distributed_tokens.py" ]; then
        echo -e "${GREEN}✓ Audit folder found - auto-discovery mode will be available${NC}"
        echo "  The audit folder contains reference tools for discovering Rubix nodes."
        echo "  Note: The audit folder is ignored by git (.gitignore)."
    else
        echo -e "${YELLOW}⚠ Audit folder found but sync_distributed_tokens.py is missing${NC}"
        echo "  The audit folder exists but is incomplete."
        AUDIT_NEEDED=true
    fi
else
    echo -e "${YELLOW}⚠ Audit folder not found${NC}"
    echo "  Auto-discovery mode requires the audit folder."
    AUDIT_NEEDED=true
fi

if [ "$AUDIT_NEEDED" = true ]; then
    echo ""
    echo -e "${BLUE}Would you like to automatically download the audit-tools repository?${NC}"
    echo "  This will enable auto-discovery mode."
    echo ""
    read -p "Download audit-tools? (y/n) [y]: " download_audit
    download_audit=${download_audit:-y}
    
    if [[ "$download_audit" =~ ^[Yy]$ ]]; then
        echo ""
        echo -e "${BLUE}Downloading audit-tools repository...${NC}"
        
        # Check if git is available
        if ! command -v git &> /dev/null; then
            echo -e "${RED}ERROR: git is not installed.${NC}"
            echo "  Please install git first, or manually download the audit folder."
            echo "  Repository: $AUDIT_REPO_URL"
        else
            # Use a temporary directory for cloning
            TEMP_AUDIT_DIR=$(mktemp -d)
            
            if git clone "$AUDIT_REPO_URL" "$TEMP_AUDIT_DIR" 2>/dev/null; then
                # Remove existing audit folder if it exists but is incomplete
                if [ -d "audit" ]; then
                    rm -rf audit
                fi
                
                # Move the cloned repo to audit folder
                mv "$TEMP_AUDIT_DIR" audit
                
                if [ -f "audit/sync_distributed_tokens.py" ]; then
                    echo -e "${GREEN}✓ Audit tools downloaded successfully${NC}"
                    echo "  Auto-discovery mode is now available!"
                else
                    echo -e "${RED}ERROR: Downloaded audit folder is missing sync_distributed_tokens.py${NC}"
                    echo "  Please check the repository: $AUDIT_REPO_URL"
                fi
            else
                echo -e "${RED}ERROR: Failed to clone audit-tools repository${NC}"
                echo "  Please check your internet connection and try again."
                echo "  Repository: $AUDIT_REPO_URL"
                echo ""
                echo "  You can still use single-database mode with --db-path"
            fi
        fi
    else
        echo ""
        echo "  Skipping audit-tools download."
        echo "  You can still use single-database mode with --db-path"
        echo ""
        echo "  To enable auto-discovery later, run:"
        echo "    git clone $AUDIT_REPO_URL audit"
    fi
fi
echo ""

# Make token_pin_client.py executable
echo -e "${BLUE}Setting up script permissions...${NC}"
chmod +x token_pin_client.py
echo -e "${GREEN}✓ token_pin_client.py is now executable${NC}"
echo ""

# Summary
echo "=========================================="
echo -e "${GREEN}Setup Complete!${NC}"
echo "=========================================="
echo ""
echo "Next steps:"
echo ""
echo "1. Ensure IPFS is set up for your node(s):"
echo "   - Each node should have its own .ipfs directory"
echo "   - Set IPFS_PATH environment variable if needed"
echo ""
echo "2. Run the client:"
echo ""
echo "   # Auto-discover all nodes (requires audit folder):"
echo "   python3 token_pin_client.py --auto-discover --search-root .."
echo ""
echo "   # Or process a single database:"
echo "   python3 token_pin_client.py --db-path /path/to/rubix.db"
echo ""
echo "3. For help:"
echo "   python3 token_pin_client.py --help"
echo ""
echo "=========================================="

