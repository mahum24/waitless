"""The WaitLess agent: read referral -> rank providers -> research -> verify -> decide -> draft letter.

Each step yields an event so the UI can show the agent working live. Models
are routed by difficulty: Nano reads, Super verifies and writes, Ultra makes
the final trade-off decision.
"""
import json

from . import data, llm, research

SHORTLIST = 4


def event(step: str, status: str, **payload) -> dict:
    return {"step": step, "status": status, **payload}


GENERIC = {"nhs", "trust", "foundation", "hospital", "hospitals", "university", "health", "healthcare",
           "the", "and", "of", "group", "clinic", "services", "limited", "ltd", "care", "spire", "nuffield",
           "ramsay", "circle", "community", "teaching", "general", "royal"}


def quote_checks_out(quote, url, results, provider) -> bool:
    """A claim counts only if its cited result exists, is about this provider and contains the quote."""
    src = next((r for r in results if r["url"] == url), None)
    if not src:
        return False
    text = f"{src['title']} {src['content']}".lower()
    words = [w for w in provider["name"].lower().replace(",", " ").split() if w not in GENERIC and len(w) > 2]
    about_it = provider["town"].lower() in text or any(w in text for w in words)
    if quote is None:
        return about_it
    return about_it and bool(quote.strip()) and quote.strip().lower() in text


