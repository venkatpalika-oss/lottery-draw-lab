"""Rollback scenario tests for Lottery Draw Lab.

Run from the repository root:
    python3 tests/rollback_scenarios.py [OLD_REF] [V1_REF] [ROLLBACK_REF]
Defaults: 29409dc (v0.9 production), HEAD (v1 candidate, this branch), dbf4d32 (approved rollback/v0.9-compat).

Each build is served at its production path (/lottery-draw-lab/) from one local origin and swapped in
place, the way a GitHub Pages deploy replaces the site. The browser profile (localStorage, service worker,
caches) persists across swaps, like a real phone. Needs Python Playwright with Chromium.
"""
import asyncio, json, os, shutil, socket, subprocess, sys, tempfile, threading, http.server, functools
from pathlib import Path
from playwright.async_api import async_playwright

REPO = Path(__file__).resolve().parent.parent
OLD, V1, RB = (sys.argv[1:4] + [None] * 3)[:3]
OLD, V1, RB = OLD or "29409dc", V1 or "HEAD", RB or "dbf4d320d9938723ec8c967bd4334abfcaec2eb9"
SHA = {n: subprocess.run(["git", "-C", REPO, "rev-parse", r], capture_output=True, text=True, check=True).stdout.strip()
       for n, r in (("old", OLD), ("v1", V1), ("rb", RB))}
CACHE = {"old": "drawlab-v6", "v1": "drawlab-v10", "rb": "drawlab-v9"}
SCREENS = {"old": ["home", "insights", "test", "tickets", "score", "checker", "outcomes", "budget", "syndicate", "data"],
           "rb": ["home", "insights", "test", "tickets", "score", "checker", "outcomes", "budget", "syndicate", "data"],
           "v1": ["home", "insights", "test", "sets", "score", "compare", "outcomes", "budget", "data"]}

root = Path(tempfile.mkdtemp()); site = root / "lottery-draw-lab"
def deploy(build):
    if site.exists(): shutil.rmtree(site)
    site.mkdir(parents=True)
    subprocess.run(f"git -C '{REPO}' archive {SHA[build]} | tar -x -C '{site}'", shell=True, check=True)
    now = None
    for p in site.rglob("*"): os.utime(p, now)          # fresh timestamps, as a new deploy has

class H(http.server.SimpleHTTPRequestHandler):
    def end_headers(self): self.send_header("Cache-Control", "no-cache"); super().end_headers()
    def log_message(self, *a): pass
s = socket.socket(); s.bind(("127.0.0.1", 0)); PORT = s.getsockname()[1]; s.close()
srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), functools.partial(H, directory=str(root)))
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{PORT}/lottery-draw-lab/"

results = []
def check(name, ok, detail=""):
    results.append((name, bool(ok), detail)); return ok
def gate(n): return True

class Phone:
    """One browser profile that survives site swaps."""
    async def start(self, p):
        self.b = await p.chromium.launch(); self.ctx = await self.b.new_context(viewport={"width": 390, "height": 844})
        self.pg = await self.ctx.new_page(); self.errors = []
        self.pg.on("pageerror", lambda e: self.errors.append(str(e)[:160]))
        return self
    async def open(self, build):
        """Deploy a build and wait until this phone is running it (service worker replaced)."""
        deploy(build); self.build = build
        await self.pg.goto(URL)
        for _ in range(20):
            await self.pg.wait_for_timeout(500)
            keys = await self.pg.evaluate("caches.keys()")
            if keys == [CACHE[build]]: break
            await self.pg.reload()
        await self.pg.reload(); await self.pg.wait_for_timeout(700)
        check(f"[{self.tag}] {build}: service worker cache replaced by {CACHE[build]}", keys == [CACHE[build]], keys)
    async def store(self):
        return json.loads(await self.pg.evaluate("localStorage.getItem('drawlab2')") or "null")
    async def screens(self, label):
        bad = []; self.errors.clear()
        for h in SCREENS[self.build]:
            await self.pg.evaluate(f"location.hash='{h}'"); await self.pg.wait_for_timeout(180)
            t = await self.pg.evaluate("document.body.innerText")
            if len(t) < 200 or "undefined" in t or "NaN" in t: bad.append(h)
        check(f"[{self.tag}] {label}: all {len(SCREENS[self.build])} screens render", not bad, bad)
        check(f"[{self.tag}] {label}: no JavaScript errors", not self.errors, self.errors[:3])
    async def offline(self, label):
        await self.ctx.set_offline(True); await self.pg.reload(); await self.pg.wait_for_timeout(700)
        t = await self.pg.evaluate("document.body.innerText"); tab = "sets" if self.build == "v1" else "tickets"
        ok = await self.pg.evaluate(f"!!document.querySelector('[data-tab={tab}]')") and len(t) > 200
        check(f"[{self.tag}] {label}: reopens offline", ok and not self.errors, self.errors[:2])
        await self.ctx.set_offline(False); await self.pg.reload(); await self.pg.wait_for_timeout(500)
    async def click(self, hash_, sel, fill=None):
        await self.pg.evaluate(f"location.hash='{hash_}'"); await self.pg.wait_for_timeout(200)
        for k, v in (fill or {}).items(): await self.pg.fill(k, v)
        await self.pg.click(sel); await self.pg.wait_for_timeout(250)

