# WaitLess

**Same treatment. Months sooner.**

NHS England's August 2026 figures show 7.2 million treatment pathways waiting in England. Most of them have a legal right to choose which provider they're referred to, and waits for the same specialty can differ by months between hospitals a short drive apart. Almost nobody uses that right, because comparing hospitals means digging through NHS statistics spreadsheets and then knowing what to say to your GP.

WaitLess does it in about 15 seconds. Upload a photo of your referral letter (or describe it), enter your postcode, and it:

1. **Reads and redacts the letter.** Nemotron 3.5 Super VL transcribes the photo and blanks out your name, date of birth and NHS number before anything else sees it.
2. **Works out the specialty and checks you're covered.** Nemotron 3 Nano maps the referral to an NHS treatment function and flags exclusions (urgent suspected cancer, maternity, outside England and so on). The eligibility rules are code, not model output.
3. **Ranks every provider near you** from the latest **NHS England Referral-to-Treatment statistics**: median wait, the wait 9 in 10 patients are seen within, the share seen within 18 weeks, and the distance from your postcode. Every number on screen comes straight from the official data.
4. **Researches the shortlist live with Tavily**: the CQC inspection rating and recent news for each provider.
5. **Verifies the evidence.** Nemotron 3 Super extracts each rating with a verbatim quote and a source URL; code then checks that the cited page exists, is about that exact hospital, and really contains the quote. Anything that fails is shown as *unverified* instead.
6. **Weighs the trade-offs for you.** Nemotron 3 Ultra (reasoning on) balances wait, travel and what you told it ("I don't drive", "I care for my mum") and explains its choice, including the downsides.
7. **Writes your letter** to your GP practice asking for the referral through the NHS e-Referral Service.

## How it uses NVIDIA Nemotron on Nebius Token Factory

Each step goes to the smallest model that can do it well, all served through the Token Factory OpenAI-compatible API:

| Step | Model | Reasoning | Why |
|---|---|---|---|
| Read letter photo | `nvidia/Nemotron-3.5-Super-VL` | off | Vision transcription plus redaction |
| Read referral | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | off | Fast structured extraction (~1 s) |
| Verify evidence | `nvidia/nemotron-3-super-120b-a12b` | off | Long-context reading of search results |
| Weigh trade-offs | `nvidia/Nemotron-3-Ultra-550b-a55b` | **on** | The one judgement call that deserves deep reasoning |
| Draft GP letter | `nvidia/nemotron-3-super-120b-a12b` | off | Fluent, constrained writing |

Nemotron 3 reasons by default; WaitLess switches reasoning off (`chat_template_kwargs.enable_thinking=false`) everywhere except the Ultra decision, which keeps the whole analysis around 15 seconds and the token bill small. The app shows a live routing panel with the model, latency, tokens and reasoning tokens for every call.

## Design principle: models propose, data and code decide

- Waits, distances and "weeks sooner" come from NHS England data, never from a model.
- Model-extracted claims (CQC ratings, news) must quote a source that code can check; otherwise they're marked unverified.
- The recommended provider must be one of the ranked options; if the model returns anything else, the top-ranked option is used.
- Eligibility exclusions are a fixed rule set, so the model can't talk anyone into or out of their rights.

## Architecture

```
browser ── POST /api/read-letter ──► Nemotron VL (Token Factory)
        ── POST /api/analyze (server-sent events, one per step)
              ├─ Nemotron Nano: specialty + exclusion flags
              ├─ postcodes.io + rules: location, eligibility
              ├─ NHS RTT dataset: rank providers by wait & distance
              ├─ Tavily: CQC + news per shortlisted provider (parallel)
              ├─ Nemotron Super: extract evidence → code verifies quotes
              ├─ Nemotron Ultra (reasoning): personalised decision
              └─ Nemotron Super: GP letter
```

- `app/llm.py`: Token Factory client, model discovery and routing, usage logging
- `app/data.py`: NHS dataset, postcode lookup, ranking, eligibility rules
- `app/research.py`: Tavily search
- `app/pipeline.py`: the agent steps and evidence verification
- `scripts/build_dataset.py`: builds `data/providers.json` from the NHS England RTT full CSV extract

## Data

- **Waiting times:** NHS England, *Consultant-led Referral to Treatment Waiting Times*, August 2026 full CSV extract (incomplete pathways), 504 providers. Medians and 92nd percentiles are interpolated from the weekly buckets; providers with fewer than 100 people waiting in a specialty are left out because their figures are too noisy.
- **Provider addresses:** NHS Organisation Data Service. **Coordinates:** postcodes.io.
- **Ratings and news:** live Tavily search, verified as described above.

To refresh the data, download the latest "Full CSV data file" from the NHS England RTT page and run:

```bash
python scripts/build_dataset.py data/raw/<extract>.csv
```

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add NEBIUS_API_KEY and TAVILY_API_KEY
uvicorn app.main:app --port 8000
```

Or with Docker:

```bash
docker build -t waitless . && docker run -p 8000:8000 --env-file .env waitless
```

## Limitations

- The median is the wait of patients *currently* on the list at that provider, which is the standard NHS measure but not a guaranteed appointment date.
- The right to choose covers the first outpatient appointment for most elective referrals in England; WaitLess flags the main exclusions but your GP makes the final clinical call.
- WaitLess is not medical advice.

## License

MIT
