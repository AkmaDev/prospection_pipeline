"""
pipeline_signals.py — Détection de signaux temporels pour la restauration française
Sources gratuites uniquement : BODACC, SIRENE/INSEE, JobSpy, Wappalyzer

Signaux détectés :
  1. BODACC  : nouveaux restaurants (ouverture, cession de fonds, liquidation)
  2. SIRENE  : base nationale des établissements restauration (NAF 56xx)
  3. JobSpy  : offres d'emploi chef/manager → nouveau décideur
  4. Wappalyzer : détection Lightspeed / Zelty / SumUp sur le site web

Usage :
  from pipeline_signals import get_signals
  signals = get_signals(city="Paris", days=7, limit=20)

  Ou en CLI :
  python pipeline_signals.py --city Paris --days 7 --limit 20
"""

import os
import sys
import re
import json
from pathlib import Path
from dotenv import load_dotenv

# Force UTF-8 sur Windows (évite UnicodeEncodeError avec les caractères spéciaux)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv(Path(__file__).parent.parent / ".env")
import argparse
import requests
import subprocess
from datetime import datetime, timedelta
from typing import Optional

# ─── Configuration ────────────────────────────────────────────────────────────

# Codes NAF restauration (INSEE)
NAF_RESTAURATION = ["5610A", "5610B", "5610C", "5621Z", "5629A", "5629B", "5630Z"]

# POS concurrents Innovorder détectables
CONCURRENTS_SIGNATURES = {
    "lightspeed": ["lightspeedapp.com", "lsretail.com", "cloud.lightspeedapp"],
    "zelty":      ["zelty.fr", "zelty.io"],
    "sumup":      ["sumup.com", "sumup-cdn"],
    "laddition":  ["laddition.com"],
    "tiller":     ["tillersystems.com", "tillerpos"],
    "square":     ["squareup.com", "squarespace.com"],  # squarespace = différent, attention
}

# ─── 1. BODACC — Nouveaux restaurants ─────────────────────────────────────────

def fetch_bodacc_new_restaurants(city: str = "", days: int = 7, limit: int = 50) -> list[dict]:
    """
    Interroge l'API BODACC via OpenDataSoft (bodacc-datadila.opendatasoft.com).
    Remplace l'ancienne URL bodacc.fr/api/search/ qui n'est plus valide.

    API : https://bodacc-datadila.opendatasoft.com/api/records/1.0/search/
    Dataset : annonces-commerciales (pas d'auth requise)
    """
    results = []
    date_from = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    base_url = "https://bodacc-datadila.opendatasoft.com/api/records/1.0/search/"

    signaux = [
        ("Créations",        "nouvelle_ouverture", "MAXIMUM",    "30-60 jours",
         "Nouveau restaurant enregistré au BODACC — zéro fournisseur POS en place"),
        ("Ventes et cessions", "cession_fonds",   "TRÈS HAUTE",  "0-30 jours",
         "Fonds de commerce cédé — nouveau propriétaire évalue tous les fournisseurs"),
    ]

    for famille, signal_type, buyer_readiness, contact_window, why in signaux:
        # Construire la requête texte
        mots_cles = "restaurant OR restauration OR brasserie OR bistro OR pizzeria OR traiteur"
        q = mots_cles

        params = {
            "dataset": "annonces-commerciales",
            "q": q,
            "rows": min(limit, 100),
            "sort": "dateparution",
            "refine.familleavis_lib": famille,
        }

        # Filtre géographique via refine (plus fiable que q=)
        if city:
            city_norm = city.strip().lower()
            if city_norm == "paris":
                params["refine.numerodepartement"] = "75"
            else:
                params["refine.ville"] = city.upper()

        try:
            resp = requests.get(base_url, params=params, timeout=15)
            if resp.status_code != 200:
                print(f"  [!] BODACC erreur HTTP {resp.status_code} ({famille})")
                continue

            data = resp.json()
            records = data.get("records", [])

            for record in records:
                fields = record.get("fields", {})
                date_pub = fields.get("dateparution", "")

                if date_pub and date_pub < date_from:
                    continue

                results.append({
                    "source": "bodacc",
                    "signal_type": signal_type,
                    "signal_label": famille,
                    "signal_date": date_pub,
                    "name": fields.get("commercant") or fields.get("denomination") or "?",
                    "adresse": f"{fields.get('cp', '')} {fields.get('ville', '')}".strip(),
                    "ville": fields.get("ville", ""),
                    "cp": fields.get("cp", ""),
                    "activite": fields.get("listeetablissements", "")[:120],
                    "url": fields.get("url_complete", ""),
                    "buyer_readiness": buyer_readiness,
                    "contact_window": contact_window,
                    "why": why,
                })

        except requests.RequestException as e:
            print(f"  [!] BODACC erreur ({famille}) : {e}")

    return results[:limit]


