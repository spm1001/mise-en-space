#!/usr/bin/env python3
"""
OAuth Authentication for mise-en-space.

Two credential sources, tried in order:
1. Local credentials.json in repo root (for external users)
2. GCP Secret Manager (for maintainer — requires gcloud CLI)

Usage:
    uv run python -m auth                    # Auto mode (opens browser)
    uv run python -m auth --manual           # Manual mode (copy-paste URL)
    uv run python -m auth --project OTHER    # Use different GCP project

Prerequisites (external users):
    - credentials.json in repo root (from GCP Console)

Prerequisites (maintainer):
    - gcloud CLI installed and authenticated
    - Access to the GCP project containing the secret
"""

import os
import sys
import argparse
import subprocess
import tempfile
from pathlib import Path

from jeton import authenticate

from oauth_config import (
    TOKEN_FILE,
    SCOPES,
    OAUTH_PORT,
    GCP_PROJECT,
    SECRET_NAME,
    LOCAL_CREDENTIALS_FILE,
)


def _is_interactive() -> bool:
    """Check if we're running in an interactive terminal."""
    return sys.stdin.isatty() and os.environ.get("DISPLAY", os.environ.get("WAYLAND_DISPLAY", ""))


def fetch_credentials_from_secret_manager(project: str, secret_name: str) -> str:
    """Fetch OAuth client credentials from GCP Secret Manager."""
    cmd = [
        'gcloud', 'secrets', 'versions', 'access', 'latest',
        f'--secret={secret_name}',
        f'--project={project}',
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result.stdout
    except FileNotFoundError:
        print("Error: gcloud CLI not found")
        print("Install: https://cloud.google.com/sdk/docs/install")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"Error fetching secret: {e.stderr.strip()}")
        print()
        print("Check that you have access to the secret:")
        print(f"  gcloud secrets versions access latest --secret={secret_name} --project={project}")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="OAuth authentication for mise-en-space"
    )
    parser.add_argument(
        '--manual',
        action='store_true',
        help='Manual mode: copy-paste OAuth flow (for remote/SSH/Claude)'
    )
    parser.add_argument(
        '--code',
        type=str,
        help='Authorization code or redirect URL (non-interactive)'
    )
    parser.add_argument(
        '--project',
        type=str,
        default=GCP_PROJECT,
        help=f'GCP project containing credentials secret (default: {GCP_PROJECT})'
    )

    args = parser.parse_args()

    # Resolve credentials: local file first, then Secret Manager
    tmp_path = None
    if LOCAL_CREDENTIALS_FILE.exists():
        print(f"Using local credentials: {LOCAL_CREDENTIALS_FILE}")
        credentials_path = str(LOCAL_CREDENTIALS_FILE)
    else:
        print(f"No local credentials.json found.")
        print(f"Fetching from Secret Manager: {args.project}/{SECRET_NAME}...")
        credentials_json = fetch_credentials_from_secret_manager(args.project, SECRET_NAME)
        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        tmp.write(credentials_json)
        tmp.close()
        tmp_path = tmp.name
        credentials_path = tmp_path

    # Default to manual mode if no display available
    manual = args.manual or bool(args.code)
    if not manual and not _is_interactive():
        print("No display detected — using manual mode.")
        manual = True

    try:
        authenticate(
            credentials_path=credentials_path,
            token_path=TOKEN_FILE,
            scopes=SCOPES,
            manual_mode=manual,
            code=args.code,
            port=OAUTH_PORT,
        )
        print()
        print(f"Authentication complete. {TOKEN_FILE} created.")
    except KeyboardInterrupt:
        print("\n\nAuthentication cancelled")
        sys.exit(1)
    except Exception as e:
        print(f"\nAuthentication failed: {e}")
        sys.exit(1)
    finally:
        # Clean up temp credentials file (only if we fetched from Secret Manager)
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)


if __name__ == '__main__':
    main()
