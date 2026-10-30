"""Service-worker cache tests for Lottery Draw Lab.

Run from the repository root:  python3 tests/sw_tests.py
Needs Python Playwright with Chromium.

The site is served the way GitHub Pages serves it: every response carries Cache-Control: max-age=600
and Last-Modified, so the browser keeps pages in its HTTP cache for 10 minutes. Builds are swapped in
place on one origin, as a deploy does, while the browser profile (HTTP cache, service worker, storage)
persists. This reproduces the stale-HTML problem seen on production on 2026-10-30 and checks the fix.
"""
import asyncio, functools, http.server, json, os, shutil, socket, subprocess, sys, tempfile, threading
from pathlib import Path
from playwright.async_api import async_playwright

REPO = Path(__file__).resolve().parent.parent
def sha(ref): return subprocess.run(["git", "-C", REPO, "rev-parse", ref], capture_output=True, text=True, check=True).stdout.strip()
OLD09, V1PROD, ROLLBACK, FIX = sha("29409dc"), sha("9ae6418"), sha("dbf4d32"), "WORKTREE"
SW_NAME = {OLD09: "drawlab-v6", V1PROD: "drawlab-v8", ROLLBACK: "drawlab-v9", FIX: "drawlab-v10"}

import time
root = Path(tempfile.mkdtemp()); site = root / "lottery-draw-lab"; CLOCK = [time.time()]
def deploy(build, marker=None, sw_name=None, drop=None):
    """Replace the site with a build. marker: text added to index.html; sw_name: new cache name
    (changes sw.js so browsers install it); drop: a file removed to simulate a failed asset."""
    if site.exists(): shutil.rmtree(site)
    if build == FIX:
        shutil.copytree(REPO, site, ignore=shutil.ignore_patterns(".git", "tests"))
    else:
        site.mkdir(parents=True)
        subprocess.run(f"git -C '{REPO}' archive {build} | tar -x -C '{site}'", shell=True, check=True)
    if marker:
        p = site / "index.html"; p.write_text(p.read_text().replace("<head>", f"<head><!--{marker}-->", 1))
    if sw_name:
        p = site / "sw.js"; t = p.read_text(); p.write_text(t.replace(t[t.index("const C='") + 9:t.index("',F=")], sw_name, 1))
    if drop: (site / drop).unlink()
    CLOCK[0] += 60   # each deploy is later than the last, so Last-Modified always changes (1-second resolution)
    for p in site.rglob("*"): os.utime(p, (CLOCK[0], CLOCK[0]))

class PagesLike(http.server.SimpleHTTPRequestHandler):
    def end_headers(self): self.send_header("Cache-Control", "max-age=600"); super().end_headers()
    def log_message(self, *a): pass
s = socket.socket(); s.bind(("127.0.0.1", 0)); PORT = s.getsockname()[1]; s.close()
srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), functools.partial(PagesLike, directory=str(root)))
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{PORT}/lottery-draw-lab/"

results = []
def check(name, ok, detail=""): results.append((name, bool(ok), detail)); return ok

CACHE_HTML = """async (name) => { const c = await caches.open(name); const out = {};
  for (const p of ['/lottery-draw-lab/', '/lottery-draw-lab/index.html']) { const r = await c.match(p); out[p] = r ? await r.text() : null }
  return out }"""

async def profile(p):
    b = await p.chromium.launch(); ctx = await b.new_context(viewport={"width": 390, "height": 844})
    pg = await ctx.new_page(); errs = []; pg.on("pageerror", lambda e: errs.append(str(e)[:150]))
    return b, ctx, pg, errs

async def settle(pg, name, tries=24):
    """Reload until the worker with cache `name` controls the page (a real user reopening the app)."""
    keys = []
    for _ in range(tries):
        await pg.wait_for_timeout(400)
        keys = await pg.evaluate("caches.keys()")
        if keys == [name] and await pg.evaluate("!!navigator.serviceWorker.controller"): break
        # the browser's own update check on page load is throttled unpredictably in automation; request it explicitly
        await pg.evaluate("navigator.serviceWorker.getRegistration().then(r=>r&&r.update()).catch(()=>{})")
        await pg.wait_for_timeout(300); await pg.reload()
    return keys

