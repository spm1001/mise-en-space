"""Ambient Application Default Credentials — the service-account door (mise-wasagu).

A Cloud Run service (Garni, agsp-rupiho) holds ambient SA credentials from
the metadata server: no token file exists anywhere. google.auth.default()
resolves that, workload identity, and GOOGLE_APPLICATION_CREDENTIALS with
one call. Opt-in is explicit (MISE_CREDENTIALS=ambient, token_store) and
never a fallback — a missing token must keep teaching setup_oauth, not
silently switch identity to whatever ADC the machine happens to hold.

A separate module rather than http_client because http_client is ratchet-
frozen at its baseline: the frozen file pays only a dispatch branch and
two refresh guards (the 2026-08-09 lesson — let the ratchet place code).

Spike evidence the route works: mit-garni README A4 — a metadata-server
token with drive.readonly listed the shared folder on Cloud Run first
try. Its finding 4 is the matching instrument note: gcloud USER ADC
carries only drive.file, so a LOCAL ambient Drive listing returns an
empty success, not a 403 — read a local zero as the credential's scope,
never as the folder's contents.
"""

from typing import Any

from oauth_config import ambient_scopes


def load_ambient_credentials() -> Any:
    """Mint credentials from ADC with mise's per-deployment scope tier."""
    import google.auth
    from google.auth.exceptions import DefaultCredentialsError

    try:
        creds, _project = google.auth.default(scopes=ambient_scopes())
    except DefaultCredentialsError as e:
        raise FileNotFoundError(
            "MISE_CREDENTIALS=ambient, but no Application Default Credentials "
            f"were found ({e}). On Cloud Run/GCE the metadata server provides "
            "them automatically; elsewhere set GOOGLE_APPLICATION_CREDENTIALS "
            "to a service-account key file, or unset MISE_CREDENTIALS to use "
            "the token file instead."
        ) from e
    return creds


def ambient_refresh_refusal() -> FileNotFoundError:
    """Teaching error for a refresh refusal in ambient mode.

    http_client's reload-then-retry path is token-file medicine: for
    ambient credentials the mint and the refresh are the same platform
    identity, so a refusal means the service account or its key is the
    problem — nothing inside mise can re-auth it.
    """
    return FileNotFoundError(
        "Ambient credentials were refused on refresh. The platform identity "
        "that minted them (metadata server / workload identity / the "
        "GOOGLE_APPLICATION_CREDENTIALS key) is where to look — setup_oauth "
        "and token files play no part in ambient mode."
    )