def sets_of(st):
    """Every saved number set, keyed by group id, whichever field name the version used."""
    return {g["id"]: g.get("sets") or g.get("tickets") for g in (st or {}).get("groups", [])}

async def main():
    async with async_playwright() as p:
        # 1. Fresh rollback-build install
        ph = await (Phone().start(p)); ph.tag = "fresh"
        await ph.open("rb"); await ph.screens("fresh rollback build"); await ph.offline("fresh rollback build")
        await ph.click("tickets", "[data-act=saveTickets]")
        st = await ph.store(); check("[fresh] rollback build saves tickets with v0.9 fields",
                                     st["groups"] and set(st["groups"][0]) == {"id", "game", "after", "tickets", "price", "created", "won"}, st.get("groups"))
        await ph.b.close()

        # 2. Existing v0.9 data opened by the rollback build
        ph = await (Phone().start(p)); ph.tag = "v0.9 data"
        await ph.open("old")
        await ph.click("tickets", "[data-act=saveTickets]")
        await ph.click("syndicate", "[data-act=synAdd]")
        await ph.pg.evaluate("location.hash='checker'"); await ph.pg.wait_for_timeout(200)
        st_old = await ph.store()
        await ph.open("rb"); await ph.screens("v0.9 data in rollback build")
        st_rb = await ph.store()
        check("[v0.9 data] stored data unchanged by rollback build", st_rb == st_old, "")
        await ph.click("tickets", "[data-act=saveTickets]")
        st_rb2 = await ph.store()
        check("[v0.9 data] after a save: earlier groups, syndicate and settings identical",
              st_rb2["groups"][:-1] == st_old["groups"] and st_rb2["syn"] == st_old["syn"] and st_rb2["price"] == st_old["price"], "")
        await ph.b.close()

        # 3-6. v0.9 -> v1 -> rollback -> v1 -> rollback -> v1, with user activity in every phase
        ph = await (Phone().start(p)); ph.tag = "cycle"
        await ph.open("old"); await ph.click("tickets", "[data-act=saveTickets]")
        expect = sets_of(await ph.store()); logs = 0
        for step, build in enumerate(["v1", "rb", "v1", "rb", "v1"], 1):
            label = f"step {step} -> {build}"
            await ph.open(build)
            st = await ph.store()
            check(f"[cycle] {label}: every saved number set preserved ({len(expect)} groups)", sets_of(st) == expect,
                  {k: (k in sets_of(st)) for k in expect})
            check(f"[cycle] {label}: spending-log entries preserved ({logs})", len(st.get("log") or []) == logs, st.get("log"))
            if build == "v1":
                check(f"[cycle] {label}: v1 makes no backup (data from either build is valid)",
                      await ph.pg.evaluate("Object.keys(localStorage).filter(k=>k.startsWith('drawlab2.backup')).length") == 0)
            if build == "rb":
                mem = await ph.pg.evaluate("({groups:S.groups,gen:S.gen})")
                check(f"[cycle] {label}: newer field names read losslessly (in memory, storage untouched until a save)",
                      all("sets" not in g and isinstance(g.get("tickets"), list) for g in mem["groups"])
                      and "sets" not in mem["gen"] and {g["id"]: g["tickets"] for g in mem["groups"]} == expect, "")
                check(f"[cycle] {label}: nothing backed up (v1 data is valid)",
                      await ph.pg.evaluate("localStorage.getItem('drawlab2.backup')") is None)
                await ph.pg.evaluate("location.hash='budget'"); await ph.pg.wait_for_timeout(200)
                t = await ph.pg.evaluate("document.body.innerText")
                check(f"[cycle] {label}: Budget notes kept spending entries and unrecorded cost",
                      (not logs or "kept on this device" in t) and "cost not recorded" in t, "")
                for new, old in (("sets", "tickets"), ("compare", "checker")):
                    await ph.pg.evaluate(f"location.hash='{new}'"); await ph.pg.wait_for_timeout(200)
                    check(f"[cycle] {label}: link #{new} opens #{old}", await ph.pg.evaluate("location.hash") == "#" + old)
            await ph.screens(label); await ph.offline(label)
            # user activity in this version
            if build == "v1":
                await ph.click("sets", "[data-act=saveSets]")
                await ph.click("budget", "[data-act=addLog]", {"#logSpent": "20", "#logWon": "5"}); logs += 1
            else:
                await ph.click("tickets", "[data-act=saveTickets]")
            expect = sets_of(await ph.store())
        await ph.b.close()

        # 7. Corrupt or incomplete storage
        cases = {
            "not JSON": "{oops",
            "JSON array": "[1,2,3]",
            "JSON string": '"hello"',
            "groups not a list": json.dumps({"game": "marksix", "groups": "x", "log": [{"id": "a", "date": "2026-10-01", "spent": 5, "won": 0}]}),
            "bad group entries": json.dumps({"game": "marksix", "groups": [None, 7, {"id": "ok", "game": "marksix", "after": "", "created": "2026-10-01", "sets": [[1, 2, 3, 4, 5, 6], ["x"]]}]}),
            "gen not an object": json.dumps({"game": "marksix", "gen": 5}),
            "empty syndicate": json.dumps({"game": "marksix", "syn": {"members": []}}),
            "unknown game": json.dumps({"game": "zzz", "k": "six"}),
            "empty object": "{}",
        }
        for name, raw in cases.items():
            for build in ("rb", "v1"):
                ph = await (Phone().start(p)); ph.tag = f"corrupt/{build}"
                await ph.open(build)
                await ph.pg.evaluate("r=>localStorage.setItem('drawlab2', r)", raw)
                await ph.pg.reload(); await ph.pg.wait_for_timeout(600)
                await ph.screens(f"{name}")
                if build == "v1":
                    bk = await ph.pg.evaluate("localStorage.getItem('drawlab2.backup')")
                    # v1 removes the syndicate field by policy, so an empty one is not corruption for v1
                    check(f"[corrupt/v1] {name}: original kept in drawlab2.backup" if name not in ("empty object", "empty syndicate") else f"[corrupt/v1] {name}: nothing to repair, no backup",
                          (bk == raw) if name not in ("empty object", "empty syndicate") else bk is None, bk)
                if build == "rb":
                    st = await ph.store(); bk = await ph.pg.evaluate("localStorage.getItem('drawlab2.backup')")
                    repaired = name not in ("empty object",)
                    check(f"[corrupt/rb] {name}: original kept in drawlab2.backup", (bk == raw) if repaired else bk is None, bk)
                    if name == "groups not a list":
                        await ph.click("tickets", "[data-act=saveTickets]")
                        st = await ph.store()
                        check("[corrupt/rb] groups not a list: spending log kept after next save", len(st.get("log", [])) == 1, st.get("log"))
                    if name == "bad group entries":
                        await ph.click("tickets", "[data-act=saveTickets]")
                        st = await ph.store()
                        check("[corrupt/rb] bad group entries: valid group and its valid set kept",
                              st["groups"][0]["id"] == "ok" and st["groups"][0]["tickets"] == [[1, 2, 3, 4, 5, 6]], st["groups"][:1])
                await ph.b.close()
    srv.shutdown()

asyncio.run(main())
w = max(len(n) for n, _, _ in results)
for n, ok, d in results:
    print(("PASS " if ok else "FAIL ") + n.ljust(w) + ("" if ok else f"   -> {str(d)[:300]}"))
g = [r for r in results if gate(r[0])]; info = [r for r in results if not gate(r[0])]
fails = sum(1 for _, ok, _ in g if not ok)
print(f"\nbuilds: v0.9={SHA['old'][:7]} v1={SHA['v1'][:7]} rollback={SHA['rb'][:7]}")
print(f"ROLLBACK + V1 STORAGE GATE: {len(g) - fails}/{len(g)} passed")
sys.exit(1 if fails else 0)