# ─── 2. SIRENE/INSEE — Base nationale restaurants ─────────────────────────────

def fetch_sirene_restaurants(city: str = "", naf_codes: list = None, limit: int = 100) -> list[dict]:
    """
    Interroge l'API SIRENE (INSEE) — clé API directe depuis portail.apisirene.fr.

    Nécessite dans .env : INSEE_API_KEY=... (section "Clés d'API" de ta souscription)
    """
    api_key = os.environ.get("INSEE_API_KEY")
    if not api_key:
        print("  [!] INSEE_API_KEY manquante — SIRENE ignoré")
        print("       portail.apisirene.fr → Souscriptions → Clés d'API")
        return []

    if naf_codes is None:
        naf_codes = NAF_RESTAURATION

    results = []
    naf_query = " OR ".join([f'activitePrincipaleEtablissement:"{c}"' for c in naf_codes])

    city_filter = ""
    if city:
        city_upper = city.upper().replace("-", " ")
        city_filter = f' AND libelleCommuneEtablissement:"{city_upper}"'

    query = f"({naf_query}) AND etatAdministratifEtablissement:A{city_filter}"

    # V3.11 = version actuelle de l'API SIRENE (V3 retourne 404)
    url = "https://api.insee.fr/entreprises/sirene/V3.11/siret"
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    params = {
        "q": query,
        "nombre": min(limit, 1000),
        "tri": "dateCreationEtablissement",
        "ordre": "desc",
    }

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        if resp.status_code != 200:
            print(f"  [!] SIRENE erreur HTTP {resp.status_code}")
            return []

        data = resp.json()
        etablissements = data.get("etablissements", [])

        for etab in etablissements:
            periodes = etab.get("periodesEtablissement", [{}])
            periode = periodes[0] if periodes else {}
            adresse_data = etab.get("adresseEtablissement", {})

            date_creation = etab.get("dateCreationEtablissement", "")
            naf = periode.get("activitePrincipaleEtablissement", "")
            nom = etab.get("uniteLegale", {}).get("denominationUniteLegale", "")
            commune = adresse_data.get("libelleCommuneEtablissement", "")
            adresse = f"{adresse_data.get('numeroVoieEtablissement', '')} {adresse_data.get('libelleVoieEtablissement', '')} {commune}"

            # Signal : ouverture récente (< 6 mois)
            signal_type = "etablissement_actif"
            signal_label = "Établissement restauration actif (SIRENE)"
            buyer_readiness = "NORMALE"
            contact_window = "Quand les autres signaux confirment"

            if date_creation:
                try:
                    date_obj = datetime.strptime(date_creation, "%Y-%m-%d")
                    months_ago = (datetime.now() - date_obj).days / 30
                    if months_ago < 3:
                        signal_type = "ouverture_recente_3mois"
                        signal_label = "Ouvert il y a moins de 3 mois"
                        buyer_readiness = "HAUTE"
                        contact_window = "Immédiatement"
                    elif months_ago < 6:
                        signal_type = "ouverture_recente_6mois"
                        signal_label = "Ouvert il y a 3-6 mois"
                        buyer_readiness = "MOYENNE-HAUTE"
                        contact_window = "Cette semaine"
                except ValueError:
                    pass

            results.append({
                "source": "sirene",
                "signal_type": signal_type,
                "signal_label": signal_label,
                "signal_date": date_creation,
                "name": nom or "Nom non disponible",
                "adresse": adresse.strip(),
                "ville": commune,
                "siret": etab.get("siret", ""),
                "naf": naf,
                "buyer_readiness": buyer_readiness,
                "contact_window": contact_window,
                "why": f"Établissement restauration NAF {naf}, créé le {date_creation}"
            })

    except requests.RequestException as e:
        print(f"  [!] SIRENE erreur : {e}")

    return results[:limit]


# ─── 3. Exa — Offres d'emploi (nouveau décideur) ─────────────────────────────

