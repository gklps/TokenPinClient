# Token Pin Client

A Python tool that automatically verifies tokens via a batch API and pins them to IPFS nodes. Designed for Rubix network nodes to ensure all owned tokens are properly pinned in their local IPFS repositories.

## 🎯 Overview

The Token Pin Client:
- Reads pending tokens (`token_status = 0`) from SQLite databases
- Calls `/tokens/batch` API to verify which tokens exist
- Updates `token_status = 2302` for tokens not found in the API
- Pins found tokens to IPFS using each node's correct `IPFS_PATH`
- Supports both single-database and auto-discovery modes

## ✨ Features

- **Automatic Node Discovery**: Scans for all `Rubix/rubix.db` files and automatically detects each node's IPFS repository
- **Per-Node IPFS Path Detection**: Automatically finds the correct `.ipfs` directory for each node
- **Batch API Processing**: Efficiently processes tokens in configurable batches
- **Status Management**: Only updates status for not-found tokens; found tokens are pinned but status remains unchanged
- **Comprehensive Logging**: Clear output showing which IPFS path is used for each node

## 📋 Requirements

- **Python 3.8+**
- **IPFS binary** (either `./ipfs` in the project directory or in system PATH)
- **SQLite database** with `TokensTable` or `tokens_table` containing tokens
- **Network access** to the `/tokens/batch` API endpoint

## 🚀 Quick Start

### 1. Clone and Setup

```bash
git clone <repository-url>
cd TokenPinClient
chmod +x setup.sh
./setup.sh
```

The setup script will:
- Check Python version
- Install required dependencies (`requests`)
- Check for IPFS binary
- Set up script permissions

### 2. Install Dependencies Manually (if needed)

```bash
pip3 install -r requirements.txt
```

### 3. Run the Client

#### Auto-Discovery Mode (Recommended)

Automatically finds all Rubix nodes and processes each with its own IPFS repository:

```bash
python3 token_pin_client.py --auto-discover --search-root ..
```

This mode:
- Scans for all `Rubix/rubix.db` files starting from `--search-root`
- Automatically detects each node's `.ipfs` directory
- Processes each node's pending tokens with the correct `IPFS_PATH`

#### Single Database Mode

Process a specific database:

```bash
python3 token_pin_client.py \
  --db-path /path/to/Rubix/rubix.db \
  --ipfs-path /path/to/.ipfs \
  --ipfs-command ./ipfs
```

## 📖 Usage

### Command Line Options

```
--db-path PATH          Path to SQLite database (required for single-db mode)
--table-name NAME       Table name (default: tokens_table)
--api-url URL           /tokens/batch API URL (default: http://173.255.197.82:5000/tokens/batch)
--batch-size N          Number of tokens per API batch (default: 100)
--ipfs-command PATH     Path to ipfs executable (default: ./ipfs)
--ipfs-path PATH        IPFS_PATH for this node's .ipfs repo (optional, uses env if not set)
--auto-discover         Auto-discover all Rubix/rubix.db databases
--search-root PATH      Root directory for auto-discovery (default: ..)
```

### Examples

**Auto-discover all nodes:**
```bash
python3 token_pin_client.py --auto-discover --search-root /path/to/nodes
```

**Process single database with custom settings:**
```bash
python3 token_pin_client.py \
  --db-path /data/node1/Rubix/rubix.db \
  --table-name TokensTable \
  --batch-size 200 \
  --ipfs-command /usr/local/bin/ipfs \
  --ipfs-path /data/node1/.ipfs
```

**Test with a specific API endpoint:**
```bash
python3 token_pin_client.py \
  --auto-discover \
  --api-url http://your-api-server:5000/tokens/batch
```

## 🔧 How It Works

### Workflow

1. **Read Pending Tokens**: Queries SQLite for tokens where `token_status = 0`
2. **Batch API Call**: Sends CIDs to `/tokens/batch` API in configurable batches
3. **Process Results**:
   - **Not Found**: Updates `token_status = 2302` for tokens in API's `not_found` list
   - **Found**: Pins token content to IPFS but **leaves status unchanged**
