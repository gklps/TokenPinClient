#!/usr/bin/env python3
"""
Token Pin Client

This tool reads pending tokens from a local SQLite database, calls the
/tokens/batch API to verify them, updates token_status for not-found
tokens, and pins found tokens to the local IPFS node using the node's
IPFS_PATH.

Intended usage per node:
  - Point to the node's SQLite database that contains tokens_table
  - Ensure the correct IPFS repository is selected (IPFS_PATH)
  - Run this script periodically or on demand
"""

import argparse
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

# Try to import helpers from the audit tools if available (for auto-discovery)
AUDIT_DIR = os.path.join(os.path.dirname(__file__), "audit")
AUDIT_IMPORT_ERROR = None
if os.path.isdir(AUDIT_DIR):
    sys.path.insert(0, AUDIT_DIR)
    try:
        from sync_distributed_tokens import (  # type: ignore
            find_rubix_databases,
            build_ipfs_path_mapping,
            extract_node_name,
            find_node_ipfs_binary,
        )
    except Exception as e:
        AUDIT_IMPORT_ERROR = str(e)
        find_rubix_databases = None  # type: ignore
        build_ipfs_path_mapping = None  # type: ignore
        extract_node_name = None  # type: ignore
        find_node_ipfs_binary = None  # type: ignore
else:
    find_rubix_databases = None  # type: ignore
    build_ipfs_path_mapping = None  # type: ignore


DEFAULT_API_URL = "http://173.255.197.82:5000/tokens/batch"

# Status codes – adjust if you already have specific meanings in your DB
PENDING_STATUS = 0
NOT_FOUND_STATUS = 2302
# Note: PINNED_STATUS and PIN_FAILED_STATUS are NOT used for status updates.
# Only not_found tokens get status updated to 2302.
# Tokens in results are pinned but status remains unchanged.
# PINNED_STATUS = 1         # (not used - status unchanged for pinned tokens)
# PIN_FAILED_STATUS = 2303  # (not used - status unchanged for pin failures)


def detect_columns(conn: sqlite3.Connection, table_name: str) -> Tuple[str, str]:
    """
    Detect CID and status column names from the given table.

    Returns:
        (cid_column, status_column)
    """
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table_name})")
    rows = cur.fetchall()
    if not rows:
        raise RuntimeError(f"Table '{table_name}' not found or has no columns")

    columns = {row[1] for row in rows}

    # Try common CID column names in order of preference
    cid_candidates = ["cid", "token_cid", "token_id", "token_hash"]
    cid_col = next((c for c in cid_candidates if c in columns), None)
    if not cid_col:
        raise RuntimeError(
            f"Could not detect CID column in table '{table_name}'. "
            f"Available columns: {sorted(columns)}"
        )

    # Token status column is required
    status_col = "token_status" if "token_status" in columns else None
    if not status_col:
        raise RuntimeError(
            f"Table '{table_name}' does not contain required column 'token_status'. "
            f"Available columns: {sorted(columns)}"
        )

    return cid_col, status_col


def get_pending_cids(
    conn: sqlite3.Connection,
    table_name: str,
    cid_col: str,
    status_col: str,
    pending_status: int = PENDING_STATUS,
) -> List[str]:
    """Fetch all CIDs with token_status == pending_status."""
    cur = conn.cursor()
    query = f"SELECT {cid_col} FROM {table_name} WHERE {status_col} = ?"
    cur.execute(query, (pending_status,))
    rows = cur.fetchall()
    cids = [row[0] for row in rows if row[0]]
    return cids


def batch_list(values: List[str], n: int) -> List[List[str]]:
    """Simple batching helper."""
    return [values[i : i + n] for i in range(0, len(values), n)]


