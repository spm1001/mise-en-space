# Deploy test for the OAuth-client seam (mise-nujina) — for Sameer

**What you're checking:** that nothing changed for you. The seam only switches on when a kit sets `MISE_EN_SPACE_OAUTH_CLIENT` or `MISE_EN_SPACE_DATA_DIR`, and no kit does yet. So the test passes when both flavours behave exactly as they did this morning. The Mac is the valuable half, because its Keychain path was only unit-tested with mocks; tube (the phone's host) keeps tokens in files, and the throwaway-home proof already covered that.

**Before you start:** the new version has to be built and installed. A merge to main ships at the next assemble, so someone has to trigger that (a `/batterie:publish`, or a manual run of batterie's "Assemble plugins" workflow). A new Claude Code session then picks it up by auto-update within about ten minutes, or at once after `/batterie:update`. The version to expect is whatever that assemble stamps. It will be higher than 1.86.31.

## On the Mac (Claude Code in Terminal)

1. Start a fresh session anywhere and run `/batterie:version`. Both **mise** and **mise-home** should show the new version. If they still show 1.86.31, run `/batterie:update`, quit, and start again.
2. Look at the session-start text. There should be **no** "Mise MCP server needs setup" or "Mise Home — optional setup" warning.
3. Type: *"Use mise to find one Google Doc in my Drive and tell me which account answered."* You should see a doc title, answered by your **itv.com** address.
4. Type: *"Now do the same with mise-home."* You should see a doc title, answered by your **planetmodha.com** address.
5. Type: *"Call mise's setup_oauth, without force, then mise-home's."* Both should say **already authenticated**, and **no browser tab opens**. This is the step that exercises the Keychain.
6. Optional, Claude Desktop or Cowork: Desktop keeps its own plugin registry, which updates separately, so check its version first. Then ask questions 3 and 4 there.

## On the phone (Guéridon, which runs on tube)

Tube updates after banc's corpus-ablation finishes (the pass-holder's call). After that:

7. Start a new session from the phone and ask questions 3, 4 and 5 again. You should get the same two accounts, and "already authenticated" twice.

## What would mean something broke

Tell the mise-en-space session (or file on the mise board) if you see any of these:

- a sign-in prompt, or a browser tab opening in step 5;
- any error that mentions "OAuth client", "not configured" or `MISE_EN_SPACE`;
- the wrong account answering: itv.com from mise-home, or planetmodha.com from mise;
- a new "needs setup" warning at session start.

One false alarm to rule out first. If a token had genuinely expired or been revoked (a password or 2-step change on the account would do it), step 5 opens a browser for an honest reason. The check is whether the same step fails on the old version too.

## What you can't test yet

The seam itself: one engine, with a client handed to it from outside. There's no kit to switch it on until the repackaging (bds-jakemi). The proof in this folder ran it on a throwaway home instead.
