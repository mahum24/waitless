"""Build data/providers.json from the NHS England RTT full extract.

For every provider x specialty we sum the incomplete-pathway (Part_2) week
buckets across commissioners, then compute the median and 92nd-percentile wait,
the share of patients seen within 18 weeks and the 52+ week backlog. Provider
addresses come from the NHS ODS directory and coordinates from postcodes.io.

Usage: python scripts/build_dataset.py data/raw/<extract>.csv
"""
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pandas as pd

OUT = Path(__file__).resolve().parent.parent / "data" / "providers.json"
ODS = "https://directory.spineservices.nhs.uk/ORD/2-0-0/organisations/{}"


def week_columns(columns):
    cols = []
    for c in columns:
        m = re.match(r"Gt (\d+) To (\d+) Weeks SUM 1", c)
        if m:
            cols.append((c, int(m.group(1)), int(m.group(2))))
        elif c == "Gt 104 Weeks SUM 1":
            cols.append((c, 104, 105))
    return sorted(cols, key=lambda x: x[1])


def percentile_weeks(counts, buckets, q):
    total = sum(counts)
    if total == 0:
        return None
    target, running = total * q, 0
    for n, (_, lo, hi) in zip(counts, buckets):
        if running + n >= target:
            # linear interpolation inside the bucket
            frac = (target - running) / n if n else 0
            return round(lo + frac * (hi - lo), 1)
        running += n
    return float(buckets[-1][2])


def ods_lookup(code):
    try:
        org = httpx.get(ODS.format(code), timeout=20).json()["Organisation"]
        loc = org["GeoLoc"]["Location"]
        web = next((c["value"] for c in org.get("Contacts", {}).get("Contact", []) if c["type"] == "http"), "")
        tel = next((c["value"] for c in org.get("Contacts", {}).get("Contact", []) if c["type"] == "tel"), "")
        return code, {"postcode": loc.get("PostCode", ""), "town": loc.get("Town", "").title(),
                      "website": web.lower(), "phone": tel}
    except Exception:
        return code, {}


def geocode(postcodes):
    out = {}
    pcs = sorted({p for p in postcodes if p})
    for i in range(0, len(pcs), 100):
        r = httpx.post("https://api.postcodes.io/postcodes", json={"postcodes": pcs[i:i + 100]}, timeout=30).json()
        for item in r["result"]:
            if item["result"]:
                out[item["query"]] = (item["result"]["latitude"], item["result"]["longitude"])
    return out


def main(path):
    df = pd.read_csv(path, low_memory=False)
    period = str(df["Period"].iloc[0])
    buckets = week_columns(df.columns)
    bcols = [b[0] for b in buckets]
    inc = df[df["RTT Part Type"] == "Part_2"]
    grouped = inc.groupby(["Provider Org Code", "Provider Org Name", "Treatment Function Code",
                           "Treatment Function Name"])[bcols].sum().reset_index()

    providers = {}
    for _, row in grouped.iterrows():
        counts = [int(row[c]) for c in bcols]
        total = sum(counts)
        if total < 20:  # too few patients to say anything useful
            continue
        within18 = sum(n for n, b in zip(counts, buckets) if b[2] <= 18)
        over52 = sum(n for n, b in zip(counts, buckets) if b[1] >= 52)
        p = providers.setdefault(row["Provider Org Code"], {
            "code": row["Provider Org Code"], "name": row["Provider Org Name"].title().replace("Nhs", "NHS"),
            "specialties": {}})
        p["specialties"][row["Treatment Function Code"]] = {
            "name": row["Treatment Function Name"],
            "waiting": total,
            "median_weeks": percentile_weeks(counts, buckets, 0.5),
            "p92_weeks": percentile_weeks(counts, buckets, 0.92),
            "pct_within_18": round(100 * within18 / total, 1),
            "over_52": over52,
        }

    print(f"{len(providers)} providers; looking up addresses...")
    with ThreadPoolExecutor(16) as pool:
        meta = dict(pool.map(ods_lookup, providers))
    coords = geocode(m.get("postcode") for m in meta.values())
    kept = []
    for code, p in providers.items():
        m = meta.get(code, {})
        ll = coords.get(m.get("postcode"))
        if not ll:
            continue
        p.update(m)
        p["lat"], p["lon"] = ll
        p["independent"] = not code.startswith("R")  # NHS trusts have R-codes
        kept.append(p)

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"period": period, "providers": kept}))
    print(f"wrote {len(kept)} providers for {period} -> {OUT}")


if __name__ == "__main__":
    main(sys.argv[1])