async def main():
    async with async_playwright() as p:
        # 1. Control: the production worker (9ae6418) stores the stale page right after a deploy.
        b, ctx, pg, errs = await profile(p)
        deploy(V1PROD, marker="build-A"); await pg.goto(URL); await settle(pg, "drawlab-v8")
        await pg.reload(); await pg.wait_for_timeout(300)       # page now held in the browser's HTTP cache
        deploy(V1PROD, marker="build-B", sw_name="drawlab-control-next")
        await pg.reload(); keys = await settle(pg, "drawlab-control-next")
        html = await pg.evaluate(CACHE_HTML, "drawlab-control-next")
        check("control: old worker reproduces stale index.html after a deploy",
              keys == ["drawlab-control-next"] and html["/lottery-draw-lab/index.html"] and "build-A" in html["/lottery-draw-lab/index.html"],
              {k: (v and ("build-B" if "build-B" in v else "build-A" if "build-A" in v else "?")) for k, v in html.items()})
        await b.close()

        # 2. Fix: the same sequence with the corrected worker stores only the new page.
        b, ctx, pg, errs = await profile(p)
        deploy(FIX, marker="build-A"); await pg.goto(URL); await settle(pg, "drawlab-v10")
        await pg.reload(); await pg.wait_for_timeout(300)
        deploy(FIX, marker="build-B", sw_name="drawlab-fix-next")
        await pg.reload(); keys = await settle(pg, "drawlab-fix-next")
        html = await pg.evaluate(CACHE_HTML, "drawlab-fix-next")
        check("fix: new worker installed and old cache replaced", keys == ["drawlab-fix-next"], keys)
        for path in ("/lottery-draw-lab/", "/lottery-draw-lab/index.html"):
            check(f"fix: cached {path} is the new build", html[path] and "build-B" in html[path], (html[path] or "")[:80])
        check("fix: the page shown online is the new build", "build-B" in await pg.content())
        await ctx.set_offline(True); await pg.reload(); await pg.wait_for_timeout(500)
        check("fix: reopens offline with the new build", "build-B" in await pg.content() and not errs, errs[:2])
        await pg.goto(URL + "index.html"); await pg.wait_for_timeout(400)
        check("fix: index.html opens offline with the new build", "build-B" in await pg.content())
        await pg.goto(URL + "?utm=x"); await pg.wait_for_timeout(400)
        check("fix: an uncached page address falls back to the app offline", "build-B" in await pg.content())
        await ctx.set_offline(False); await b.close()

        # 3. Atomic install: a missing essential file leaves the previous worker and complete cache in charge.
        b, ctx, pg, errs = await profile(p)
        deploy(FIX, marker="build-A"); await pg.goto(URL); await settle(pg, "drawlab-v10")
        deploy(FIX, marker="build-B", sw_name="drawlab-broken-next", drop="fonts/plex-mono-600.woff2")
        for _ in range(4): await pg.reload(); await pg.wait_for_timeout(600)
        keys = await pg.evaluate("caches.keys()")
        reg = await pg.evaluate("navigator.serviceWorker.getRegistration().then(r=>({active:r.active&&r.active.scriptURL,installing:!!r.installing,waiting:!!r.waiting}))")
        check("atomic: failed install leaves no partial cache", keys == ["drawlab-v10"], keys)
        n = await pg.evaluate("caches.open('drawlab-v10').then(c=>c.keys()).then(k=>k.length)")
        check("atomic: previous cache still complete (13 files)", n >= 13, n)
        check("atomic: no new worker waiting or installing", not reg["waiting"] and not reg["installing"], reg)
        await ctx.set_offline(True); await pg.reload(); await pg.wait_for_timeout(500)
        check("atomic: app still reopens offline from the previous worker's cache",
              await pg.evaluate("!!document.querySelector('[data-tab=sets]')") and not errs, errs[:2])
        await ctx.set_offline(False); await b.close()

        # 4. Real upgrades with browser caching: v0.9 -> fix, production v1 -> fix, and rollback compatibility.
        PRE = [[3, 5, 20, 26, 34, 45], [3, 4, 20, 32, 36, 47]]
        for label, start in (("v0.9 (29409dc)", OLD09), ("production v1 (9ae6418)", V1PROD)):
            b, ctx, pg, errs = await profile(p)
            deploy(start); await pg.goto(URL); await settle(pg, SW_NAME[start])
            if start == OLD09:
                await pg.evaluate("""s => { const o = JSON.parse(localStorage.getItem('drawlab2') || '{}');
                    o.groups = [{id:'u1', game:'marksix', after:'', tickets:s, price:10, created:'2026-10-30T00:00:00Z', won:0}];
                    o.syn = {name:'Office', members:[{name:'A', paid:5}], prize:0}; localStorage.setItem('drawlab2', JSON.stringify(o)) }""", PRE)
            else:
                await pg.evaluate("""s => { const o = JSON.parse(localStorage.getItem('drawlab2') || '{}');
                    o.groups = [{id:'u1', game:'marksix', after:'', created:'2026-10-30T00:00:00Z', sets:s}];
                    o.log = [{id:'l1', date:'2026-10-30', spent:20, won:0}]; localStorage.setItem('drawlab2', JSON.stringify(o)) }""", PRE)
            await pg.reload(); await pg.wait_for_timeout(400)
            deploy(FIX); keys = await settle(pg, "drawlab-v10", tries=30)
            check(f"upgrade from {label}: cache replaced by drawlab-v10", keys == ["drawlab-v10"], keys)
            html = await pg.evaluate(CACHE_HTML, "drawlab-v10")
            check(f"upgrade from {label}: both cached pages are v1",
                  all(v and 'data-tab="sets"' in v for v in html.values()), {k: bool(v and 'data-tab="sets"' in v) for k, v in html.items()})
            for _ in range(3):   # online pages may stay stale for the old worker's lifetime; reopen as a user would
                if await pg.evaluate("!!document.querySelector('[data-tab=sets]')"): break
                await pg.reload(); await pg.wait_for_timeout(500)
            st = json.loads(await pg.evaluate("localStorage.getItem('drawlab2')"))
            check(f"upgrade from {label}: saved number sets kept", [g.get("sets") for g in st["groups"]] == [PRE], st.get("groups"))
            if start == V1PROD: check(f"upgrade from {label}: spending log kept", len(st.get("log", [])) == 1, st.get("log"))
            else: check(f"upgrade from {label}: removed v0.9 fields gone", "syn" not in st and "price" not in st)
            await ctx.set_offline(True); await pg.reload(); await pg.wait_for_timeout(500)
            check(f"upgrade from {label}: reopens offline as v1", await pg.evaluate("!!document.querySelector('[data-tab=sets]')"))
            await ctx.set_offline(False)
            # rollback to the compatible build and forward again, with browser caching in place
            deploy(ROLLBACK); keys = await settle(pg, "drawlab-v9", tries=30)
            html = await pg.evaluate(CACHE_HTML, "drawlab-v9")
            check(f"{label}: rollback build installs (drawlab-v9)", keys == ["drawlab-v9"], keys)
            deploy(FIX); keys = await settle(pg, "drawlab-v10", tries=30)
            html = await pg.evaluate(CACHE_HTML, "drawlab-v10")
            check(f"{label}: forward again to fix (drawlab-v10), both cached pages v1",
                  keys == ["drawlab-v10"] and all(v and 'data-tab="sets"' in v for v in html.values()), keys)
            for _ in range(3):
                if await pg.evaluate("!!document.querySelector('[data-tab=sets]')"): break
                await pg.reload(); await pg.wait_for_timeout(500)
            st = json.loads(await pg.evaluate("localStorage.getItem('drawlab2')"))
            check(f"{label}: number sets kept after rollback and forward", [g.get("sets") for g in st["groups"]] == [PRE], st.get("groups"))
            check(f"{label}: no script errors", not errs, errs[:3])
            await b.close()
    srv.shutdown()

asyncio.run(main())
w = max(len(n) for n, _, _ in results)
for n, ok, d in results:
    print(("PASS " if ok else "FAIL ") + n.ljust(w) + ("" if ok else f"   -> {str(d)[:300]}"))
fails = sum(1 for _, ok, _ in results if not ok)
print(f"\n{len(results) - fails}/{len(results)} passed")
sys.exit(1 if fails else 0)