4. **IPFS Pinning**: For each found token:
   - Uses the token's `content` field from API response
   - Runs `echo -n "<content>" | ipfs add` with the node's `IPFS_PATH`
   - Verifies the returned CID matches the token's CID

### Status Codes

- `0` (PENDING_STATUS): Tokens waiting to be processed
- `2302` (NOT_FOUND_STATUS): Tokens not found in the API (updated automatically)
- Other statuses: Remain unchanged (found tokens are pinned but status is preserved)

### IPFS Path Detection

In auto-discovery mode, the client:
1. Finds all `Rubix/rubix.db` files
2. For each database, walks up the directory tree to find the corresponding `.ipfs` directory
3. Validates the `.ipfs` directory (checks for `config`, `datastore`, etc.)
4. Uses that `IPFS_PATH` when pinning tokens for that node

## 📁 Project Structure

```
TokenPinClient/
├── token_pin_client.py    # Main script
├── setup.sh               # Setup script
├── requirements.txt        # Python dependencies
├── README.md              # This file
├── .gitignore             # Git ignore rules
└── audit/                 # Reference tools (ignored by git)
    └── ...                # Audit repo tools for auto-discovery
```

## 🔍 Database Schema

The client expects a SQLite table (typically `TokensTable` or `tokens_table`) with:

- **CID column**: Contains the IPFS CID (detected automatically: `cid`, `token_cid`, `token_id`, `token_hash`)
- **Status column**: `token_status` (must exist)
- **Pending tokens**: Rows where `token_status = 0` are processed

## 🌐 API Integration

### `/tokens/batch` API

**Request:**
```json
POST /tokens/batch
Content-Type: application/json

{
  "cids": ["Qm...", "Qm...", ...]
}
```

**Response:**
```json
{
  "not_found": ["QmTqF1fUwee2h4pwNva3tW1tRAUMbJAiFeCewjJQTy5fvN"],
  "results": {
    "QmPd2Zm46ws1TZgpRu25YwPvLJkFAWP9k5QCCrqjNzKEiQ": {
      "cid": "QmPd2Zm46ws1TZgpRu25YwPvLJkFAWP9k5QCCrqjNzKEiQ",
      "content": "0014444717e44c186e1d38b133350f6625d80f0ff1787e6737be783e96fdcb42c74",
      "token_level": 1,
      "token_number": 2306987
    },
    ...
  },
  "total_found": 9,
  "total_not_found": 1,
  "total_requested": 10
}
```

## 🛠️ Troubleshooting

### IPFS Binary Not Found

```bash
# Option 1: Place ipfs binary in project directory
cp /path/to/ipfs ./ipfs
chmod +x ./ipfs

# Option 2: Use system IPFS
python3 token_pin_client.py --ipfs-command ipfs ...

# Option 3: Specify full path
python3 token_pin_client.py --ipfs-command /usr/local/bin/ipfs ...
```

### IPFS_PATH Not Set Correctly

```bash
# Check current IPFS_PATH
echo $IPFS_PATH

# Set for current session
export IPFS_PATH=/path/to/.ipfs

# Or use --ipfs-path flag
python3 token_pin_client.py --ipfs-path /path/to/.ipfs ...
```

### Auto-Discovery Not Working

- Ensure the `audit` folder exists (contains reference tools)
- Check that `Rubix/rubix.db` files exist in the search path
- Verify directory structure: `.../NodeName/Rubix/rubix.db`

### Database Connection Issues

- Verify SQLite database path is correct
- Check file permissions
- Ensure table name matches (use `--table-name` if different)

## 📝 Notes

- **Status Updates**: Only `not_found` tokens get status updated to `2302`. Found tokens are pinned but their status remains unchanged.
- **IPFS Pinning**: The client uses `ipfs add` with the content from the API response. The CID returned by IPFS must match the token's CID for successful pinning.
- **Audit Folder**: The `audit/` folder is ignored by git (reference only). It contains helper functions for auto-discovery. If missing, only single-database mode is available.

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## 📄 License

[Add your license here]

## 🔗 Related Projects

- **Audit Tools**: Reference implementation in `audit/` folder (not part of this repo)

---

**Questions or Issues?** Please open an issue on GitHub.

