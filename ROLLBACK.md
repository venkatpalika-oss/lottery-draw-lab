# Rollback runbook: v1 → v0.9-compat

Use only if v1 is live on `main` and must be withdrawn. Nothing in this branch deploys by itself.

## What this build is

`rollback/v0.9-compat` is production commit `29409dc` (v0.9) with only these changes:

- **index.html**: a tolerant loader that reads data written by v1.
  - Converts v1's `sets` back to `tickets` without loss. v1 reads either name.
  - Keeps every field it does not understand, such as v1's spending log, so those fields are written back unchanged.
  - Never invents a cost for number sets saved in v1. They show "cost not recorded" and count 0 toward spending.
  - If stored data is unreadable or has to be repaired, keeps the original text under `drawlab2.backup`, so nothing is overwritten.
  - v1 links (`#sets`, `#compare`) open the matching v0.9 screens.
  - The Budget screen says how many v1 spending entries are being kept.
- **sw.js**: offline cache renamed `drawlab-v9`, so phones replace the v1 cache.
- **tests/rollback_scenarios.py** and this file.

The v0.9 interface and behaviour are otherwise unchanged.

## Known limits (by design, not defects)

- Data that v1 already removed cannot be restored by this build: syndicate members, ticket price, and per-ticket prizes recorded before v1.
- Costs or prizes entered on tickets during a rollback period are dropped by v1's migration when v1 returns. v1 does not convert them into spending-log entries.
- v1 spending-log entries are kept but not displayed while the rollback build is live.
- `drawlab2.backup` is written once and never overwritten.

## Before deploying

1. Owner approval for the rollback, in writing.
2. `python3 tests/rollback_scenarios.py` on this branch: the ROLLBACK GATE must be all passed.
3. Note the current `main` SHA as the roll-forward point.

## Deploy (no force-push; history keeps v1)

```bash
git fetch origin
git checkout main && git pull --ff-only
git rm -r -q .
git checkout origin/rollback/v0.9-compat -- .
git diff --cached --stat
git diff --cached --quiet origin/rollback/v0.9-compat && echo "tree matches rollback branch"
git commit -m "Rollback: deploy rollback/v0.9-compat (<rollback SHA>)"
git push origin main
```

The push to `main` triggers the existing "Deploy to GitHub Pages" workflow.

## Verify (within 15 minutes)

1. Actions: the Pages run for the new `main` commit has the status "success".
2. `https://venkatpalika-oss.github.io/lottery-draw-lab/sw.js` starts with `const C='drawlab-v9'`. The CDN can take up to 10 minutes.
3. On a test phone that had v1:
   - open the app online, close it and open it again;
   - the tab bar shows **Tickets**;
   - saved sets are present on Tickets and in Checker;
   - Budget shows the "kept on this device" note if the phone had spending entries;
   - every screen opens.
4. Airplane mode: close and reopen the app. It must load.
5. The Android app loads the live site, so it rolls back on its next online launch. No Play update is needed.

## Abort conditions

Stop and roll forward to the previous `main` SHA, using the same deploy steps with that SHA instead of the rollback branch, if any of these happen:

- the Pages deploy fails, or `sw.js` is not `drawlab-v9` after 15 minutes;
- any screen is blank or shows a script error on the test phone;
- saved number sets are missing or changed;
- the app does not reopen offline.

## Recovery rules

- **Never tell users to clear site or app data.** All data lives only on their phone, and neither build deletes it.
- Redeploying either build is safe in any order. Repeated v1 → rollback → v1 transitions were tested with no data loss.
- If a phone's stored data was corrupt, the original is in `localStorage['drawlab2.backup']`.

## Roll forward to v1 again

Use the same deploy steps with the v1 SHA. v1's cache name (`drawlab-v8`) differs from `drawlab-v9`, so phones switch back. Number sets and spending-log entries carry over.
