"""mise_en_space — the blessed library door into mise.

mise is primarily an MCP server (search / fetch / do over stdio), but two
consumers use it in-process as a library: glaneur (nightly transcript
harvest) and Garni (Cloud Run agent workspaces). This package is the
contract for that door: one class, three verbs, credentials selected in
code. Everything else in the wheel (adapters/, extractors/, tools/, the
root modules) is reachable but unblessed — it can move without notice;
this surface moves deliberately.

The two entry points
--------------------
**Fetch-conversion** (`Mise.fetch`): give it a Drive file id, Gmail thread
id, or folder id (`recursive=True` walks the tree) and it deposits
converted content on disk, exactly as the MCP tool would:

    .mise/{type}--{title-slug}--{id-prefix}/
        manifest.json    # self-describing metadata, tab/heading/slide maps
        content.md       # the document, as markdown
        comments.md      # open comments, when any
        ...              # per-tab CSVs, thumbnails, raw attachments

The return value carries `path` to that folder plus `cues` (warnings,
comment counts, restore points). Deposits are the design, not a
convenience: manifests, merged-cell resolution, teaching errors and
comment extraction all come free by entering through the same machinery
the MCP uses. Read what you need from the deposit and discard it.

**Write-back** (`Mise.do`): `do("create", ...)` makes Docs / Sheets /
Forms / folders from markdown, CSV or a local file; `do("overwrite", ...)`
replaces a Doc's or Sheet's content (Sheets take `range=` in A1 notation).
The full operation set is `tools.OPERATIONS`; errors come back as dicts,
never exceptions (see "Error contract" below), and name what is missing.

Credentials — one identity per process
--------------------------------------
Pass at most one selector to the constructor:

    Mise()                        # env / keychain / token-file resolution
    Mise(ambient=True)            # ADC: Cloud Run metadata server, workload
                                  #   identity, GOOGLE_APPLICATION_CREDENTIALS.
                                  #   Service-account gates fire: search
                                  #   defaults to Drive, mailbox ops refuse.
    Mise(token_path="/path")      # guest mode: caller-owned token file,
                                  #   never written back, nothing persisted
    Mise(credentials=creds_obj)   # any google-auth Credentials object —
                                  #   the caller owns refresh and scopes

mise is a single-identity architecture (module-level clients, cached
services), so the selection is process-wide and the last constructed
Mise wins. Construct once, early. Selecting in code while MISE_TOKEN_PATH
or MISE_CREDENTIALS is set in the environment raises — two sources naming
an identity have no honest precedence. The ambient scope tier stays
env-selected (MISE_SCOPES=readonly) because it is fixed per deployment,
not per call site.

Installing the wheel
--------------------
mise's jeton dependency is declared as a bare `Requires-Dist: jeton` —
uv source maps do not ride wheel metadata — so consumers install it
themselves alongside the wheel:

    uv pip install mise_en_space-*.whl "jeton @ git+https://github.com/spm1001/jeton.git"

Error contract
--------------
`do()` returns the operation's result dict on success and
`{"error": True, "kind": ..., "message": ...}` on failure — the same
teaching errors the MCP surface emits, with the remedy in the message.
`fetch()` returns a FetchResult or FetchError dataclass; `search()`
returns a SearchResult whose `errors` list carries per-source failures.
Check, don't try/except: the machinery never raises for Workspace-side
failures, only for programming errors (unknown params, bad modes).
"""

from pathlib import Path
from typing import Any

import token_store
from adapters.http_client import clear_http_client, clear_sync_client
from models import FetchError, FetchResult, MiseError, SearchResult
from tools import OPERATIONS, do_fetch, do_search
from tools.dispatch import DO_PARAM_DEFAULTS as _DO_DEFAULTS
from tools.dispatch import run_operation

__all__ = [
    "OPERATIONS",
    "FetchError",
    "FetchResult",
    "Mise",
    "MiseError",
    "SearchResult",
]

# _DO_DEFAULTS is the full parameter surface of do(), mirroring server.py's
# do() wrapper — dispatch handlers index params with p["key"], so every key
# must be present (a partial dict raises KeyErrors dressed as INTERNAL errors).
# It is imported from tools.dispatch (as DO_PARAM_DEFAULTS) rather than kept
# here: run_operation needs the same defaults to tell a caller-supplied param
# from a signature default, and two copies of a 39-key table drift in silence.
# tests/unit/test_facade.py pins it against server.do's real signature, so a
# param added there without a matching entry fails loudly.


