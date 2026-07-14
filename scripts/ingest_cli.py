#!/usr/bin/env python3
"""
Bulk-ingest every supported file in a folder (recursively) through the running
API, so normal auth/ACL/dedupe logic applies.

Usage:
    python scripts/ingest_cli.py \
        --api-url http://localhost/api \
        --email admin@example.com --password ... \
        --folder /path/to/documents \
        --groups dcas-cert,public
"""
import argparse
import sys
from pathlib import Path

import requests

SUPPORTED_SUFFIXES = {".pdf", ".docx", ".txt", ".md", ".html", ".htm"}


def login(api_url: str, email: str, password: str) -> str:
    resp = requests.post(
        f"{api_url}/auth/login",
        data={"username": email, "password": password},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def upload_file(api_url: str, token: str, path: Path, groups: str):
    with open(path, "rb") as f:
        resp = requests.post(
            f"{api_url}/documents/upload",
            headers={"Authorization": f"Bearer {token}"},
            params={"group_names": groups},
            files={"file": (path.name, f)},
            timeout=120,
        )
    if resp.status_code == 409:
        print(f"  skip (duplicate): {path.name}")
        return
    resp.raise_for_status()
    print(f"  queued: {path.name}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--folder", required=True)
    parser.add_argument("--groups", default="public", help="comma-separated group names")
    args = parser.parse_args()

    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"Not a directory: {folder}", file=sys.stderr)
        sys.exit(1)

    token = login(args.api_url, args.email, args.password)

    files = [p for p in folder.rglob("*") if p.suffix.lower() in SUPPORTED_SUFFIXES]
    print(f"Found {len(files)} supported files under {folder}")

    for path in files:
        try:
            upload_file(args.api_url, token, path, args.groups)
        except requests.HTTPError as e:
            print(f"  FAILED: {path.name} — {e.response.text}", file=sys.stderr)


if __name__ == "__main__":
    main()
