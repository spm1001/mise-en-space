# Dependabot triage — 2026-06-28 (mise-zemuwe)

13 open alerts on `spm1001/mise-en-space` default branch (4 high, 4 medium, 5 low),
flagged on the 0.7.9 push. All in `uv.lock`, all `runtime` scope.

## Headline

**Every alert is a transitive dependency — none is a direct dep of mise.** The handoff
guessed they'd live in the extraction tail (onnxruntime/magika/pdf*) and be mitigated by
the optional `extraction` extra. Verified false on both counts:

- They're the **`mcp` web/auth stack** (starlette, python-multipart, pyjwt,
  pydantic-settings) and **`google-auth`** (cryptography) — not the ML/PDF deps.
- `mcp[cli]` and `jeton`→`google-auth` are **core**, so these ship in *both* the full
  and slim builds. The `extraction` extra mitigates **none** of them.

Reverse-dependency tree (`uv tree --invert`):

| Package | High? | Pulled in by | Fixed in |
|---|---|---|---|
| starlette | H + 1 low | `mcp` | 1.3.1 |
| python-multipart | H + 3 low | `mcp` | 0.0.30 |
| pyjwt | H + 3 med + 1 low | `mcp[crypto]` | 2.13.0 |
| cryptography | H | `google-auth` (core) + `pdfminer-six` (extraction) | 48.0.1 |
| pydantic-settings | 1 med | `mcp` | 2.14.2 |

## Per-high disposition

1. **starlette GHSA-82w8-qh3p-5jfq** (form limits silently ignored) — *transitive via mcp.*
   Reachable **only in `--remote` mode** (StreamableHTTP); stdio (the daily path) never
   starts the web server. Remote is single-user behind Tailscale Funnel, not a public
   endpoint. Real-world risk low; **bump to ≥1.3.1** when next shipping.
2. **python-multipart GHSA-5rvq-cxj2-64vf** (quadratic querystring DoS) — *transitive via mcp.*
   Same reachability as starlette: remote-mode form parsing only. **Bump to ≥0.0.30.**
3. **pyjwt GHSA-xgmm-8j9v-c9wx** (JWK-as-HMAC forged HS256) — *transitive via mcp[crypto].*
   mise authenticates via jeton/google-auth, **not** mcp's JWK/OAuth path — vulnerable code
   present but **unreached**. **Bump to ≥2.13.0** (clears 4 of the 5 pyjwt alerts).
4. **cryptography GHSA-537c-gmf6-5ccf** (vulnerable OpenSSL in wheels) — *transitive via
   google-auth, **core/always-on**.* The only high in the always-active path (TLS + JWT
   signing for Google OAuth). Practical risk for an outbound-only HTTPS client is low, but
   this is the **top-priority bump: ≥48.0.1.**

## Moderates / lows (one-line posture)

- 3 further **pyjwt** mediums + 1 low (JWKClient SSRF, base64 DoS, algorithm-allowlist
  bypass) — all in the unreached mcp JWK path; the **pyjwt ≥2.13.0** bump clears them.
- **pydantic-settings** medium (NestedSecrets symlink escape) — transitive via mcp, the
  NestedSecrets source isn't used by mise; unreached. Bump ≥2.14.2 opportunistically.
- 3 **python-multipart** lows + 1 **starlette** low — remote-mode-only, cleared by the
  bumps above.

## Recommended action (deferred — see below)

All fixes are transitive, so the lever is forcing the locked versions up:

```bash
uv lock --upgrade-package starlette --upgrade-package python-multipart \
        --upgrade-package pyjwt --upgrade-package cryptography \
        --upgrade-package pydantic-settings
uv run --all-extras python -m pytest   # regression
```

If `mcp 1.27.0`'s constraints pin any below the fixed version, bump `mcp` itself instead.
A Dependabot config to *mute* noise is **not** warranted — these aren't extra-gated noise,
they're core-shipped transitives with real (if low-reachability) fixes.

## Why not applied tonight

`uv.lock` is **vendored content** → bumping it is a shippable change, and a content-change
without a version bump arms the marketplace ratchet (the documented SPOF). A second Claude
is concurrently harmonising marketplace versions, so a lock change here would collide.
**Disposition: triaged and specified; apply the bump as part of the next coordinated
release**, not as a standalone push.
