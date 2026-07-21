#!/usr/bin/env python3
# Two-stage quality-lock verifier for census candidates (no pre-known country), for the fabric.
# Stage A (homepage): live? + Shopify.country -> English-first?  -> dead/non-english SKIP products.json
# Stage B (products.json, only for live+English): >=10 products, >=3 priced, physical, score>=85
# Rate-bucketed + retry-safe. Emits keepers + resolved-tracking + unknown-geo bucket (recall-safe).
import sys, os, json, re, ssl, time, threading, urllib.request, urllib.error, concurrent.futures as cf
import argparse

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
WEST = {"US", "GB", "CA", "AU", "NZ", "IE"}
CUR2C = {"USD": "US", "GBP": "GB", "CAD": "CA", "AUD": "AU", "NZD": "NZ", "EUR": "IE"}
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
RATE = float(os.environ.get("RATE", "200"))     # fabric IPs aren't throttled -> effectively uncapped
WORKERS = int(os.environ.get("WORKERS", "50"))  # high concurrency; dead hosts fail fast (short timeout)

class Bucket:
    def __init__(s, r): s.r = r; s.t = r; s.last = time.time(); s.lk = threading.Lock()
    def take(s):
        while True:
            with s.lk:
                now = time.time(); s.t = min(s.r, s.t + (now - s.last) * s.r); s.last = now
                if s.t >= 1: s.t -= 1; return
            time.sleep(0.03)
B = Bucket(RATE)

def get(url, n):
    B.take()
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=8, context=CTX)
        return r.getcode(), r.read(n)
    except urllib.error.HTTPError as e:
        return (e.code, b"")
    except Exception as e:
        m = str(e).lower()
        return (429, b"") if ("timed out" in m or "timeout" in m or "reset" in m) else (0, b"")

def country_of(body):
    h = body.decode("utf-8", "ignore")
    m = re.search(r'Shopify\.country\s*=\s*"([A-Z]{2})"', h) or re.search(r'"countryCode"\s*:\s*"([A-Z]{2})"', h)
    if m: return m.group(1)
    m = re.search(r'Shopify\.currency\s*=\s*\{[^}]*"active"\s*:\s*"([A-Z]{3})"', h)
    if m: return CUR2C.get(m.group(1), "?")
    return "?"

def is_shopify(body):
    h = body[:6000].decode("utf-8", "ignore").lower()
    return "shopify" in h or "cdn.shopify" in h

def gate(body, country):
    try:
        prods = json.loads(body).get("products", [])
    except Exception:
        return None
    if len(prods) < 10: return None
    priced = phys = tv = 0
    for p in prods:
        for v in p.get("variants", []):
            tv += 1
            try:
                if float(v.get("price") or 0) > 0: priced += 1
            except (TypeError, ValueError): pass
            if v.get("requires_shipping"): phys += 1
    if priced < 3: return None
    n = len(prods); score = 40 + 25 + (20 if n >= 10 else 0) + (15 if n >= 50 else 0)
    if score < 85: return None
    phys_frac = round(phys / tv, 2) if tv else 0.0
    brand = (str(prods[0].get("vendor") or ""))[:60].replace("\t", " ").replace("\n", " ")
    return (n, score, phys_frac, brand)

def verify(d):
    # Stage A: homepage -> live + country
    code, body = get(f"https://{d}", 240000)
    if code in (429, 0): return ("rate", d, None)
    if code != 200 or not body: return ("dead", d, None)
    country = country_of(body)
    if country in WEST or country == "?":
        # Stage B: products.json (only reached by live + English/unknown)
        c2, b2 = get(f"https://{d}/products.json?limit=250", 1_500_000)
        if c2 in (429, 0): return ("rate", d, None)
        if c2 != 200: return ("dead", d, None)
        g = gate(b2, country)
        if not g: return ("notqual", d, None)
        n, score, phys, brand = g
        tag = "keeper" if country in WEST else "keeper_unknown_geo"
        return (tag, d, f"{d}\t{country}\t{n}\t{score}\t{phys}\t{brand}")
    return ("noneng", d, None)

def load_rows(a):
    if a.infile and a.shard is not None and a.of:
        lines = [l.strip() for l in open(a.infile) if l.strip()][a.shard::a.of]
    else:
        lines = [l.strip() for l in sys.stdin if l.strip()]
    return [l.split(",")[0].strip().lower() for l in lines if l]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--infile"); ap.add_argument("--shard", type=int); ap.add_argument("--of", type=int)
    ap.add_argument("--resolved-out"); a, _ = ap.parse_known_args()
    doms = load_rows(a)
    hist = {"keeper": 0, "keeper_unknown_geo": 0, "dead": 0, "noneng": 0, "notqual": 0, "rate_final": 0}
    resolved = []; t0 = time.time()
    pending = doms
    for rnd in range(2):
        retry = []
        with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
            for kind, d, line in ex.map(verify, pending):
                if kind.startswith("keeper"):
                    print(line, flush=True); hist[kind] += 1; resolved.append(d)
                elif kind == "rate":
                    retry.append(d)
                else:
                    hist[kind] += 1; resolved.append(d)
        if not retry: break
        time.sleep(65); pending = retry
    hist["rate_final"] = len(pending)
    if a.resolved_out:
        open(a.resolved_out, "w").write("\n".join(resolved) + ("\n" if resolved else ""))
    dt = time.time() - t0
    sys.stderr.write(f"[DIAG] {len(doms)} in {dt:.0f}s | {hist} | RATE={RATE}\n")

if __name__ == "__main__":
    main()
