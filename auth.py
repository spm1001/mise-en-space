#!/usr/bin/env python3
"""
OAuth Authentication for mise-en-space.

Fetches OAuth client credentials from GCP Secret Manager, then runs the
OAuth flow to create a local token.json.

Usage:
    uv run python -m auth                    # Auto mode (opens browser)
    uv run python -m auth --manual           # Manual mode (copy-paste URL)
    uv run python -m auth --project OTHER    # Use different GCP project

Prerequisites:
    - gcloud CLI installed and authenticated
    - Access to the GCP project containing the secret
"""

import sys
import argparse
import subprocess
import tempfile
from pathlib import Path

from itv_google_auth import authenticate

from oauth_config import (
    TOKEN_FILE,
    SCOPES,
    OAUTH_PORT,
    GCP_PROJECT,
    SECRET_NAME,
)


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
        help='Manual mode: copy-paste OAuth flow (for remote/SSH)'
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

    # Fetch credentials from Secret Manager
    print(f"Fetching credentials from {args.project}/{SECRET_NAME}...")
    credentials_json = fetch_credentials_from_secret_manager(args.project, SECRET_NAME)

    # Write to temp file for itv_google_auth (expects a path)
    with tempfile.NamedTemporaryFile(
        mode='w', suffix='.json', delete=False
    ) as tmp:
        tmp.write(credentials_json)
        tmp_path = tmp.name

    try:
        authenticate(
            credentials_path=tmp_path,
            token_path=TOKEN_FILE,
            scopes=SCOPES,
            manual_mode=args.manual or bool(args.code),
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
        # Clean up temp credentials file
        Path(tmp_path).unlink(missing_ok=True)


if __name__ == '__main__':
    main()
