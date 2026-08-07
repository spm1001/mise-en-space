# Verdict: resolving a-family Gmail URL tokens (mise-gilojo)

**Verdict: exploitable-as-fallback — via rendered-DOM harvest in a logged-in browser, not via the sync API or any constructed URL.** A real browser is required for the stable route. One matched pair recorded (the first).

## The matched pair

From a real search-view URL supplied by Sameer on 2026-08-07 (`#search/hasan.patel%40itv.com/QgrcJHsbdJTGBvvQznvJDWRjKHcsnKvmpKQ`), opened in tube's logged-in passe-chrome:

| Identifier | Value |
|---|---|
| Web token | `QgrcJHsbdJTGBvvQznvJDWRjKHcsnKvmpKQ` |
| Raw decode (Arsenal algorithm, no prefix fix-up) | `a:r-8125895545114462359` |
| Real thread id (`data-legacy-thread-id`) | `19fd641a90e83369` = 1,872,763,085,319,058,281 |
| Visible message (`data-message-id` / `data-legacy-message-id`) | `#msg-f:1872766488607412380` = `19fd6732f4b3509c` ✓ (f-transform reconfirmed) |
| Thread subject | "Typical client and agency contract samples" |

The r-number is **4.34× larger** than the real thread id decimal. No arithmetic transform exists — the r-number is a client-assigned compose-time id, settling the question mise-jujoti left open ("the identity transform fails; that does not prove no transform exists" — this pair does, for any monotone-ish transform, and the client-assigned mechanism explains why).

Note the raw decode is `a:r-N` with **no `thread`/`msg` prefix** — the decoder's `thread-` fix-up is a guess. In a search-view URL the token may name a message, not the thread.

## What was tried, in order

1. **Constructed Show-original URL** (`?ik=<ik>&view=om&permmsgid=msg-a:r-8125895545114462359`): **REFUTED.** With the correct `ik` the surface answers 200 with "The message that you requested doesn't exist." The URL-token r-number is not a valid `msg-a` permmsgid. (Without `ik`, `view=om` 404s outright — `ik` is mandatory on that surface. Positive control: the known-good `msg-f:1872845353272970994` URL rendered Show-original with the expected Message-ID in the same browser, so surface and `ik` were both proven before reading the msg-a null as a finding. `ik` is per-account stable across browsers — the Mac-harvested `2bb48b24a5` worked in tube's Chrome.)
2. **`drafts.get` with the r-number**: 404 (probed earlier the same day; recorded on `validation._decode_gmail_web_token`). The r-number reaches no API surface.
3. **Rendered-DOM harvest**: **WORKS.** Open the permalink in a logged-in Chrome, wait for the SPA, read attributes:
   - `[data-legacy-thread-id]` → API thread id
   - `[data-legacy-message-id]` → API message ids (expanded messages only)
   - `document.title` / `.hP` → subject
   ~10 seconds, no clicks, no Show-original menu. Recipe used:
   ```
   passe run --reuse-tab -c 'goto <permalink>; wait 8;
     eval JSON.stringify([...document.querySelectorAll("[data-legacy-thread-id]")].map(e => e.getAttribute("data-legacy-thread-id")))'
   ```
4. **Sync-API capture / cookie-only replay: deliberately not pursued.** The DOM route dominates it: same browser requirement, but a *rendered-DOM attribute* is a far stabler surface than an undocumented internal sync endpoint (the spike's own guard called that surface UNSTABLE). Cookie-only replay without rendering remains unproven either way; nothing structural rules it out, but Gmail's server-rendered basic-HTML view was retired in 2024, so any browserless route would have to speak the internal API — exactly the fragility the guard warns about.

## Why the browser is required

The ids exist only in the rendered SPA DOM (or behind the internal sync API). There is no server-rendered Gmail surface left that carries them, and the a-family numbers reach no public API. A logged-in CDP browser (passe) is therefore the minimum viable instrument — and its session lapses to an Okta wall on ITV accounts, so the route is a *fallback with a human re-auth cost*, never a primary path.

## What shipped instead (suite 1.36.0, mise-lerulo)

The deterministic routes that need no browser: `fetch()` now accepts RFC 822 Message-IDs (resolved via `rfc822msgid:`) and `msg-f` Show-original URLs (decimal→hex), and a-family refusals attach recent `in:sent` candidates. The browser recipe above is only for the residue: an a-family URL whose thread isn't in the candidates and whose Message-ID nobody has copied.

## Addendum, same day: the recipe is now wired (mise-johata)

`adapters/gmail_browser.py` implements the DOM harvest as an automatic fetch fallback — a-family fragment URLs resolve zero-click where a logged-in CDP Chrome answers (proven live: the matched-pair URL above resolved and deposited in 6.8s through `do_fetch`, disclosure cue attached). Fail-open throughout, so machines without a browser keep the refusal + candidates unchanged. Two implementation traps hit live and recorded in the module: Chrome's `/json/new` HTTP endpoint takes its URL raw after the `?` (a `url=` key-value silently leaves the tab on `about:blank`) — navigate via `Page.navigate` over the websocket instead; and an SSO-wall verdict needs a persistent streak of observations, because a healthy silent refresh legitimately bounces through `accounts.google.com` mid-load.