def run(referral: str, postcode: str, max_miles: float, context: str):
    llm.USAGE.clear()

    # 1. Read the referral (Nano: fast structured extraction)
    yield event("read", "running", tier="nano")
    specs = "\n".join(f"{c}: {n}" for c, n in sorted(data.SPECIALTIES.items(), key=lambda x: x[1]))
    parsed = llm.chat_json("nano", [
        {"role": "system", "content": "You read NHS GP referral letters and patient descriptions. Reply with JSON only."},
        {"role": "user", "content": f"""Text from the patient:
\"\"\"{referral}\"\"\"

Extra context from the patient: \"\"\"{context}\"\"\"

Pick the single best matching specialty code from this list:
{specs}

Return JSON:
{{"specialty_code": "C_xxx",
  "condition": "short plain-English description of the problem",
  "flags": {{"urgent_cancer": bool, "emergency": bool, "maternity": bool, "already_treated": bool, "prison_or_forces": bool}},
  "needs": ["practical constraints mentioned, e.g. does not drive, carer, wheelchair user"]}}"""},
    ], step="Read referral", max_tokens=800)
    code = parsed.get("specialty_code")
    if code not in data.SPECIALTIES:
        code = next((c for c, n in data.SPECIALTIES.items() if n.lower().startswith(str(code).lower())), "C_999")
    parsed["specialty_code"] = code
    parsed["specialty"] = data.SPECIALTIES.get(code, "All specialties")
    yield event("read", "done", tier="nano", result=parsed)

    # 2. Locate + eligibility (deterministic)
    yield event("locate", "running")
    loc = data.lookup_postcode(postcode)
    if not loc:
        yield event("locate", "error", message=f"Couldn't find the postcode '{postcode}'.")
        return
    elig = data.eligibility(parsed.get("flags", {}), loc["country"])
    yield event("locate", "done", result={"location": loc, "eligibility": elig})

    # 3. Rank providers from NHS England data (deterministic)
    yield event("rank", "running")
    ranked = data.candidates(code, loc["lat"], loc["lon"], max_miles)
    base, options = ranked["baseline"], ranked["options"]
    if not options:
        yield event("rank", "error", message="No providers with published waiting times for this specialty in range. Try a larger distance.")
        return
    shortlist = options[:SHORTLIST]
    if base and base["code"] not in {s["code"] for s in shortlist}:
        base_row = next((o for o in options if o["code"] == base["code"]), dict(base, weeks_saved=0.0))
        shortlist.append(base_row)
    yield event("rank", "done", result={"baseline": base, "options": options[:25], "period": data.PERIOD})

    # 4. Live research (Tavily)
    yield event("research", "running", tier="tavily")
    evidence = research.gather(shortlist)
    yield event("research", "done", tier="tavily",
                result={c: {"sources": len(e["cqc"]) + len(e["news"])} for c, e in evidence.items()})

    # 5. Verify evidence (Super), then check every quote really is in the source
    yield event("verify", "running", tier="super")
    packed = {s["code"]: {"name": s["name"], **evidence.get(s["code"], {})} for s in shortlist}
    verified = llm.chat_json("super", [
        {"role": "system", "content": "You check evidence about NHS and independent hospitals. Only use the supplied search results. Reply with JSON only."},
        {"role": "user", "content": f"""Search results per provider:
{json.dumps(packed)[:24000]}

Only use a result if it is clearly about that exact provider and location (not another hospital in the same chain).
For each provider code return:
{{"<code>": {{"cqc_rating": "the OVERALL rating: Outstanding|Good|Requires improvement|Inadequate|Unknown",
             "cqc_quote": "exact short phrase copied verbatim from a result that states the overall rating, or empty",
             "cqc_url": "url of that result or empty",
             "news": "one sentence on anything recent and relevant (strikes, closures, extra capacity, investigations) or empty",
             "news_url": "url or empty"}}}}"""},
    ], step="Verify evidence", max_tokens=2500)
    for s in shortlist:
        v = verified.get(s["code"]) or {}
        ev = evidence.get(s["code"], {})
        v["verified"] = quote_checks_out(v.get("cqc_quote"), v.get("cqc_url"), ev.get("cqc", []), s)
        if not v["verified"]:
            v["cqc_rating"] = "Unverified"
        if not quote_checks_out(None, v.get("news_url"), ev.get("news", []), s):
            v["news"], v["news_url"] = "", ""
        s["evidence"] = v
    yield event("verify", "done", tier="super", result={s["code"]: s["evidence"] for s in shortlist})

    # 6. Decide (Ultra: weigh wait vs travel vs patient needs)
    yield event("decide", "running", tier="ultra")
    table = [{k: s.get(k) for k in ("code", "name", "town", "miles", "median_weeks", "p92_weeks",
                                    "pct_within_18", "weeks_saved", "independent", "evidence")} for s in shortlist]
    decision = llm.chat_json("ultra", [
        {"role": "system", "content": "You help NHS patients in England use their legal right to choose where they are referred. "
                                      "You are careful, honest about uncertainty and never invent numbers. Reply with JSON only."},
        {"role": "user", "content": f"""Patient condition: {parsed.get('condition')}
Patient needs: {parsed.get('needs')} / {context}
Nearest NHS trust (baseline): {base['name'] if base else 'unknown'}
Options (waits are from NHS England RTT data {data.PERIOD}; median_weeks = median wait of patients currently waiting; p92_weeks = 92nd percentile):
{json.dumps(table)}

Choose the best option for THIS patient, weighing wait time, travel and their needs, and inspection quality where verified.
Only state facts that appear above. Do not claim anything about transport links, parking, facilities or services that isn't in the data;
if the patient's needs depend on something unknown, put it in ask_gp or tradeoffs as something to check.
Return JSON:
{{"recommended_code": "...", "backup_code": "...",
  "headline": "one sentence a patient understands",
  "reasons": ["3-4 short reasons citing the figures above"],
  "tradeoffs": ["1-2 honest downsides"],
  "ask_gp": ["2-3 questions to ask the GP"]}}"""},
    ], step="Weigh trade-offs", max_tokens=8000, temperature=0.3, think=True)
    codes = {s["code"] for s in shortlist}
    if decision.get("recommended_code") not in codes:
        decision["recommended_code"] = shortlist[0]["code"]
    if decision.get("backup_code") not in codes:
        decision["backup_code"] = next(c["code"] for c in shortlist if c["code"] != decision["recommended_code"])
    yield event("decide", "done", tier="ultra", result=decision)

    # 7. Draft the letter (Super)
    yield event("letter", "running", tier="super")
    rec = next(s for s in shortlist if s["code"] == decision["recommended_code"])
    letter = llm.chat("super", [
        {"role": "system", "content": "You write short, polite, firm letters from NHS patients in England to their GP practice. Plain text only, no markdown."},
        {"role": "user", "content": f"""Write a letter from the patient to their GP practice asking to be referred for {parsed.get('specialty')} ({parsed.get('condition')}) to:
{rec['name']}, {rec['town']} {rec['postcode']} (NHS provider code {rec['code']}).

Mention that under the NHS Choice Framework patients in England have a legal right to choose which provider they are referred to for a first outpatient appointment, and that NHS England data for {data.PERIOD} shows a median wait of {rec['median_weeks']} weeks there{f" compared with {base['median_weeks']} weeks at {base['name']}" if base else ""}.
Ask them to use the e-Referral Service to book it, and to say if there is a clinical reason this is not suitable. Leave [Your name], [Date of birth], [NHS number], [Address] placeholders. Under 220 words."""},
    ], step="Draft GP letter", max_tokens=1200, temperature=0.4)
    yield event("letter", "done", tier="super", result={"text": letter})

    yield event("complete", "done", result={"usage": llm.USAGE, "models": llm.MODELS,
                                            "recommended": rec, "shortlist": shortlist})