def call_tokens_batch_api(api_url: str, cids: List[str]) -> Dict:
    """Call the /tokens/batch API for a list of CIDs."""
    payload = {"cids": cids}
    resp = requests.post(api_url, json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()


def run_ipfs_add(
    content: str,
    ipfs_command: str = "./ipfs",
    ipfs_path: Optional[str] = None,
) -> Tuple[bool, Optional[str], str]:
    """
    Run `ipfs add` on the given content via stdin.

    Returns:
        (success, cid, raw_output_or_error)
    """
    env = os.environ.copy()
    if ipfs_path:
        env["IPFS_PATH"] = ipfs_path

    # ipfs add reads from stdin when no file arguments are given
    try:
        proc = subprocess.run(
            [ipfs_command, "add"],
            input=content.encode("utf-8"),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError as e:
        return False, None, f"ipfs command not found: {e}"

    out = proc.stdout.decode("utf-8", errors="replace").strip()
    err = proc.stderr.decode("utf-8", errors="replace").strip()

    if proc.returncode != 0:
        return False, None, f"ipfs add failed: {err or out}"

    # Typical output line: "added <cid> <name>"
    cid = None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "added":
            cid = parts[1]
            break

    if not cid:
        return False, None, f"could not parse CID from ipfs add output: {out}"

    return True, cid, out


def update_token_statuses(
    conn: sqlite3.Connection,
    table_name: str,
    cid_col: str,
    status_col: str,
    cid_list: List[str],
    new_status: int,
) -> int:
    """Bulk update token_status for the given list of CIDs."""
    if not cid_list:
        return 0

    cur = conn.cursor()
    query = f"UPDATE {table_name} SET {status_col} = ? WHERE {cid_col} = ?"
    rows_updated = 0
    for cid in cid_list:
        cur.execute(query, (new_status, cid))
        rows_updated += cur.rowcount
    conn.commit()
    return rows_updated


def process_batch(
    conn: sqlite3.Connection,
    table_name: str,
    cid_col: str,
    status_col: str,
    api_url: str,
    cids: List[str],
    ipfs_command: str,
    ipfs_path: Optional[str],
) -> Tuple[int, int, int]:
    """
    Process one batch of CIDs:
      - Call /tokens/batch
      - Mark not_found as NOT_FOUND_STATUS (2302)
      - For results, pin via IPFS but leave status unchanged

    Returns:
      (not_found_count, pinned_ok_count, pin_failed_count)
    """
    if not cids:
        return 0, 0, 0

    print(f"Calling /tokens/batch for {len(cids)} tokens...")
    data = call_tokens_batch_api(api_url, cids)

    not_found = data.get("not_found", []) or []
    results = data.get("results", {}) or {}

    # Mark not_found immediately
    if not_found:
        updated = update_token_statuses(
            conn, table_name, cid_col, status_col, not_found, NOT_FOUND_STATUS
        )
        print(f"  Marked {updated} tokens as NOT_FOUND (status={NOT_FOUND_STATUS})")

    pinned_ok = 0
    pin_failed = 0

    for cid, info in results.items():
        content = info.get("content")
        if not content:
            print(f"  Skipping {cid}: no content in API response")
            pin_failed += 1
            continue

        success, added_cid, output = run_ipfs_add(
            content=content, ipfs_command=ipfs_command, ipfs_path=ipfs_path
        )
        if not success:
            print(f"  IPFS add failed for {cid}: {output} (status unchanged)")
            pin_failed += 1
            continue

        if added_cid != cid:
            print(
                f"  CID mismatch for {cid}: ipfs returned {added_cid}, "
                f"expected {cid} (status unchanged)"
            )
            pin_failed += 1
            continue

        # Success: pinned but status remains unchanged
        pinned_ok += 1
        print(f"  Pinned OK: {cid} (status unchanged)")

    return len(not_found), pinned_ok, pin_failed


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Token Pin Client: verify tokens via /tokens/batch and pin to IPFS"
    )
    parser.add_argument(
        "--db-path",
        required=False,
        help="Path to a single SQLite database that contains tokens_table / TokensTable",
    )
    parser.add_argument(
        "--table-name",
        default="tokens_table",
        help="Name of the SQLite table with tokens (default: tokens_table)",
    )
    parser.add_argument(
        "--api-url",
        default=DEFAULT_API_URL,
        help=f"/tokens/batch API URL (default: {DEFAULT_API_URL})",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Number of tokens per API batch (default: 100)",
    )
    parser.add_argument(
        "--ipfs-command",
        default="./ipfs",
        help="Path to ipfs executable (default: ./ipfs)",
    )
    parser.add_argument(
        "--ipfs-path",
        default=None,
        help="IPFS_PATH for this node's .ipfs repo (if not set, uses existing env)",
    )
    parser.add_argument(
        "--auto-discover",
        action="store_true",
        help=(
            "Automatically find all Rubix/rubix.db databases starting from "
            "a root folder and process each node with its own IPFS_PATH"
        ),
    )
    parser.add_argument(
        "--search-root",
        default="..",
        help=(
            "Root directory to search for Rubix/rubix.db when using --auto-discover "
            "(default: .., typically the parent containing Node folders)"
        ),
    )

    args = parser.parse_args(argv)

    # AUTO-DISCOVER MODE: scan all Rubix/rubix.db and handle each node automatically
    if args.auto_discover:
        if find_rubix_databases is None or build_ipfs_path_mapping is None:
            print("ERROR: --auto-discover requires the audit tools to be available.")
            print("")
            if not os.path.isdir(AUDIT_DIR):
                print(f"The 'audit' folder was not found at: {AUDIT_DIR}")
                print("")
                print("Would you like to automatically download the audit-tools repository?")
                print("This will enable auto-discovery mode.")
                print("")
                try:
                    response = input("Download audit-tools? (y/n) [y]: ").strip().lower()
                    if not response:
                        response = "y"
                except (KeyboardInterrupt, EOFError):
                    print("\nCancelled.")
                    return 1
                
                if response in ["y", "yes"]:
                    print("")
                    print("Downloading audit-tools repository...")
                    import subprocess
                    import tempfile
                    import shutil
                    
                    AUDIT_REPO_URL = "https://github.com/gklps/audit-tools.git"
                    temp_dir = tempfile.mkdtemp()
                    
                    try:
                        # Check if git is available
                        result = subprocess.run(
                            ["git", "--version"],
                            capture_output=True,
                            timeout=5
                        )
                        if result.returncode != 0:
                            raise FileNotFoundError("git not found")
                        
                        # Clone the repository
                        print(f"Cloning {AUDIT_REPO_URL}...")
                        result = subprocess.run(
                            ["git", "clone", AUDIT_REPO_URL, temp_dir],
                            capture_output=True,
                            text=True,
                            timeout=60
                        )
                        
                        if result.returncode == 0:
                            # Move to audit folder
                            if os.path.exists(AUDIT_DIR):
                                shutil.rmtree(AUDIT_DIR)
                            shutil.move(temp_dir, AUDIT_DIR)
                            
                            # Re-import
                            sys.path.insert(0, AUDIT_DIR)
                            try:
                                from sync_distributed_tokens import (  # type: ignore
                                    find_rubix_databases,
                                    build_ipfs_path_mapping,
                                    extract_node_name,
                                    find_node_ipfs_binary,
                                )
                                print("✓ Audit tools downloaded and imported successfully!")
                                print("Auto-discovery mode is now enabled.")
                                print("")
                            except Exception as e:
                                print(f"ERROR: Downloaded but import failed: {e}")
                                print("Please check the audit folder manually.")
                                return 1
                        else:
                            print(f"ERROR: Failed to clone repository: {result.stderr}")
                            print(f"Repository: {AUDIT_REPO_URL}")
                            return 1
                    except FileNotFoundError:
                        print("ERROR: git is not installed.")
                        print("Please install git first, or manually download the audit folder.")
                        print(f"Repository: {AUDIT_REPO_URL}")
                        return 1
                    except subprocess.TimeoutExpired:
                        print("ERROR: Download timed out. Please check your internet connection.")
                        return 1
                    except Exception as e:
                        print(f"ERROR: Failed to download: {e}")
                        return 1
                    finally:
                        # Clean up temp directory if it still exists
                        if os.path.exists(temp_dir):
                            shutil.rmtree(temp_dir)
                else:
                    print("")
                    print("Skipping download. To enable auto-discovery later:")
                    print(f"  git clone {AUDIT_REPO_URL} {AUDIT_DIR}")
                    print("")
                    print("Alternative: Use single-database mode with --db-path instead.")
                    return 1
            elif AUDIT_IMPORT_ERROR:
                print(f"The 'audit' folder exists but import failed: {AUDIT_IMPORT_ERROR}")
                print("")
                print("Please ensure:")
                print("1. The audit folder contains 'sync_distributed_tokens.py'")
                print("2. All dependencies for the audit tools are installed")
                print("3. The file is not corrupted")
                print("")
                print("Alternative: Use single-database mode with --db-path instead.")
                return 1
            else:
                print("The audit tools could not be imported for an unknown reason.")
                print("")
                print("Alternative: Use single-database mode with --db-path instead.")
                return 1

        print("==============================================")
        print(" Token Pin Client - AUTO DISCOVER MODE")
        print("==============================================")
        print(f"Search root : {os.path.abspath(args.search_root)}")
        print(f"API URL     : {args.api_url}")
        print(f"Batch size  : {args.batch_size}")
        print(f"IPFS cmd    : {args.ipfs_command}")
        print("Table name  : TokensTable (Rubix/rubix.db)")
        print("==============================================")

        # Find all Rubix/rubix.db databases starting from search_root
        databases = find_rubix_databases(args.search_root)  # type: ignore
        if not databases:
            print("No Rubix/rubix.db databases found. Nothing to do.")
            return 0

        # Build per-database IPFS_PATH mapping
        ipfs_mapping = build_ipfs_path_mapping(databases)  # type: ignore

        # Build per-database IPFS binary mapping
        ipfs_binary_mapping = {}
        if find_node_ipfs_binary:
            for db_path, _ in databases:
                ipfs_binary = find_node_ipfs_binary(db_path)  # type: ignore
                ipfs_binary_mapping[db_path] = ipfs_binary
        else:
            # Fallback: use global ipfs command for all nodes
            for db_path, _ in databases:
                ipfs_binary_mapping[db_path] = args.ipfs_command

        # Print summary of all discovered nodes and their IPFS paths
        print("\n📋 Discovered Nodes and IPFS Configuration:")
        print("=" * 80)
        for idx, (db_path, _last_mod) in enumerate(databases, start=1):
            node_ipfs_path = ipfs_mapping.get(db_path)
            node_ipfs_binary = ipfs_binary_mapping.get(db_path, args.ipfs_command)
            if extract_node_name:
                node_name = extract_node_name(db_path)  # type: ignore
            else:
                node_name = Path(db_path).parent.parent.name
            print(f"{idx}. Node: {node_name}")
            print(f"   Database: {db_path}")
            if node_ipfs_path:
                print(f"   IPFS_PATH: {node_ipfs_path}")
            else:
                print(f"   IPFS_PATH: ⚠️  NOT FOUND (will use environment IPFS_PATH)")
            if node_ipfs_binary:
                print(f"   IPFS binary: {node_ipfs_binary}")
            else:
                print(f"   IPFS binary: ⚠️  NOT FOUND (will use: {args.ipfs_command})")
        print("=" * 80)
        print()

        overall_not_found = 0
        overall_pinned_ok = 0
        overall_pin_failed = 0

        for idx, (db_path, _last_mod) in enumerate(databases, start=1):
            node_ipfs_path = ipfs_mapping.get(db_path)
            node_ipfs_binary = ipfs_binary_mapping.get(db_path, args.ipfs_command)
            # Extract node name if extract_node_name is available
            if extract_node_name:
                node_name = extract_node_name(db_path)  # type: ignore
            else:
                # Fallback: extract from path
                node_name = Path(db_path).parent.parent.name
            
            print("\n----------------------------------------------")
            print(f"[{idx}/{len(databases)}] Processing node: {node_name}")
            print(f"  Database path: {db_path}")
            if node_ipfs_path:
                print(f"  IPFS_PATH    : {node_ipfs_path}")
            else:
                print(
                    "  IPFS_PATH    : WARNING - No .ipfs directory found for this node; "
                    "will use existing IPFS_PATH environment."
                )
            if node_ipfs_binary:
                print(f"  IPFS binary  : {node_ipfs_binary}")
            else:
                print(f"  IPFS binary  : {args.ipfs_command} (fallback)")

            if not os.path.exists(db_path):
                print(f"  Skipping: DB not found on disk anymore: {db_path}")
                continue

            conn = sqlite3.connect(db_path)
            try:
                # For Rubix/rubix.db, the table is typically TokensTable
                table_name = "TokensTable"
                cid_col, status_col = detect_columns(conn, table_name)
                print(f"  Detected CID column   : {cid_col}")
                print(f"  Detected status column: {status_col}")

                pending_cids = get_pending_cids(
                    conn, table_name, cid_col, status_col, PENDING_STATUS
                )
                total_pending = len(pending_cids)
                if total_pending == 0:
                    print("  No pending tokens (token_status == 0) for this node.")
                    continue

                print(f"  Pending tokens to process: {total_pending}")

                total_not_found = 0
                total_pinned_ok = 0
                total_pin_failed = 0

                for i, batch_cids in enumerate(
                    batch_list(pending_cids, args.batch_size), start=1
                ):
                    print(
                        f"\n  --- Node batch {i} "
                        f"({len(batch_cids)} tokens) ---"
                    )
                    nf, ok, failed = process_batch(
                        conn=conn,
                        table_name=table_name,
                        cid_col=cid_col,
                        status_col=status_col,
                        api_url=args.api_url,
                        cids=batch_cids,
                        ipfs_command=node_ipfs_binary,
                        ipfs_path=node_ipfs_path,
                    )
                    total_not_found += nf
                    total_pinned_ok += ok
                    total_pin_failed += failed

                print("\n  Node summary:")
                print(f"    Pending processed     : {total_pending}")
                print(f"    Not found (status→2302): {total_not_found}")
                print(f"    Pinned OK (status unchanged): {total_pinned_ok}")
                print(f"    Pin failed (status unchanged): {total_pin_failed}")

                overall_not_found += total_not_found
                overall_pinned_ok += total_pinned_ok
                overall_pin_failed += total_pin_failed

            except Exception as e:
                print(f"  ERROR processing {db_path}: {e}")
            finally:
                conn.close()

        print("\n==============================================")
        print(" Token Pin Client - AUTO DISCOVER SUMMARY")
        print("==============================================")
        print(f"Total databases processed : {len(databases)}")
        print(f"Total not found (status→2302): {overall_not_found}")
        print(f"Total pinned OK (status unchanged): {overall_pinned_ok}")
        print(f"Total pin failed (status unchanged): {overall_pin_failed}")
        print("==============================================")
        return 0

    # SINGLE-DB MODE: process just one SQLite database
    db_path = args.db_path
    if not db_path:
        print(
            "ERROR: --db-path is required when not using --auto-discover. "
            "Use --auto-discover to scan all nodes automatically."
        )
        return 1

    if not os.path.exists(db_path):
        print(f"ERROR: SQLite database not found at {db_path}")
        return 1

    print("==============================================")
    print(" Token Pin Client - SINGLE DB MODE")
    print("==============================================")
    print(f"SQLite DB : {db_path}")
    print(f"Table     : {args.table_name}")
    print(f"API URL   : {args.api_url}")
    print(f"Batch size: {args.batch_size}")
    print(f"IPFS cmd  : {args.ipfs_command}")
    if args.ipfs_path:
        print(f"IPFS_PATH : {args.ipfs_path}")
    else:
        print("IPFS_PATH : using existing environment (must be set correctly)")
    print("==============================================")

    conn = sqlite3.connect(db_path)
    try:
        cid_col, status_col = detect_columns(conn, args.table_name)
        print(f"Detected CID column   : {cid_col}")
        print(f"Detected status column: {status_col}")

        pending_cids = get_pending_cids(
            conn, args.table_name, cid_col, status_col, PENDING_STATUS
        )
        total_pending = len(pending_cids)
        if total_pending == 0:
            print("No pending tokens found (token_status == 0). Nothing to do.")
            return 0

        print(f"Found {total_pending} pending tokens to verify and pin.")

        total_not_found = 0
        total_pinned_ok = 0
        total_pin_failed = 0

        for i, batch_cids in enumerate(
            batch_list(pending_cids, args.batch_size), start=1
        ):
            print(
                f"\n--- Processing batch {i} "
                f"({len(batch_cids)} tokens) ---"
            )
            nf, ok, failed = process_batch(
                conn=conn,
                table_name=args.table_name,
                cid_col=cid_col,
                status_col=status_col,
                api_url=args.api_url,
                cids=batch_cids,
                ipfs_command=args.ipfs_command,
                ipfs_path=args.ipfs_path,
            )
            total_not_found += nf
            total_pinned_ok += ok
            total_pin_failed += failed

        print("\n==============================================")
        print(" Token Pin Client Summary")
        print("==============================================")
        print(f"Total pending processed : {total_pending}")
        print(f"Not found (status→2302) : {total_not_found}")
        print(f"Pinned OK (status unchanged): {total_pinned_ok}")
        print(f"Pin failed (status unchanged): {total_pin_failed}")
        print("==============================================")

        return 0

    except Exception as e:
        print(f"ERROR: {e}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())


