"""Stored-data resilience tests for Lottery Draw Lab v1.

Run:  python3 tests/storage_tests.py      (needs Python Playwright with Chromium)

Covers: idempotent loading (valid data is never rewritten), backups before any repair, backup
preservation and limits, recovery of valid parts from partially corrupt data, the three confirmed
failure categories, and randomised corruption of every stored field.
"""
import asyncio, copy, json, random, sys
from pathlib import Path
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
URL = (ROOT / "index.html").as_uri()
SCREENS = ["home", "insights", "test", "sets", "score", "compare", "outcomes", "budget", "data"]
B = "drawlab2.backup"
results = []
def check(name, ok, detail=""): results.append((name, bool(ok), detail)); return ok

def draws(n=30):
    r = random.Random(7); out = []
    for i in range(n): out.append({"date": f"2026-0{1 + i // 28}-{1 + i % 28:02d}", "nums": sorted(r.sample(range(1, 50), 6))})
    return out
VALID = {"game": "marksix", "k": 6, "N": 49, "draws": draws(), "source": "import", "win": 100, "warm": 50, "period": 100,
         "groups": [{"id": "g1", "game": "marksix", "after": "2026-01-05|" + "-".join(map(str, draws()[4]["nums"])),
                     "created": "2026-01-05T10:00:00.000Z", "sets": [[1, 2, 3, 4, 5, 6], [7, 14, 21, 28, 35, 42]]}],
         "cur": "HK$", "limit": 200, "remind": True,
         "log": [{"id": "l1", "date": "2026-10-01", "spent": 40, "won": 10}, {"id": "l2", "date": "2026-10-08", "spent": 20, "won": 0}],
         "gen": {"strategy": "hot", "count": 3, "avoid": False, "sets": [[3, 9, 17, 22, 31, 44], [2, 8, 15, 27, 33, 41], [5, 11, 19, 26, 38, 47]]}}
V09 = {"game": "marksix", "k": 6, "N": 49, "draws": [], "source": "sample", "win": 100, "warm": 50, "period": 100,
       "groups": [{"id": "a", "game": "marksix", "after": "", "tickets": [[1, 2, 3, 4, 5, 6]], "price": 10, "created": "2026-10-01T00:00:00Z", "won": 40}],
       "price": 10, "cur": "HK$", "limit": 200, "remind": True, "pauseOver": True,
       "syn": {"name": "Office", "members": [{"name": "A", "paid": 5}], "prize": 0},
       "gen": {"strategy": "random", "count": 5, "avoid": True, "tickets": [[1, 2, 3, 4, 5, 6]]}}

