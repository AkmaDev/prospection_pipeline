# Pipeline IA — Prospection B2B via BODACC

> Reads France's official business registry (BODACC) every morning at 6am,
> finds newly opened restaurants, scores them on 15 criteria, and generates
> a personalized 5-touch email sequence using Claude.

Built as a school project (L3 AI student, ESGI Paris) — ended up making it a real working system.

*First real test in progress — results coming soon.*

---

## The Problem

A B2B sales team spends 2–4 hours per prospect on manual research:
searching for the restaurant's POS system, scrolling Google Maps reviews,
guessing pain points, writing an email that doesn't sound like a template.

At 20 prospects per week, that's **40–80 hours of manual work** — just for the first touch.

---

## What Makes This Different

Most prospecting tools find contacts. This one uses **BODACC** — France's official
business registry — as a buying signal.

A restaurant that just opened has 3 things in common:
- They haven't signed with a POS vendor yet
- The founder is still personally making decisions
- They're in the first 30–60 days (peak buying window)

BODACC publishes every French business registration, every day, for free.
Nobody was using it for outbound prospecting.

---

## Real Output — O'Tacos Paris 12

```
Score : 65/100  |  Statut : prospect_tiede_a_qualifier
Vocabulaire miroir : "restauration rapide", "halal", "click & collect"

Pain points détectés :
  - 3 tablettes séparées (Uber Eats, Deliveroo, Just Eat) à gérer en rush
  - Pas d'intégration unifiée salle + livraison + bornes
  - Aucun pilotage temps réel sur les 2 points de vente

Email objet : "Vos 3 tablettes Uber Eats, Deliveroo, Just Eat sur un seul écran ?"
```

**Generated email (actual output, not a mock):**

> Bonjour,
>
> Je vois que O'Tacos Porte Dorée et Porte de Vincennes gèrent les commandes
> Uber Eats, Deliveroo et Just Eat en parallèle — c'est 3 tablettes à surveiller
> en plein rush.
>
> Notre solution unifie toutes vos commandes livraison, salle et click & collect
> dans une seule interface. Les restaurants fast-food qui ont franchi le pas
> réduisent typiquement les erreurs de commande et gagnent du temps sur chaque service.
>
> Est-ce qu'un échange de 15 minutes cette semaine vous conviendrait ?

No human wrote this. It took ~25 seconds.

---

## Anti-Hallucination Rules

All Claude prompts enforce strict data discipline:

- ⛔ No invented restaurant names or client references
- ⛔ No fabricated stats or figures not present in the scraped data
- ⛔ No fictional "nearby competitor" or "same-city customer" stories
- ✅ Social proof uses generic segment formulations ("restaurants in this segment typically…")
- ✅ Stats are either sourced from the restaurant's own data or explicitly hedged ("typically", "in general")

Leads scored **< 40/100** get no email sequence generated — only a "cold lead" flag.

---

## How It Works

### 1. Signal detection (BODACC)
Every morning at 6am, the pipeline reads BODACC for:
- New restaurant openings (`Créations`)
- Ownership transfers (`Cessions`)
- Active hiring signals — growth indicator

### 2. Lead scoring (0–100)
15-criteria scoring with dynamic decrements:

| Signal | Points |
|--------|--------|
| BODACC signal < 30 days | +35 |
| Competitor POS detected (Lightspeed, Zelty) | +25 |
| Delivery without unified integration | +20 |
| Manager identified on LinkedIn | +15 |
| Inactive 30+ days | -20 |
| Email bounce | -50 |

Leads below 40/100 are flagged as cold — no sequence generated, no send button shown.

### 3. Email sequence (5 touches via Claude)

| Touch | Goal | Format |
|-------|------|--------|
| J+0 | First contact anchored on BODACC signal | 150 words |
| J+3 | Social proof — segment-level (no invented names) | 60 words |
| J+7 | ROI + ADERA (pre-answering the likely objection) | 100 words |
| J+14 | Call request | 3 lines |
| J+30 | Reactivation with fresh market data | 80 words |

The system uses **mirror language**: it detects the exact words the restaurant uses
to describe itself and reintegrates them naturally. The prospect reads their own
language — they feel understood, not spammed.

