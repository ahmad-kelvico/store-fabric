#!/usr/bin/env python3
# Two-stage quality-lock verifier (curl-based — urllib gets bot-tarpitted by Shopify/Cloudflare).
# Stage A (homepage): live? + Shopify.country -> English-first?  Stage B (products.json): >=10 priced physical.
# curl fetches (browser-like, reliable). curl-fail => DEAD (not throttle). Only HTTP 429 => retry.
import sys, os, json, re, subprocess, concurrent.futures as cf
import argparse

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
WEST = {"US", "GB", "CA", "AU", "NZ", "IE"}
CUR2C = {"USD": "US", "GBP": "GB", "CAD": "CA", "AUD": "AU", "NZD": "NZ", "EUR": "IE"}
WORKERS = int(os.environ.get("WORKERS", "16"))

def curl_get(url, maxbytes):
    """returns (http_code:int, body:bytes). code 0 = curl failed (dead)."""
    try:
        r = subprocess.run(
            ["curl", "-sL", "--max-time", "9", "--compressed", "-A", UA, "-w", "\n%{http_code}", url],
            capture_output=True, timeout=13)
        out = r.stdout
        nl = out.rfind(b"\n")
        if nl < 0:
            return (0, b"")
        code = out[nl + 1:].decode("ascii", "ignore").strip()
        return (int(code) if code.isdigit() else 0, out[:nl][:maxbytes])
    except Exception:
        return (0, b"")

def country_of(body):
    h = body.decode("utf-8", "ignore")
    m = re.search(r'Shopify\.country\s*=\s*"([A-Z]{2})"', h) or re.search(r'"countryCode"\s*:\s*"([A-Z]{2})"', h)
    if m: return m.group(1)
    m = re.search(r'Shopify\.currency\s*=\s*\{[^}]*"active"\s*:\s*"([A-Z]{3})"', h)
    if m: return CUR2C.get(m.group(1), "?")
    return "?"

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
    code, body = curl_get(f"https://{d}", 240000)          # Stage A: homepage
    if code == 429: return ("rate", d, None)
    if code != 200 or not body: return ("dead", d, None)
    country = country_of(body)
    if not (country in WEST or country == "?"):
        return ("noneng", d, None)
    c2, b2 = curl_get(f"https://{d}/products.json?limit=250", 1_500_000)   # Stage B: products.json
    if c2 == 429: return ("rate", d, None)
    if c2 != 200: return ("dead", d, None)
    g = gate(b2, country)
    if not g: return ("notqual", d, None)
    n, score, phys, brand = g
    tag = "keeper" if country in WEST else "keeper_unknown_geo"
    return (tag, d, f"{d}\t{country}\t{n}\t{score}\t{phys}\t{brand}")

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
    resolved = []
    retry = []
    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:   # single pass; retry-WAVES (fresh IPs) recover the throttled
        for kind, d, line in ex.map(verify, doms):
            if kind.startswith("keeper"):
                print(line, flush=True); hist[kind] += 1; resolved.append(d)
            elif kind == "rate":
                retry.append(d)
            else:
                hist[kind] += 1; resolved.append(d)
    hist["rate_final"] = len(retry)
    if a.resolved_out:
        open(a.resolved_out, "w").write("\n".join(resolved) + ("\n" if resolved else ""))
    sys.stderr.write(f"[DIAG] {len(doms)} | {hist}\n")

if __name__ == "__main__":
    main()
