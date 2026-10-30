"""NHS waiting-time data, postcode lookup and the deterministic ranking.

Everything numeric the user sees (waits, distances, weeks saved) comes from
here, never from a model.
"""
import json
import math
from functools import lru_cache
from pathlib import Path

import httpx

DATA = json.loads((Path(__file__).resolve().parent.parent / "data" / "providers.json").read_text())
PERIOD = DATA["period"]
PROVIDERS = DATA["providers"]
# Below this many waiting patients a provider's median is too noisy to rank on.
MIN_WAITING = 100

SPECIALTIES = {}
for p in PROVIDERS:
    for code, s in p["specialties"].items():
        if code.startswith("C_") and code != "C_999":
            SPECIALTIES[code] = s["name"]


@lru_cache(maxsize=512)
def lookup_postcode(postcode: str) -> dict | None:
    r = httpx.get(f"https://api.postcodes.io/postcodes/{postcode.strip()}", timeout=10)
    if r.status_code != 200:
        return None
    res = r.json()["result"]
    return {"postcode": res["postcode"], "lat": res["latitude"], "lon": res["longitude"],
            "country": res["country"], "district": res.get("admin_district") or ""}


def miles(lat1, lon1, lat2, lon2) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def candidates(specialty: str, lat: float, lon: float, max_miles: float) -> dict:
    """Providers offering `specialty` within `max_miles`, plus the nearest NHS trust as the baseline."""
    rows = []
    for p in PROVIDERS:
        s = p["specialties"].get(specialty)
        if not s or s["median_weeks"] is None or s["waiting"] < MIN_WAITING:
            continue
        d = miles(lat, lon, p["lat"], p["lon"])
        rows.append({
            "code": p["code"], "name": p["name"], "town": p["town"], "postcode": p["postcode"],
            "website": p.get("website", ""), "phone": p.get("phone", ""),
            "lat": p["lat"], "lon": p["lon"], "independent": p["independent"],
            "miles": round(d, 1), **{k: s[k] for k in ("waiting", "median_weeks", "p92_weeks",
                                                         "pct_within_18", "over_52")},
        })
    nhs = sorted((r for r in rows if not r["independent"]), key=lambda r: r["miles"])
    baseline = nhs[0] if nhs else None
    nearby = [r for r in rows if r["miles"] <= max_miles]
    # Rank on wait first; distance breaks near-ties (1 week ~ 15 miles).
    nearby.sort(key=lambda r: r["median_weeks"] + r["miles"] / 15)
    for r in nearby:
        r["weeks_saved"] = round(baseline["median_weeks"] - r["median_weeks"], 1) if baseline else None
    return {"baseline": baseline, "options": nearby}


# Situations where the legal right to choose a first outpatient provider does
# not apply (NHS Choice Framework).
EXCLUSIONS = {
    "urgent_cancer": "Urgent suspected-cancer referrals must be seen as fast as possible, so the usual choice of provider does not apply. Speak to your GP about the quickest option.",
    "emergency": "Emergency care is not covered by the right to choose. Use 111 or 999.",
    "maternity": "Maternity services have their own separate choices, so this tool doesn't cover them.",
    "already_treated": "The right to choose applies when you are first referred. If treatment has already started, ask your hospital or GP about moving instead.",
    "prison_or_forces": "People in prison, detained under the Mental Health Act, or serving in the armed forces have different rules.",
}


def eligibility(flags: dict, country: str) -> dict:
    reasons = []
    if country != "England":
        reasons.append(f"The legal right to choose your hospital applies in England; your postcode is in {country}, which has different rules.")
    for key, text in EXCLUSIONS.items():
        if flags.get(key):
            reasons.append(text)
    return {"eligible": not reasons, "reasons": reasons}