### 4. Contact extraction (free waterfall)
1. Scrapes restaurant website (homepage + `/contact`) for email + phone via regex
2. Falls back to TripAdvisor via Exa semantic search
3. Falls back to Claude-estimated email format

### 5. Gerant confidence scoring
LinkedIn enrichment via Exa returns a confidence score:
- **High** — LinkedIn profile title or URL matches the restaurant name/city → used in email
- **Low** — profile is ambiguous (wrong company, different city) → email uses generic opener, UI shows ⚠ badge

### 6. Daily operations
- **6am cron job** — detects new signals, analyzes leads, sends digest email to sales team
- **Streamlit dashboard** — "Today" tab shows Top 5 leads by ITO score, one-click email send
- **Notion CRM** — automatic Kanban sync on every status change

---

## Architecture

```
BODACC API
    │
    ▼
pipeline_signals.py  ──► signal list (new openings, transfers, hiring)
    │
    ▼
pipeline.py
  ├── enrich_gerant()               # LinkedIn enrichment via Exa + confidence scoring
  ├── scrape_contact_from_website() # free email + phone extraction
  ├── scrape_tripadvisor_contact()  # phone fallback via Exa
  └── analyze_restaurant()          # Claude: score + 5-touch email sequence (score ≥ 40 only)
    │
    ├── outputs/*.json
    │       │
    │       └── Notion Kanban (auto-sync)
    │
    └── daily_run.py
            ├── Top 5 ITO ranking (Optimal Timing Index)
            ├── IRP alerts (leads at risk of competitor signing)
            └── Gmail digest at 6am
    │
    ▼
streamlit_app.py  ──► dashboard (Détecter / Agir / Suivre / Analyse manuelle / Contexte)
```

---

## Business Case

| | Manual | This pipeline |
|---|---|---|
| Time per prospect | 2–4 hours | ~25 seconds |
| Cost per prospect | €40–80 (at €20/h) | ~€0.05 |
| Email personalization | Human-written | Mirror vocabulary (automated) |
| CRM update | Manual | Automatic |
| **50 prospects/week** | **100–200 hours** | **~20 minutes** |

Apollo.io charges €99/month. Clay charges €149/month. Neither writes the email.

---

## Stack

| Component | Tool |
|-----------|------|
| LLM | Claude Opus 4.6 (Anthropic) — Haiku in demo mode |
| Semantic search + LinkedIn | Exa |
| Signal source | BODACC (French public registry) |
| Web scraping | `requests` + regex |
| Dashboard | Streamlit |
| CRM | Notion API |
| Email | Gmail SMTP |
| Scheduling | Windows Task Scheduler |

---

## Setup

```bash
git clone https://github.com/AkmaDev/bodacc-prospection-pipeline
cd bodacc-prospection-pipeline/agent_innovorder
pip install anthropic python-dotenv requests streamlit
```

Copy `.env.example` → `.env`:

```env
ANTHROPIC_API_KEY=sk-ant-...       # required

# Enrichment (optional but recommended)
EXA_API_KEY=...                    # LinkedIn + TripAdvisor enrichment

# CRM sync (optional)
NOTION_API_KEY=...
NOTION_DATABASE_ID=...

# Digest email (optional)
DIGEST_EMAIL_FROM=you@gmail.com
DIGEST_EMAIL_TO=you@gmail.com
DIGEST_EMAIL_PASSWORD=xxxx-xxxx    # Gmail app password

# Customization
COMPANY_NAME=Your Company
COMPANY_CONTEXT=Your product description
SALES_REP_NAME=Your Name

# Demo mode (limits to 3 analyses, uses Haiku)
DEMO_MODE=false
DEMO_LIMIT=3
```

```bash
# Schedule daily run at 6am (Windows, run as admin)
setup_cron_windows.bat

# Launch dashboard
streamlit run streamlit_app.py

# Or run manually
python daily_run.py --days 7 --limit 20

# Analyze a single restaurant
python pipeline.py "O'Tacos" "Paris 12"

# Setup Notion Kanban (first time)
python notion_kanban.py --setup
```

---

*Built by [Manassé Akpovi](https://www.linkedin.com/in/manasse-akpovi/) — L3 IA, ESGI Paris*