class Mise:
    """The blessed in-process handle on Google Workspace.

    See the module docstring for the credential modes and the deposit
    shape. `base_path` is where `.mise/` deposits land (and where
    `do(source=...)` reads them from); default is the process cwd.
    """

    def __init__(
        self,
        *,
        credentials: Any | None = None,
        token_path: Path | str | None = None,
        ambient: bool = False,
        base_path: Path | str | None = None,
    ) -> None:
        token_store.configure_identity(
            credentials=credentials, token_path=token_path, ambient=ambient
        )
        # Credentials bake into the HTTP clients at creation — a fresh
        # identity needs fresh clients, and a bare Mise() (back to
        # env-default) must not keep serving a predecessor's injection.
        clear_http_client()
        clear_sync_client()
        self.base_path: Path | None = Path(base_path) if base_path else None

    def search(
        self,
        query: str = "",
        sources: list[str] | None = None,
        max_results: int = 20,
        base_path: Path | None = None,
        folder_id: str | None = None,
        type: str | None = None,  # shadows the builtin for parity with the tool surface
        raw_query: str | None = None,
        time_min: str | None = None,
        time_max: str | None = None,
        calendar_id: str | None = None,
    ) -> SearchResult:
        """Find files/emails/events; metadata plus preview, results deposited.

        Passes through to tools.do_search — see its docstring for the
        query grammar (plain query is AND across words; raw_query is
        Drive's own language). With `folder_id` and no query, lists that
        folder's immediate children — the discovery half of a folder walk.
        `time_min`/`time_max` (ISO date or datetime, any range — historical
        fine) bound the calendar window with no query term needed: the
        clash-check and event-backfill route (mise-riduka).
        """
        return do_search(
            query=query,
            sources=sources,
            max_results=max_results,
            base_path=base_path or self.base_path,
            folder_id=folder_id,
            type=type,
            raw_query=raw_query,
            time_min=time_min,
            time_max=time_max,
            calendar_id=calendar_id,
        )

    def fetch(
        self,
        file_id: str,
        base_path: Path | None = None,
        attachment: str | None = None,
        recursive: bool = False,
        tabs: list[str] | None = None,
        suggestions: str = "accepted",
        raw: bool = False,
        thumbnails: bool = True,
        crops: bool = True,
    ) -> FetchResult | FetchError:
        """Deposit one artefact's converted content; return path + cues.

        Accepts Drive file ids, Gmail thread/message ids, and folder ids
        (`recursive=True` for the full tree, depth 5). The deposit folder
        layout is in the module docstring; `result.path` names it.
        `thumbnails=False` skips page/slide thumbnail rendering, and
        `crops=False` skips PDF embedded-graphic crop extraction (plus its
        content.md anchors) — together the wall-clock levers for text-only
        corpus hydration (mise-giwawa, mise-tanoti).
        """
        return do_fetch(
            file_id,
            base_path=base_path or self.base_path,
            attachment=attachment,
            recursive=recursive,
            tabs=tabs,
            suggestions=suggestions,
            raw=raw,
            thumbnails=thumbnails,
            crops=crops,
        )

    def do(self, operation: str, **params: Any) -> dict[str, Any]:
        """Act on Workspace — create, overwrite, move, comment, and friends.

        `operation` is one of tools.OPERATIONS; params are the same names
        the MCP tool takes (title, content, folder_id, file_id, source,
        range, ...). Unknown params raise TypeError rather than being
        silently dropped. Returns the operation's result dict, or an
        error dict whose message teaches the fix.
        """
        unknown = sorted(set(params) - set(_DO_DEFAULTS))
        if unknown:
            raise TypeError(
                f"do({operation!r}) got unknown parameter(s) {unknown}. "
                f"Valid: {sorted(_DO_DEFAULTS)}"
            )
        full = dict(_DO_DEFAULTS)
        if self.base_path is not None:
            full["base_path"] = str(self.base_path)
        full.update(params)
        return run_operation(operation, full)
