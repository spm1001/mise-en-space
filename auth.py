#!/usr/bin/env python3
"""
OAuth Authentication for mise-en-space.

Two credential sources, tried in order:
1. Local credentials.json in repo root (for external users)
2. GCP Secret Manager (for maintainer — requires gcloud CLI)

Usage:
    uv run python -m auth                    # Auto mode (opens browser)
    uv run python -m auth --remote           # Print auth URL for remote/SSH
    uv run python -m auth --code URL         # Exchange code from --remote flow
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

from jeton import authenticate, get_auth_url

from oauth_config import (
    TOKEN_FILE,
    SCOPES,
    OAUTH_PORT,
    GCP_PROJECT,
    SECRET_NAME,
    LOCAL_CREDENTIALS_FILE,
)


def _can_open_browser() -> bool:
    """Check if we can open a browser for automatic OAuth flow.

    The auto flow opens a browser and starts a localhost callback server —
    no terminal input needed. The question is simply: can we open a browser?
    - macOS: always yes (via `open` command)
    - Linux with X11/Wayland: yes
    - Linux without display (SSH, CI): no
    """
    if sys.platform == "darwin":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


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
        '--remote',
        action='store_true',
        help='Print auth URL and save PKCE state (for remote/SSH — complete with --code)'
    )
    parser.add_argument(
        '--code',
        type=str,
        help='Exchange auth code or redirect URL from --remote flow'
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

    try:
        if args.remote:
            # Phase 1: generate auth URL and save PKCE state
            auth_url = get_auth_url(
                credentials_path=credentials_path,
                token_path=TOKEN_FILE,
                scopes=SCOPES,
                port=OAUTH_PORT,
            )
            print()
            print("Open this URL in your browser:")
            print()
            print(auth_url)
            print()
            print("After granting permissions, you'll be redirected to localhost (it will fail).")
            print("Copy the full redirect URL from the browser address bar, then run:")
            print()
            print(f"  uv run python -m auth --code '<redirect_url>'")
            print()
        elif args.code:
            # Phase 2: exchange code using saved PKCE state
            authenticate(
                credentials_path=credentials_path,
                token_path=TOKEN_FILE,
                scopes=SCOPES,
                code=args.code,
                port=OAUTH_PORT,
            )
            print()
            print(f"Authentication complete. {TOKEN_FILE} created.")
        else:
            # Auto mode — needs a browser
            if not _can_open_browser():
                print("No browser available — use --remote instead.")
                sys.exit(1)
            authenticate(
                credentials_path=credentials_path,
                token_path=TOKEN_FILE,
                scopes=SCOPES,
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