def fetch_jobspy_signals(city: str = "Paris", days: int = 14, limit: int = 30) -> list[dict]:
    """
    Détecte les offres d'emploi chef/manager dans la restauration via Exa Search API.
    Remplace JobSpy (incompatible Python 3.13).

    Un restaurant qui recrute un chef = nouveau décideur = fenêtre d'achat ouverte.

    Nécessite EXA_API_KEY dans .env (tier gratuit suffisant).
    """
    api_key = os.environ.get("EXA_API_KEY")
    if not api_key:
        print("  [!] EXA_API_KEY manquante — signaux emploi ignorés")
        return []

    results = []
    date_from = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")

    queries = [
        f"offre emploi chef cuisine restaurant {city}",
        f"offre emploi directeur gérant restaurant {city}",
    ]

    for query in queries:
        try:
            resp = requests.post(
                "https://api.exa.ai/search",
                headers={"x-api-key": api_key, "Content-Type": "application/json"},
                json={
                    "query": query,
                    "type": "auto",
                    "num_results": min(limit, 10),
                    "start_published_date": date_from,
                    "include_domains": [
                        "welcometothejungle.com", "indeed.com", "linkedin.com",
                        "apec.fr", "cadremploi.fr", "monster.fr", "jobijoba.com",
                    ],
                    "contents": {"highlights": {"max_characters": 500}},
                },
                timeout=15,
            )

            if resp.status_code != 200:
                print(f"  [!] Exa erreur HTTP {resp.status_code}")
                continue

            for item in resp.json().get("results", []):
                title = item.get("title", "")
                url = item.get("url", "")
                highlights = item.get("highlights", [""])
                snippet = highlights[0] if highlights else ""
                pub_date = item.get("publishedDate", "")[:10]

                # Extraire le nom du restaurant depuis le titre/snippet
                name = title.split(" - ")[0].split(" | ")[0].strip()
                if not name or len(name) > 80:
                    continue

                is_chef = any(k in title.lower() for k in ["chef", "cuisine", "cuisinier", "cook"])
                is_manager = any(k in title.lower() for k in ["directeur", "gérant", "responsable", "manager"])

                if is_chef:
                    signal_type = "recrutement_chef"
                    signal_label = "Recrute un chef de cuisine"
                    why = "Nouveau chef = réévaluation des fournisseurs et outils POS"
                    buyer_readiness = "HAUTE"
                elif is_manager:
                    signal_type = "recrutement_manager"
                    signal_label = "Recrute un manager/directeur"
                    why = "Nouveau décideur entrant = fenêtre d'achat ouverte 30-90 jours"
                    buyer_readiness = "TRÈS HAUTE"
                else:
                    signal_type = "recrutement_general"
                    signal_label = "Offre emploi restauration"
                    why = "Recrutement actif = croissance ou remplacement"
                    buyer_readiness = "MOYENNE-HAUTE"

                results.append({
                    "source": "exa_jobs",
                    "signal_type": signal_type,
                    "signal_label": signal_label,
                    "signal_date": pub_date,
                    "name": name,
                    "adresse": "",
                    "ville": city,
                    "job_url": url,
                    "snippet": snippet[:200],
                    "buyer_readiness": buyer_readiness,
                    "contact_window": "2-4 semaines",
                    "why": why,
                })

        except requests.RequestException as e:
            print(f"  [!] Exa erreur ({query[:40]}…) : {e}")

    return results[:limit]


# ─── 4. Wappalyzer — Détection POS concurrent ────────────────────────────────

