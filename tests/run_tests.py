"""Lottery Draw Lab automated tests.

Run:  python3 tests/run_tests.py
Needs Python Playwright with Chromium. Exits non-zero on any failure.

Covers:
  1. Maths: exact hypergeometric odds, probabilities sum to 1, CSV parsing.
  2. Every screen renders in a phone-sized headless browser with no script errors.
  3. No network requests outside the app's own files.
  4. v1 policy rules: no syndicate screen, no ticket/price/prize-purchase wording,
     saved number sets are never linked to money, spending log is self-reported only.
  5. Legacy routes redirect; old saved data migrates without the removed fields.
  6. Offline: the service-worker cache list names files that exist.
"""
import asyncio, json, os, re, sys
from pathlib import Path
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
URL = (ROOT / "index.html").as_uri()
SCREENS = ["home", "insights", "test", "sets", "score", "compare", "outcomes", "budget", "data"]
# Words that describe real-money participation and must not appear in visible text.
BANNED = [r"\bticket", r"\bsyndicate", r"\bprice\b", r"\bper ticket", r"\bbuy\b", r"\bbet\b",
          r"\bwager", r"\bjackpot", r"prize won", r"\bpot\b", r"play now", r"\bwinnings\b"]

results = []
def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        pg = await ctx.new_page()
        errors, external = [], []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("request", lambda r: external.append(r.url) if not r.url.startswith(("file:", "data:", "blob:")) else None)
        await pg.goto(URL)
        await pg.wait_for_timeout(800)

        # 1. maths
        m = await pg.evaluate("""() => {
          const S6=[...Array(7).keys()].map(m=>hyper(m,6,49)), total=S6.reduce((a,b)=>a+b,0);
          const jack=Math.round(1/hyper(6,6,49)); const p3=S6.slice(3).reduce((a,b)=>a+b,0);
          const pr=parseRows('24/10/2026, 3, 11, 19, 27, 38, 45, 12\\n2026-10-27 1 2 3 4 5 6\\nbad row',false);
          return {total,jack,per:Math.round(p3*1000),parsed:pr.out,skipped:pr.skipped}}""")
        check("odds sum to 1", abs(m["total"] - 1) < 1e-9, m["total"])
        check("6/49 all-match odds = 1 in 13,983,816", m["jack"] == 13983816, m["jack"])
        check("981 of 1,000 combinations match 2 or fewer", 1000 - m["per"] == 981, m["per"])
        check("CSV parsing (dd/mm/yyyy, bonus ignored, bad row skipped)",
              m["parsed"] == [{"date": "2026-10-24", "nums": [3, 11, 19, 27, 38, 45]},
                              {"date": "2026-10-27", "nums": [1, 2, 3, 4, 5, 6]}] and m["skipped"] == 1, m)

        # 2 & 4. render every screen, collect visible text
        texts = {}
        for h in SCREENS:
            await pg.evaluate(f"location.hash='{h}'")
            await pg.wait_for_timeout(200)
            t = await pg.evaluate("document.body.innerText")
            texts[h] = t
            check(f"screen '{h}' renders", len(t) > 200 and "undefined" not in t and "NaN" not in t, len(t))
        alltext = "\n".join(texts.values())
        for pat in BANNED:
            hits = re.findall(r".{0,25}" + pat + r".{0,25}", alltext, re.I)
            check(f"no visible '{pat}' wording", not hits, hits[:3])
        check("tab bar shows 'Number sets'", "Number sets" in texts["home"])
        check("Number sets screen has 'Generate number sets'", "Generate number sets" in texts["sets"])
        check("Number sets screen has 'Save sets for comparison'", "Save sets for comparison" in texts["sets"])
        check("no currency shown on Number sets screen", not re.search(r"HK\$|£|€|\$\s?\d", texts["sets"]), "")
        check("responsible-play notes present", "set a budget" in texts["home"] and "lose money on average" in texts["budget"])

        # save sets, add result, compare; saved groups carry no money fields
        await pg.evaluate("location.hash='sets'"); await pg.wait_for_timeout(150)
        await pg.click("[data-act=saveSets]"); await pg.wait_for_timeout(150)
        g = await pg.evaluate("S.groups.at(-1)")
        check("saved set group has no price/won/cost fields", set(g.keys()) == {"id", "game", "after", "sets", "created"}, list(g.keys()))
        first = g["sets"][0]
        await pg.evaluate("location.hash='compare'"); await pg.wait_for_timeout(150)
        await pg.fill("#resNums", " ".join(map(str, first)))
        await pg.click("[data-act=addResult]"); await pg.wait_for_timeout(200)
        t = await pg.evaluate("document.body.innerText")
        check("comparison shows a full match for the matching set", f"Matched {len(first)}" in t)
        check("comparison has no prize input", await pg.locator("[data-chg=won]").count() == 0)

        # spending log: self-reported, independent of number sets
        before = await pg.evaluate("monthSpend()")
        await pg.evaluate("location.hash='budget'"); await pg.wait_for_timeout(150)
        await pg.fill("#logSpent", "40"); await pg.fill("#logWon", "10")
        await pg.click("[data-act=addLog]"); await pg.wait_for_timeout(150)
        after = await pg.evaluate("[monthSpend(), yearTotals(), S.log.length]")
        check("spending log adds manual entries only", before == 0 and after[0] == 40 and after[1]["won"] == 10 and after[2] == 1, after)
        await pg.evaluate("location.hash='sets'"); await pg.wait_for_timeout(100)
        await pg.click("[data-act=saveSets]"); await pg.wait_for_timeout(100)
        check("saving sets never changes spending", await pg.evaluate("monthSpend()") == 40)

        # 5. legacy routes and migration
        for old, new in [("tickets", "sets"), ("checker", "compare"), ("syndicate", "home")]:
            await pg.evaluate(f"location.hash='{old}'"); await pg.wait_for_timeout(150)
            check(f"legacy #{old} redirects to #{new}", await pg.evaluate("location.hash") == "#" + new)
        mig = await pg.evaluate("""() => migrate({syn:{members:[1]},price:10,pauseOver:true,gen:{tickets:[[1,2,3,4,5,6]]},
            groups:[{id:'a',game:'marksix',after:'x',created:'2026-10-01',tickets:[[1,2,3,4,5,6]],price:10,won:40}]})""")
        check("migration removes syndicate/price/winnings and renames tickets",
              "syn" not in mig and "price" not in mig and "pauseOver" not in mig and mig["gen"].get("sets")
              and "tickets" not in mig["gen"] and set(mig["groups"][0].keys()) == {"id", "game", "after", "created", "sets"}
              and mig["log"] == [], mig)

        # stored data after an upgrade: removed fields must be gone from storage, not just from memory
        await pg.evaluate("""() => localStorage.setItem('drawlab2', JSON.stringify({game:'marksix',k:6,N:49,source:'sample',
            syn:{members:[{name:'A',paid:5}]},price:10,pauseOver:true,gen:{tickets:[[1,2,3,4,5,6]]},
            groups:[{id:'a',game:'marksix',after:'x',created:'2026-10-01',tickets:[[1,2,3,4,5,6]],price:10,won:40}]}))""")
        await pg.reload(); await pg.wait_for_timeout(600)
        st = await pg.evaluate("JSON.parse(localStorage.getItem('drawlab2'))")
        check("upgrade rewrites storage without syndicate/price/prize fields",
              not {"syn", "price", "pauseOver"} & set(st) and set(st["groups"][0]) == {"id", "game", "after", "created", "sets"}
              and st["groups"][0]["sets"] == [[1, 2, 3, 4, 5, 6]] and "tickets" not in st["gen"], st)

        # 3. network isolation and script errors
        check("no external network requests", not external, external[:5])
        check("no script errors", not errors, errors[:3])

        # layout: no sideways scroll or clipped figures on small and large phones
        for w in (320, 360, 412):
            lp = await b.new_page(viewport={"width": w, "height": 800})
            await lp.goto(URL); await lp.wait_for_timeout(500)
            bad = []
            for h in SCREENS:
                await lp.evaluate(f"location.hash='{h}'"); await lp.wait_for_timeout(100)
                r = await lp.evaluate("""()=>({o:document.documentElement.scrollWidth>innerWidth+1,
                  c:[...document.querySelectorAll('.tile .v')].filter(e=>e.scrollWidth>e.clientWidth+1).map(e=>e.textContent)})""")
                if r["o"] or r["c"]: bad.append((h, r))
            check(f"layout fits at {w}px wide", not bad, bad[:2])
            await lp.close()

        # 6b. real offline test: serve the app at its production path, cache it, go offline, reload
        import subprocess, tempfile, shutil, socket, time
        tmp = Path(tempfile.mkdtemp()); shutil.copytree(ROOT, tmp / "lottery-draw-lab", ignore=shutil.ignore_patterns(".git", "tests"))
        s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
        srv = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"], cwd=tmp,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(0.8)
            octx = await b.new_context(viewport={"width": 390, "height": 844})
            op = await octx.new_page()
            await op.goto(f"http://127.0.0.1:{port}/lottery-draw-lab/")
            await op.wait_for_function("navigator.serviceWorker && navigator.serviceWorker.controller !== null || (location.reload(), false)", timeout=8000)
            await op.wait_for_timeout(1500)
            await octx.set_offline(True)
            await op.reload(); await op.wait_for_timeout(800)
            await op.evaluate("location.hash='sets'"); await op.wait_for_timeout(200)
            ot = await op.evaluate("document.body.innerText")
            check("works offline after first load (service worker)", "Generate number sets" in ot, ot[:80])
            await octx.close()
        except Exception as e:
            check("works offline after first load (service worker)", False, repr(e)[:200])
        finally:
            srv.terminate(); shutil.rmtree(tmp, ignore_errors=True)
        await b.close()

    # 6. offline cache list
    sw = (ROOT / "sw.js").read_text()
    files = re.search(r"F=\[(.*?)\]", sw).group(1)
    missing = [f for f in re.findall(r"'([^']+)'", files) if f not in ("./",) and not (ROOT / f).exists()]
    check("service-worker cache files all exist", not missing, missing)
    man = json.loads((ROOT / "manifest.json").read_text())
    check("manifest start_url/scope unchanged", man["start_url"] == "/lottery-draw-lab/" and man["scope"] == "/lottery-draw-lab/")
    check("manifest icons exist", all((ROOT / i["src"]).exists() for i in man["icons"]))
    check("manifest description has no syndicate/ticket wording", not re.search(r"syndicate|ticket|prize", man["description"], re.I))

asyncio.run(main())
w = max(len(n) for n, _, _ in results)
for n, ok, d in results:
    print(("PASS " if ok else "FAIL ") + n.ljust(w) + ("" if ok else f"   -> {d}"))
fails = sum(1 for _, ok, _ in results if not ok)
print(f"\n{len(results) - fails}/{len(results)} passed")
sys.exit(1 if fails else 0)
