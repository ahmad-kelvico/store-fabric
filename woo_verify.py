#!/usr/bin/env python3
# WooCommerce verifier — Store API first, evidence-with-receipts, zero fabrication.
# Every keeper's fields are READ from the live /wp-json/wc/store/v1/products response. No inference.
# Output TSV: domain \t country \t products \t priced \t score \t phys_frac \t currency \t sample_price \t brand
# curl-based (urllib gets bot-tarpitted). 418/HTML/empty -> NOT a keeper (unverifiable), never guessed.
import sys, os, json, subprocess, concurrent.futures as cf
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
WEST = {"USD": "US", "GBP": "GB", "CAD": "CA", "AUD": "AU", "NZD": "NZ"}   # EUR handled separately (IE only)
WORKERS = int(os.environ.get("WORKERS", "8"))

def curl(url):
    try:
        r = subprocess.run(["curl", "-sL", "--max-time", "10", "--compressed", "-A", UA, url],
                           capture_output=True, timeout=13)
        return r.stdout
    except Exception:
        return b""

def nonzero(s):
    return s not in (None, "", "0") and str(s).strip("0.") != ""

def phys_of(p):
    w = p.get("weight")
    dim = p.get("dimensions") or {}
    has_dim = any(str(dim.get(k) or "").strip() not in ("", "0") for k in ("length", "width", "height"))
    return 1 if (str(w or "").strip() not in ("", "0") or has_dim) else 0

# strong service/digital signals in REAL product categories (used to EXCLUDE non-ICP, recall-first)
SERVICE_KW = ("training", "course", "class", "lesson", "workshop", "seminar", "tuition", "coaching",
              "consult", "appointment", "booking", "reservation", "session", "membership", "subscription",
              "ticket", "admission", "webinar", "masterclass", "e-book", "ebook", "download", "printable",
              "digital", "template", "software", "license", "plugin", "preset", "online course", "gift card")
def service_of(p):
    cats = " ".join((c.get("name", "") + " " + c.get("slug", "")) for c in (p.get("categories") or [])).lower()
    return 1 if any(k in cats for k in SERVICE_KW) else 0

def verify(d):
    body = curl(f"https://{d}/wp-json/wc/store/v1/products?per_page=100")
    if not body:
        return ("dead", d, None)
    try:
        prods = json.loads(body)
    except Exception:
        return ("blocked", d, None)             # 418 / HTML challenge / not JSON -> unverifiable, NOT a keeper
    if not isinstance(prods, list):
        return ("blocked", d, None)
    if len(prods) < 10:
        return ("thin", d, None)                # fewer than 10 real products
    priced = phys = svc = 0; sample = ""
    cur = (prods[0].get("prices") or {}).get("currency_code")
    for p in prods:
        pr = p.get("prices") or {}
        if nonzero(pr.get("price")):
            priced += 1
            if not sample:
                try:
                    mu = int(pr.get("currency_minor_unit") or 2)
                    sample = f'{pr.get("currency_symbol","")}{int(pr["price"])/(10**mu):.2f}'
                except Exception: pass
        phys += phys_of(p)
        svc += service_of(p)
    if priced < 3:
        return ("notqual", d, None)             # no real priced products
    country = WEST.get(cur) or ("IE" if cur == "EUR" and d.endswith(".ie") else None)
    if not country:
        return ("noneng", d, None)              # currency not English-market (strict; ambiguous EUR dropped unless .ie)
    n = len(prods); phys_frac = round(phys / n, 2); svc_frac = round(svc / n, 2)
    if svc_frac > 0.5:                           # store dominated by service/digital categories -> not ICP
        return ("service", d, None)
    # evidence tier for physical (never claimed without proof)
    status = "confirmed-physical" if phys_frac >= 0.3 else ("check-service" if svc_frac > 0 else "physical-unverified")
    score = 40 + 25 + 20 + (15 if n >= 50 else 0)
    b = prods[0].get("brands")
    br = (str(b[0].get("name", ""))[:50] if isinstance(b, list) and b else d).replace("\t", " ").replace("\n", " ")
    line = f"{d}\t{country}\t{n}\t{priced}\t{score}\t{phys_frac}\t{cur}\t{sample}\t{status}\t{br}"
    return ("keeper", d, line)

def main():
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument("--resolved-out"); a, _ = ap.parse_known_args()
    doms = [l.strip().split(",")[0].lower() for l in sys.stdin if l.strip()]
    from collections import Counter
    hist = Counter(); resolved = []
    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for kind, d, line in ex.map(verify, doms):
            hist[kind] += 1
            if kind == "keeper":
                print(line, flush=True); resolved.append(d)
            elif kind in ("thin", "noneng", "notqual", "service"):
                resolved.append(d)                 # definitive non-keeper; blocked/dead stay retryable
    if a.resolved_out:
        open(a.resolved_out, "w").write("\n".join(resolved) + ("\n" if resolved else ""))
    sys.stderr.write(f"[WOO-DIAG] {len(doms)} | {dict(hist)}\n")

if __name__ == "__main__":
    main()