SPY = """(() => { window.__writes = []; const set = Storage.prototype.setItem;
  Storage.prototype.setItem = function (k, v) { window.__writes.push(k); return set.call(this, k, v) } })()"""

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        async def phone(stored=None, extra=None):
            ctx = await b.new_context(viewport={"width": 390, "height": 844}); pg = await ctx.new_page()
            errs = []; pg.on("pageerror", lambda e: errs.append(str(e)[:150]))
            await pg.goto(URL); await pg.wait_for_timeout(300)
            await pg.evaluate("([s,x]) => { localStorage.clear(); if (s !== null) localStorage.setItem('drawlab2', s);"
                              " for (const [k,v] of Object.entries(x||{})) localStorage.setItem(k, v) }", [stored, extra])
            await pg.add_init_script(SPY); await pg.reload(); await pg.wait_for_timeout(500)
            return ctx, pg, errs
        async def state(pg):
            return await pg.evaluate("""() => { const o = {}; for (const k of Object.keys(localStorage)) o[k] = localStorage.getItem(k);
                let main = null; try { main = JSON.parse(localStorage.getItem('drawlab2') || 'null') } catch (e) {}
                return {main, keys: Object.keys(localStorage).sort(), raw: o,
                        writes: window.__writes} }""")
        async def renders(pg, errs, fast=False):
            errs.clear(); bad = []
            for h in SCREENS:
                await pg.evaluate(f"location.hash='{h}'"); await pg.wait_for_timeout(60 if fast else 150)
                t = await pg.evaluate("document.body.innerText")
                if len(t) < 200 or "undefined" in t or "NaN" in t: bad.append(h)
            return bad, list(errs)

        # 1. Idempotence: valid v1 data is never rewritten and never backed up
        raw = json.dumps(VALID)
        ctx, pg, errs = await phone(raw)
        st = await state(pg)
        check("valid v1 data: not rewritten on open", "drawlab2" not in st["writes"], st["writes"])
        check("valid v1 data: no backup made", not [k for k in st["keys"] if k.startswith(B)], st["keys"])
        check("valid v1 data: everything kept exactly", st["main"] == VALID, "")
        for i in range(3): await pg.reload(); await pg.wait_for_timeout(300)
        st = await state(pg)
        check("valid v1 data: still not rewritten after 3 more opens", "drawlab2" not in st["writes"] and st["raw"]["drawlab2"] == raw, st["writes"])
        bad, e = await renders(pg, errs); check("valid v1 data: all screens render, no errors", not bad and not e, (bad, e))
        await ctx.close()

        # 2. v0.9 data: migrated once, then idempotent
        ctx, pg, errs = await phone(json.dumps(V09))
        st = await state(pg)
        check("v0.9 data: migrated and written once", st["writes"].count("drawlab2") == 1 and "syn" not in st["main"]
              and st["main"]["groups"][0]["sets"] == [[1, 2, 3, 4, 5, 6]] and st["main"]["gen"]["sets"] == [[1, 2, 3, 4, 5, 6]], st["writes"])
        check("v0.9 data: not treated as corrupt (no backup)", not [k for k in st["keys"] if k.startswith(B)], st["keys"])
        first = st["raw"]["drawlab2"]; await pg.reload(); await pg.wait_for_timeout(400); st = await state(pg)
        check("v0.9 data: second open writes nothing", "drawlab2" not in st["writes"] and st["raw"]["drawlab2"] == first, st["writes"])
        await ctx.close()

        # 3. The three confirmed categories: repaired, everything else kept, original backed up
        cats = {
            "groups is not an array": ({**VALID, "groups": "x"}, lambda m: m["groups"] == [] and m["log"] == VALID["log"] and m["gen"] == VALID["gen"] and m["draws"] == VALID["draws"]),
            "gen is not an object": ({**VALID, "gen": 5}, lambda m: m["groups"] == VALID["groups"] and m["log"] == VALID["log"] and m["gen"]["sets"] is not None),
            "unknown game identifier": ({**VALID, "game": "zzz", "k": "six"}, lambda m: m["game"] == "marksix" and m["k"] == 6 and m["groups"] == VALID["groups"] and m["log"] == VALID["log"]),
        }
        for name, (data, kept) in cats.items():
            raw = json.dumps(data); ctx, pg, errs = await phone(raw)
            st = await state(pg); bad, e = await renders(pg, errs)
            check(f"{name}: all screens render, no errors", not bad and not e, (bad, e))
            check(f"{name}: original copied to backup before repair", st["raw"].get(B) == raw, st["keys"])
            check(f"{name}: valid number sets, spending log and settings kept", kept(st["main"]), st["main"] and {k: st["main"].get(k) for k in ("game", "groups", "log")})
            await pg.reload(); await pg.wait_for_timeout(300); st2 = await state(pg)
            check(f"{name}: repaired once, not rewritten on next open", "drawlab2" not in st2["writes"], st2["writes"])
            await ctx.close()

        # 4. Partially corrupt data: valid parts survive, invalid parts dropped, backup kept
        part = copy.deepcopy(VALID)
        part["groups"] += [None, 7, {"id": "g2", "game": "marksix", "after": "", "created": "x", "sets": [[1, 2, 3, 4, 5, 6], ["x"], [1, 1, 2, 3, 4, 5], [1, 2, 3, 4, 5, 99]]},
                           {"game": "marksix", "sets": "nope"}]
        part["log"] += [None, {"id": "l3", "date": "yesterday", "spent": 5}, {"id": "l4", "date": "2026-10-09", "spent": -3}, {"date": "2026-10-10", "spent": "7", "won": ""}]
        part["draws"] = part["draws"] + [{"date": "2026-05-01", "nums": [1, 2, 3]}, "junk"]
        part["gen"]["sets"].append([0, 1, 2, 3, 4, 5]); part["limit"] = "lots"; part["remind"] = "yes"; part["cur"] = 5
        raw = json.dumps(part); ctx, pg, errs = await phone(raw)
        st = await state(pg); m = st["main"]; bad, e = await renders(pg, errs)
        check("partial corruption: all screens render, no errors", not bad and not e, (bad, e))
        check("partial corruption: original copied to backup", st["raw"].get(B) == raw)
        check("partial corruption: valid groups and only their valid sets kept",
              [g["id"] for g in m["groups"]] == ["g1", "g2", "r4"] and m["groups"][0]["sets"] == VALID["groups"][0]["sets"]
              and m["groups"][1]["sets"] == [[1, 2, 3, 4, 5, 6]] and m["groups"][2]["sets"] == [], [(g["id"], g["sets"]) for g in m["groups"]])
        check("partial corruption: valid spending entries kept, invalid dropped",
              [x.get("id") for x in m["log"]] == ["l1", "l2", "r5"] and m["log"][2]["spent"] == "7", m["log"])
        check("partial corruption: valid draws kept", m["draws"] == VALID["draws"], len(m["draws"]))
        check("partial corruption: valid generated sets kept", m["gen"]["sets"] == VALID["gen"]["sets"], m["gen"]["sets"])
        check("partial corruption: bad settings fall back to defaults", m["limit"] == 200 and m["remind"] is True and m["cur"] == "HK$", (m["limit"], m["remind"], m["cur"]))
        await ctx.close()

        # 5. Backup preservation and limits
        ctx, pg, errs = await phone("{bad1", {B: "EXISTING"})
        st = await state(pg)
        check("existing backup is never overwritten", st["raw"][B] == "EXISTING", st["raw"].get(B))
        extra = [k for k in st["keys"] if k.startswith(B + ".")]
        check("a different original goes to a dated extra backup", len(extra) == 1 and st["raw"][extra[0]] == "{bad1", extra)
        await pg.reload(); await pg.wait_for_timeout(300); st = await state(pg)
        check("the same original is not backed up twice", len([k for k in st["keys"] if k.startswith(B)]) == 2, st["keys"])
        await ctx.close()
        full = {B: "first", **{f"{B}.2026-01-0{i}T00:00:00.000Z": f"old{i}" for i in range(1, 6)}}
        ctx, pg, errs = await phone("{bad2", full)
        st = await state(pg)
        check("at most 6 backups kept; existing ones untouched when full",
              all(st["raw"].get(k) == v for k, v in full.items()) and len([k for k in st["keys"] if k.startswith(B)]) == 6, st["keys"])
        bad, e = await renders(pg, errs); check("app still opens when backups are full", not bad and not e, (bad, e))
        await ctx.close()
        ctx, pg, errs = await phone("{bad3")
        st = await state(pg)
        check("unreadable data is kept in storage until the user saves", st["raw"]["drawlab2"] == "{bad3" and st["raw"][B] == "{bad3", st["keys"])
        await ctx.close()

        # 6. Imported draws that are all invalid: falls back to sample data, original backed up
        raw = json.dumps({**VALID, "draws": [{"date": "x", "nums": "y"}]})
        ctx, pg, errs = await phone(raw); st = await state(pg); bad, e = await renders(pg, errs)
        check("all imported draws invalid: sample data used, no errors", st["main"]["source"] == "sample" and not bad and not e, (bad, e))
        check("all imported draws invalid: original backed up", st["raw"].get(B) == raw)
        await ctx.close()

        # 7. Randomised corruption of every stored field
        rnd = random.Random(2026); junk = [None, 0, -1, 1e9, "", "x", True, [], [None], {}, {"a": 1}, [[1, 2]], "2026-13-45"]
        def paths(o, pre=()):
            yield pre
            if isinstance(o, dict):
                for k, v in o.items(): yield from paths(v, pre + (k,))
            elif isinstance(o, list):
                for i, v in enumerate(o[:3]): yield from paths(v, pre + (i,))
        allp = [q for q in paths(VALID) if q]
        failures = []
        ctx = await b.new_context(viewport={"width": 390, "height": 844}); pg = await ctx.new_page()
        errs = []; pg.on("pageerror", lambda e: errs.append(str(e)[:120]))
        await pg.goto(URL); await pg.wait_for_timeout(300)
        for it in range(80):
            d = copy.deepcopy(VALID)
            for _ in range(rnd.randint(1, 4)):
                q = rnd.choice(allp); tgt = d
                try:
                    for key in q[:-1]: tgt = tgt[key]
                    if rnd.random() < 0.15: del tgt[q[-1]]
                    else: tgt[q[-1]] = rnd.choice(junk)
                except (KeyError, IndexError, TypeError): pass
            await pg.evaluate("s => localStorage.setItem('drawlab2', s)", json.dumps(d))
            await pg.reload(); await pg.wait_for_timeout(250)
            bad, e = await renders(pg, errs, fast=True)
            if bad or e: failures.append({"bad": bad, "err": e[:1], "data": {k: d.get(k) for k in d if d.get(k) != VALID.get(k)}})
        check("80 random corruptions of stored fields: every screen renders, no errors", not failures, failures[:2])
        await ctx.close(); await b.close()

asyncio.run(main())
w = max(len(n) for n, _, _ in results)
for n, ok, d in results:
    print(("PASS " if ok else "FAIL ") + n.ljust(w) + ("" if ok else f"   -> {str(d)[:400]}"))
fails = sum(1 for _, ok, _ in results if not ok)
print(f"\n{len(results) - fails}/{len(results)} passed")
sys.exit(1 if fails else 0)