def detect_pos_on_website(url: str) -> dict:
    """
    Détecte le POS/caisse utilisé par un restaurant via son site web.
    Utilise wappalyzer (npm) ou une détection heuristique simple.

    Retourne : {"detected": "lightspeed", "confidence": "haute"}
    """
    if not url:
        return {"detected": None, "confidence": None}

    # Essayer d'abord wappalyzer CLI
    try:
        result = subprocess.run(
            ["npx", "wappalyzer", url, "--pretty"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            output = result.stdout.lower()
            for pos, signatures in CONCURRENTS_SIGNATURES.items():
                if any(sig in output for sig in signatures):
                    return {"detected": pos, "confidence": "haute", "method": "wappalyzer"}
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Fallback : analyse heuristique du HTML
    try:
        resp = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        html = resp.text.lower()
        for pos, signatures in CONCURRENTS_SIGNATURES.items():
            if any(sig in html for sig in signatures):
                return {"detected": pos, "confidence": "moyenne", "method": "html_scan"}
    except Exception:
        pass

    return {"detected": None, "confidence": None, "method": "none"}


def enrich_with_pos_detection(restaurants: list[dict]) -> list[dict]:
    """
    Pour chaque restaurant avec un site web connu, détecte le POS utilisé.
    Ajoute un signal de priorité MAX si concurrent direct Innovorder détecté.
    """
    for r in restaurants:
        site = r.get("site") or r.get("url", "")
        if not site:
            continue

        pos_info = detect_pos_on_website(site)
        r["pos_detected"] = pos_info

        if pos_info.get("detected") in ["lightspeed", "zelty"]:
            r["signal_type"] = "concurrent_detecte_" + pos_info["detected"]
            r["signal_label"] = f"Utilise {pos_info['detected'].capitalize()} (concurrent direct)"
            r["buyer_readiness"] = "MAXIMUM"
            r["contact_window"] = "Cette semaine"
            r["why"] = f"{pos_info['detected'].capitalize()} détecté sur leur site — prospect priorité MAX pour Innovorder"

    return restaurants


# ─── Agrégateur principal ─────────────────────────────────────────────────────

def get_signals(
    city: str = "Paris",
    days: int = 7,
    limit: int = 20,
    sources: list = None,
) -> list[dict]:
    """
    Point d'entrée principal. Agrège tous les signaux disponibles.

    Args:
        city    : ville cible
        days    : fenêtre temporelle (signaux des X derniers jours)
        limit   : nombre max de résultats par source
        sources : liste de sources à activer (défaut : toutes)
                  ["bodacc", "sirene", "jobspy", "wappalyzer"]

    Returns:
        Liste de restaurants avec leurs signaux, triés par priorité
    """
    if sources is None:
        sources = ["bodacc", "sirene", "jobspy"]

    all_signals = []

    if "bodacc" in sources:
        print(f"\n  [BODACC] Nouveaux restaurants ({city}, {days} derniers jours)...")
        bodacc = fetch_bodacc_new_restaurants(city=city, days=days, limit=limit)
        print(f"          → {len(bodacc)} signal(s) trouvé(s)")
        all_signals.extend(bodacc)

    if "sirene" in sources:
        print(f"\n  [SIRENE] Établissements restauration actifs ({city})...")
        sirene = fetch_sirene_restaurants(city=city, limit=limit)
        print(f"          → {len(sirene)} établissement(s) trouvé(s)")
        all_signals.extend(sirene)

    if "jobspy" in sources:
        print(f"\n  [EXA JOBS] Offres emploi chef/manager ({city})...")
        jobs = fetch_jobspy_signals(city=city, days=days, limit=limit)
        print(f"          → {len(jobs)} offre(s) trouvée(s)")
        all_signals.extend(jobs)

    # Dédupliquer par nom (fallback sur adresse ou index si nom absent)
    seen = set()
    unique = []
    for i, s in enumerate(all_signals):
        raw_name = s.get("name", "")
        key = re.sub(r"[^a-z0-9]", "", raw_name.lower())
        if not key:
            # Pas de nom — utiliser adresse ou index comme clé unique
            key = re.sub(r"[^a-z0-9]", "", s.get("adresse", str(i)).lower()) or str(i)
        if key not in seen:
            seen.add(key)
            unique.append(s)

    # Trier par priorité
    priority_order = {
        "MAXIMUM": 0,
        "TRÈS HAUTE": 1,
        "HAUTE": 2,
        "MOYENNE-HAUTE": 3,
        "NORMALE": 4,
    }
    unique.sort(key=lambda x: priority_order.get(x.get("buyer_readiness", "NORMALE"), 4))

    return unique


# ─── Affichage ────────────────────────────────────────────────────────────────

def display_signals(signals: list[dict]):
    print(f"\n{'═'*60}")
    print(f"  {len(signals)} signal(s) détecté(s)")
    print(f"{'═'*60}")

    for i, s in enumerate(signals, 1):
        readiness = s.get("buyer_readiness", "?")
        badge = {"MAXIMUM": "🔥", "TRÈS HAUTE": "🔥", "HAUTE": "✅",
                 "MOYENNE-HAUTE": "⚠️ ", "NORMALE": "📋"}.get(readiness, "📋")

        print(f"\n  {badge} [{i:02d}] {s.get('name', '?')}")
        print(f"       Source  : {s.get('source', '?').upper()}")
        print(f"       Signal  : {s.get('signal_label', '?')}")
        print(f"       Date    : {s.get('signal_date', '?')}")
        print(f"       Ville   : {s.get('ville', '?')}")
        print(f"       Priorité: {readiness} — contacter {s.get('contact_window', '?')}")
        print(f"       Pourquoi: {s.get('why', '?')}")


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Détection de signaux temporels — restauration française"
    )
    parser.add_argument("--city", default="Paris", help="Ville cible")
    parser.add_argument("--days", type=int, default=7,
                        help="Fenêtre temporelle en jours (défaut: 7)")
    parser.add_argument("--limit", type=int, default=20,
                        help="Limite par source (défaut: 20)")
    parser.add_argument("--sources", nargs="+",
                        default=["bodacc", "sirene", "jobspy"],
                        choices=["bodacc", "sirene", "jobspy", "wappalyzer"],
                        help="Sources à activer")
    parser.add_argument("--output", help="Sauvegarder en JSON (ex: signals.json)")
    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("  PIPELINE SIGNAUX — Restauration française")
    print(f"  Sources : {', '.join(args.sources).upper()}")
    print("=" * 60)

    signals = get_signals(
        city=args.city,
        days=args.days,
        limit=args.limit,
        sources=args.sources,
    )

    display_signals(signals)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(signals, f, ensure_ascii=False, indent=2)
        print(f"\n  → Sauvegardé : {args.output}")

    print(f"\n{'═'*60}\n")
    return signals


if __name__ == "__main__":
    main()
