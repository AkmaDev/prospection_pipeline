#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pipeline de prospection IA — v3
Clay pour la restauration : Discovery + Scoring + Email personnalisé

Modes :
  Discovery (nouveau) : trouve des restaurants inconnus qui matchent des critères
    python pipeline.py --discover "fast food" --city Paris --limit 20
    python pipeline.py --discover "franchise livraison" --city Lyon --limit 10

  Single (conservé) : analyse un restaurant connu
    python pipeline.py --single "O'Tacos" --city "Paris 12"

Architecture :
  Discovery : Outscraper Google Maps search → N restaurants inconnus avec données complètes
  Analysis  : Claude Opus 4.6 (1 appel/restaurant) → score + email personnalisé
  Output    : outputs/{slug}.json par restaurant + outputs/summary_{timestamp}.json
"""

import sys
import io
# Redirection UTF-8 uniquement en exécution directe (pas quand importé par Streamlit)
if sys.stdout and hasattr(sys.stdout, 'buffer') and __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import json
import re
import argparse
import os
import requests
from pathlib import Path
from dotenv import load_dotenv
import anthropic

# Charge le .env depuis la racine du projet (monmaster/.env)
load_dotenv(Path(__file__).parent.parent / ".env")
from datetime import datetime, timedelta

try:
    from outscraper import ApiClient as OutscraperClient
    OUTSCRAPER_AVAILABLE = True
except ImportError:
    OUTSCRAPER_AVAILABLE = False

try:
    from notion_kanban import push_to_notion
    NOTION_AVAILABLE = True
except ImportError:
    NOTION_AVAILABLE = False
    def push_to_notion(data): return None

CLIENT = anthropic.Anthropic()

# ─── Config dynamique (env) ───────────────────────────────────────────────────

COMPANY_NAME    = os.environ.get("COMPANY_NAME", "POS Solutions")
SALES_REP_NAME  = os.environ.get("SALES_REP_NAME", "Votre commercial")
COMPANY_CONTEXT = os.environ.get(
    "COMPANY_CONTEXT",
    "Solution de caisse et gestion complète pour les restaurants. "
    "Unifie commandes salle, livraison et click & collect dans une seule interface. "
    "Analytics temps réel, réduction des erreurs de caisse.",
)
DEMO_MODE = os.environ.get("DEMO_MODE", "false").lower() == "true"
# ANTHROPIC_MODEL surcharge le choix du modèle indépendamment du mode démo.
# Par défaut : Haiku si DEMO_MODE=true, Opus sinon.
MODEL = os.environ.get(
    "ANTHROPIC_MODEL",
    "claude-haiku-4-5-20251001" if DEMO_MODE else "claude-opus-4-6",
)

# ─── System prompt ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = f"""Tu es un expert en prospection commerciale B2B pour la restauration.

{COMPANY_NAME} en bref :
{COMPANY_CONTEXT}

CONCURRENTS QU'ON CIBLE (par ordre de priorité) :
1. Lightspeed Restaurant - cher, support lent → priorité MAX
2. Zelty - limité pour les chaînes → priorité haute
3. Caisses non connectées (traditionnel) → priorité moyenne
4. SumUp - trop basique → priorité moyenne
5. Laddition, Tiller → priorité normale

CRITÈRES DE SCORING (0-100) :
Points positifs :
+35 pts : Signal BODACC < 30 jours (fenêtre d'or — contacter maintenant)
+20 pts : Signal BODACC 30-60 jours (fenêtre active)
+25 pts : Utilise Lightspeed, Zelty ou concurrent direct
+20 pts : Fort volume commandes (restauration rapide, franchise)
+20 pts : Livraison sans intégration unifiée (tablettes multiples)
+15 pts : Multi-établissements ou franchise
+15 pts : Gérant identifié sur LinkedIn
+10 pts : Signaux de croissance récente (recrutement, expansion)
+10 pts : Contrat qui expire bientôt (signal détectable)

Score de Maturité Projet (SMP) — bonus/malus à intégrer :
+25 : Contrainte externe détectée (ouverture < 2 semaines, changement gérant récent)
+15 : Recrutement actif de personnel (expansion = budget disponible)
+10 : Secteur en forte croissance

Décréments (obligatoires si applicable) :
-20 pts : Lead inactif depuis 30+ jours (aucune nouvelle interaction détectable)
-50 pts : Email bounce ou faux contact (disqualifiant — ne pas contacter)

RÈGLES ABSOLUES pour l'email :
- Ligne 1 : citer leur système actuel ou un fait spécifique
- Maximum 150 mots dans le corps (J+0) — respecter strictement
- Inclure 1 chiffre concret pertinent pour eux
- Signature : {SALES_REP_NAME}, {COMPANY_NAME}

PHRASES INTERDITES (jamais, sous aucun prétexte) :
"je me permets", "n'hésitez pas", "suite à notre échange",
"j'espère que vous allez bien", "je vous contacte au sujet de",
"dans le cadre de", "permettez-moi de", "j'espère que ce message vous trouve bien"

RÈGLES ABSOLUES SUR LES DONNÉES ET RÉFÉRENCES (violations = réponse invalide) :
⛔ INTERDIT d'inventer des noms de restaurants, de clients ou de cas d'usage réels.
⛔ INTERDIT de citer des statistiques ou chiffres non présents dans les données fournies.
   Pas de "8-12% de marge perdue", pas de "vos voisins ont switché chez X", pas de
   "la Brasserie Y dans votre ville a économisé Z€".
⛔ INTERDIT de localiser un client fictif dans la même ville ou région que le prospect.
✅ Pour les chiffres : utiliser uniquement des formulations générales explicitement vagues :
   "typiquement", "en général", "les restaurants similaires tendent à...".
✅ Pour les noms : ne citer aucun restaurant précis sauf s'il est fourni dans les données.

RÈGLE LINGUISTIQUE (crucial pour la personnalisation) :
Dans les données reçues (description, catégories, avis, site web), identifie
2-3 mots ou expressions que CE restaurant utilise lui-même pour se décrire.
Réintègre ces expressions dans l'email de manière naturelle.
Le prospect doit sentir que tu as regardé son profil, pas envoyé un template.

Retourne UNIQUEMENT un JSON valide, sans markdown, sans texte avant ou après."""

# ─── Amélioration 1 : Enrichissement gérant via Exa ──────────────────────────

def _gerant_confidence(profile_title: str, profile_url: str,
                        restaurant_name: str, city: str) -> str:
    """
    Retourne "high" si le profil LinkedIn semble correspondre au restaurant,
    "low" sinon. Un faux positif "high" est acceptable ; un faux positif "low"
    fait juste qu'on n'utilise pas le nom dans l'email — pas de dégât.
    """
    def normalize(s: str) -> str:
        import unicodedata
        s = unicodedata.normalize("NFD", s.lower())
        return "".join(c for c in s if unicodedata.category(c) != "Mn")

    title_n = normalize(profile_title)
    url_n   = normalize(profile_url)
    name_n  = normalize(restaurant_name)
    city_n  = normalize(city)

    # Mots significatifs du nom du restaurant (longueur > 3 pour ignorer articles)
    name_words = [w for w in name_n.split() if len(w) > 3]

    # Le profil est "high" si au moins un mot du restaurant OU la ville apparaît
    # dans le titre ou l'URL du profil
    matched = any(w in title_n or w in url_n for w in name_words)
    matched = matched or (len(city_n) > 3 and city_n in title_n)

    return "high" if matched else "low"


def enrich_gerant(name: str, city: str) -> dict:
    """
    Cherche le gérant/propriétaire sur LinkedIn via Exa people search.
    Retourne : gerant_nom, gerant_linkedin, gerant_titre, gerant_confidence.
    gerant_confidence = "high" | "low" — "low" = profil trouvé mais non vérifié,
    ne pas utiliser le nom dans les emails.
    """
    api_key = os.environ.get("EXA_API_KEY")
    if not api_key:
        return {}

    query = f"gérant propriétaire directeur restaurant {name} {city}"
    try:
        resp = requests.post(
            "https://api.exa.ai/search",
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            json={
                "query": query,
                "type": "neural",
                "num_results": 5,
                "include_domains": ["linkedin.com"],
                "use_autoprompt": True,
            },
            timeout=10,
        )
        if resp.status_code != 200:
            return {}

        results = resp.json().get("results", [])
        for r in results:
            url   = r.get("url", "")
            title = r.get("title", "")
            if "linkedin.com/in/" in url and title:
                parts      = title.split(" - ")
                confidence = _gerant_confidence(title, url, name, city)
                return {
                    "gerant_nom":        parts[0].strip() if parts else title,
                    "gerant_titre":      parts[1].strip() if len(parts) > 1 else "Gérant / Propriétaire",
                    "gerant_linkedin":   url,
                    "gerant_confidence": confidence,
                }
    except Exception as e:
        print(f"  [!] Exa gérant : {e}")
    return {}


# ─── Outscraper ───────────────────────────────────────────────────────────────

def get_outscraper_client():
    api_key = os.environ.get("OUTSCRAPER_API_KEY")
    if not api_key or not OUTSCRAPER_AVAILABLE:
        return None
    return OutscraperClient(api_key=api_key)


def scrape_contact_from_website(website_url: str) -> dict:
    """
    Scrape un site restaurant → retourne email + téléphone. 100% gratuit.
    Essaie homepage puis /contact, /nous-contacter, /a-propos, /about.
    Retourne : {"email": str|None, "phone": str|None}
    """
    from urllib.parse import urljoin

    EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')
    PHONE_RE = re.compile(r'(?:(?:\+|00)33[\s.\-]?|0)[1-9](?:[\s.\-]?\d{2}){4}')
    NOISE_EMAIL = ["sentry", "example.com", "jquery", "schema.org", "w3.org",
                   "google", "facebook", "instagram", "twitter", "wixpress",
                   "shopify", "amazonaws", "cloudflare"]

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    result = {"email": None, "phone": None}

    for path in ["", "/contact", "/nous-contacter", "/a-propos", "/about"]:
        if result["email"] and result["phone"]:
            break
        try:
            url = urljoin(website_url, path) if path else website_url
            r = requests.get(url, headers=headers, timeout=5, allow_redirects=True)
            if r.status_code != 200:
                continue

            if not result["email"]:
                for email in EMAIL_RE.findall(r.text):
                    if not any(n in email.lower() for n in NOISE_EMAIL) and "." in email.split("@")[1]:
                        result["email"] = email.lower()
                        break

            if not result["phone"]:
                # Priorité aux liens tel: (plus fiables)
                tel_match = re.search(r'tel:([\d\s\+\-\.]{8,})', r.text)
                if tel_match:
                    p = re.sub(r'[\s.\-]', '', tel_match.group(1))
                    if p.startswith("+33"):
                        p = "0" + p[3:]
                    if len(p) == 10:
                        result["phone"] = p
                if not result["phone"]:
                    phones = PHONE_RE.findall(r.text)
                    if phones:
                        p = re.sub(r'[\s.\-]', '', phones[0])
                        if p.startswith("+33"):
                            p = "0" + p[3:]
                        if len(p) == 10:
                            result["phone"] = p
        except Exception:
            continue

    return result


def scrape_tripadvisor_contact(name: str, city: str) -> dict:
    """
    Cherche le téléphone d'un restaurant sur TripAdvisor.
    Utilise Exa pour trouver l'URL TripAdvisor, puis scrape la page (HTTP 200, données dans HTML).
    Retourne : {"phone": str|None, "tripadvisor_url": str|None}
    """
    api_key = os.environ.get("EXA_API_KEY")
    result = {"phone": None, "tripadvisor_url": None}

    if not api_key:
        return result

    # Étape 1 : trouver l'URL TripAdvisor via Exa
    try:
        resp = requests.post(
            "https://api.exa.ai/search",
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            json={
                "query": f"{name} {city} restaurant",
                "type": "neural",
                "num_results": 3,
                "include_domains": ["tripadvisor.fr"],
                "use_autoprompt": False,
            },
            timeout=10,
        )
        if resp.status_code != 200:
            return result

        ta_url = None
        for r in resp.json().get("results", []):
            if "Restaurant_Review" in r.get("url", ""):
                ta_url = r["url"]
                break

        if not ta_url:
            return result
        result["tripadvisor_url"] = ta_url
    except Exception:
        return result

    # Étape 2 : scraper la page TripAdvisor (HTTP 200 confirmé, données en HTML)
    PHONE_RE = re.compile(r'(?:(?:\+|00)33[\s.\-]?|0)[1-9](?:[\s.\-]?\d{2}){4}')
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "fr-FR,fr;q=0.9",
    }
    try:
        r = requests.get(ta_url, headers=headers, timeout=10, allow_redirects=True)
        if r.status_code == 200:
            tel_match = re.search(r'tel:([\d\s\+\-\.]{8,})', r.text)
            if tel_match:
                p = re.sub(r'[\s.\-]', '', tel_match.group(1))
                if p.startswith("+33"):
                    p = "0" + p[3:]
                if len(p) == 10:
                    result["phone"] = p
            if not result["phone"]:
                phones = PHONE_RE.findall(r.text)
                if phones:
                    p = re.sub(r'[\s.\-]', '', phones[0])
                    if p.startswith("+33"):
                        p = "0" + p[3:]
                    if len(p) == 10:
                        result["phone"] = p
    except Exception:
        pass

    return result


def discover_restaurants_free(sector: str, city: str, limit: int = 20) -> list[dict]:
    """
    Discovery Google Maps sans API — gratuit, HTTP + regex.
    Fallback automatique quand OUTSCRAPER_API_KEY est absente.
    Extrait les domaines de sites web référencés dans la page Google Maps.
    """
    import time

    query = f"{sector} {city}".replace(" ", "+")
    url = f"https://www.google.com/maps/search/{query}"
    BLOCKED = ["google", "gstatic", "schema.org", "w3.org", "facebook",
               "maps.app", "googleapis", "goo.gl", "youtube", "wikipedia",
               "yelp", "tripadvisor"]

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        url_re = re.compile(r'https?://[^\s"\'<>]+')
        seen_domains, restaurants = set(), []

        for found_url in url_re.findall(resp.text):
            parts = found_url.split("/")
            if len(parts) < 3:
                continue
            domain = parts[2].lower().replace("www.", "")
            if any(b in domain for b in BLOCKED) or domain in seen_domains:
                continue
            seen_domains.add(domain)
            restaurants.append({
                "name": domain,
                "site": found_url,
                "source": "free_scrape",
            })
            if len(restaurants) >= limit:
                break

        if not restaurants:
            print("  [!] Google Maps free scraping : 0 résultat (possible rate-limit Google).")
            print("      → Ajouter OUTSCRAPER_API_KEY dans .env pour un résultat fiable.")
        else:
            print(f"  → {len(restaurants)} site(s) extrait(s) via free scraping")

        return restaurants

    except Exception as e:
        print(f"  [!] Erreur Google Maps free scraping : {e}")
        return []


def discover_restaurants(sector: str, city: str, limit: int) -> list[dict]:
    """
    Discovery : Outscraper Google Maps search → liste de restaurants inconnus.
    Retourne jusqu'à `limit` restaurants avec leurs données complètes.
    """
    client = get_outscraper_client()
    if not client:
        print("  [!] OUTSCRAPER_API_KEY manquante — fallback sur free scraping...")
        return discover_restaurants_free(sector, city, limit)

    query = f"{sector} {city}"
    print(f"  Recherche Google Maps : \"{query}\" (max {limit} résultats)...")

    try:
        results = client.google_maps_search(query, limit=limit, language="fr")
        restaurants = []
        for r in results:
            if isinstance(r, list):
                restaurants.extend(r)
            elif isinstance(r, dict):
                restaurants.append(r)
        restaurants = [r for r in restaurants if isinstance(r, dict) and r.get("name")]
        print(f"  → {len(restaurants)} restaurant(s) trouvé(s)")
        return restaurants[:limit]
    except Exception as e:
        print(f"  [!] Erreur Outscraper : {e}")
        return []


def fetch_single_restaurant(name: str, city: str) -> dict:
    """Enrichissement d'un restaurant connu (mode single)."""
    client = get_outscraper_client()
    if not client:
        return {}
    try:
        results = client.google_maps_search(f"{name} {city}", limit=1, language="fr")
        if results and results[0]:
            r = results[0]
            return r[0] if isinstance(r, list) else r
    except Exception as e:
        print(f"  [!] Outscraper erreur : {e}")
    return {}

# ─── Claude analysis ──────────────────────────────────────────────────────────

USEFUL_FIELDS = [
    "name", "full_address", "type", "rating", "reviews",
    "phone", "site", "description", "about",
    "working_hours", "price_level", "subtypes",
    "reviews_tags", "reviews_per_score"
]


def analyze_restaurant(restaurant_data: dict, stream_callback=None) -> dict:
    """
    Analyse Claude : prend les données Outscraper déjà fetchées,
    retourne le JSON scoré + séquence email complète (J0/J3/J7/J14/J30).
    stream_callback : callable(text: str) appelé à chaque token si streaming activé.
    """
    name = restaurant_data.get("name", "Restaurant inconnu")
    address = restaurant_data.get("full_address", "")

    filtered = {k: v for k, v in restaurant_data.items()
                if k in USEFUL_FIELDS and v}

    # Amélioration 1 — Enrichissement gérant
    gerant = restaurant_data.get("_gerant") or enrich_gerant(name, address or "Paris")
    gerant_section = ""
    if gerant.get("gerant_nom"):
        confidence = gerant.get("gerant_confidence", "low")
        if confidence == "high":
            gerant_section = (
                f"\n👤 GÉRANT CONFIRMÉ : {gerant['gerant_nom']} "
                f"({gerant.get('gerant_titre', '?')}) — {gerant.get('gerant_linkedin', '')}\n"
                f"→ Commence l'email par son prénom. Exemple : 'Bonjour {gerant['gerant_nom'].split()[0]},'\n"
                f"   Mentionne son rôle de manière naturelle si pertinent.\n"
            )
        else:
            gerant_section = (
                f"\n⚠ PROFIL LINKEDIN NON VÉRIFIÉ : {gerant['gerant_nom']} "
                f"({gerant.get('gerant_titre', '?')}) — lien possible mais non confirmé.\n"
                f"→ NE PAS utiliser ce nom dans l'email. Commence par 'Bonjour,' sans prénom.\n"
                f"   Mets gerant_nom à 'non identifié' dans le JSON de sortie.\n"
            )

    # Waterfall contact — email + téléphone depuis le site web, puis TripAdvisor
    import time as _time
    missing_email = not restaurant_data.get("email")
    missing_phone = not restaurant_data.get("phone")

    if (missing_email or missing_phone) and restaurant_data.get("site"):
        print("  Recherche contact sur le site web (free scraping)...")
        contact = scrape_contact_from_website(restaurant_data["site"])
        if contact.get("email") and missing_email:
            restaurant_data["email"] = contact["email"]
            filtered["email"] = contact["email"]
            print(f"  → Email trouvé (site web) : {contact['email']}")
            missing_email = False
        if contact.get("phone") and missing_phone:
            restaurant_data["phone"] = contact["phone"]
            filtered["phone"] = contact["phone"]
            print(f"  → Téléphone trouvé (site web) : {contact['phone']}")
            missing_phone = False
        _time.sleep(1)

    # Fallback téléphone via TripAdvisor (si Exa dispo et phone toujours manquant)
    if missing_phone:
        city_hint = address or "Paris"
        print("  Recherche téléphone sur TripAdvisor...")
        ta = scrape_tripadvisor_contact(name, city_hint)
        if ta.get("phone"):
            restaurant_data["phone"] = ta["phone"]
            filtered["phone"] = ta["phone"]
            print(f"  → Téléphone trouvé (TripAdvisor) : {ta['phone']}")
        _time.sleep(1)

    # Contexte signal temporel (injecté par le mode --signals)
    signal_context = restaurant_data.get("_signal_context", "")
    signal_section = ""
    if signal_context:
        signal_section = f"""
⚡ SIGNAL TEMPOREL DÉTECTÉ :
{signal_context}

IMPORTANT : Commence l'email par ce signal. Exemple :
"J'ai vu que vous venez d'ouvrir votre restaurant..." ou
"Votre annonce pour un chef de cuisine m'a alerté..."
Ne jamais commencer par "Bonjour" ou une formule générique.

"""

    # Calcul des dates de séquence
    today = datetime.now()
    date_j3 = (today + timedelta(days=3)).strftime("%Y-%m-%d")
    date_j7 = (today + timedelta(days=7)).strftime("%Y-%m-%d")
    date_j14 = (today + timedelta(days=14)).strftime("%Y-%m-%d")
    date_j30 = (today + timedelta(days=30)).strftime("%Y-%m-%d")

    user_prompt = f"""Voici les données de ce restaurant :{signal_section}{gerant_section}

{json.dumps(filtered, ensure_ascii=False, indent=2)}

Sur la base de ces données, effectue une analyse commerciale complète :
1. Identifie leur système de caisse actuel si possible (Lightspeed, Zelty, SumUp, etc.)
2. Détermine leurs pain points probables
3. Identifie 2-3 expressions qu'ils utilisent pour se décrire (pour le langage miroir)
4. Calcule le score selon les critères de scoring, en intégrant le SMP :
   - Applique les bonus SMP si tu détectes une contrainte externe (ouverture imminente,
     changement gérant, recrutement actif)
   - Applique les décréments si applicable (-20 inactif, -50 bounce)
   - La justification doit détailler les critères appliqués
5. Rédige la SÉQUENCE COMPLÈTE de 5 emails :
   - email_corps (J+0) : email d'ouverture, 150 mots max, ancré sur un fait BODACC ou signal
     précis détecté. JAMAIS les phrases interdites.
   - email_relance_j3 (J+3) : preuve sociale — décris un bénéfice concret pour CE TYPE de
     restaurant (basé sur les données reçues). ⛔ Sans nommer aucun restaurant fictif.
     Formule autorisée : "Les restaurants [segment] qui unifient leurs commandes..."
     Formule interdite : "[Nom inventé] à [ville du prospect] avait le même défi..."
     60 mots max.
   - email_relance_j7 (J+7) : relance + ADERA — angle ROI différent ET pré-réponse à
     l'objection la plus probable pour ce prospect (identifie l'objection parmi :
     "pas le budget", "trop occupé pour changer", "on a déjà un système qui marche",
     "on verra après l'été"). Retourner l'objection avec 1 fait — si aucun chiffre réel
     n'est disponible dans les données, utiliser une formulation générale ("typiquement",
     "en général") plutôt qu'inventer un pourcentage précis.
     100 mots max. Commence par "Petite relance sur mon message de la semaine dernière..."
   - email_appel_j14 (J+14) : 3 lignes maximum, demande directe de 15 min.
     "Dernier message de ma part. 15 min cette semaine pour voir si ça colle ?"
   - email_reactivation_j30 (J+30) : réactivation après silence total — angle "info marché",
     pas de pression. 80 mots max. Commence par "Je ne veux pas insister, mais [fait nouveau
     sur leur secteur ou concurrent]..." — ce fait doit être générique (tendance marché connue)
     et non inventé pour ce prospect. Donne une raison de répondre sans forcer.

RÈGLE DE SCORE CRITIQUE :
Si le score calculé est < 40 : ne génère PAS les emails.
Mets TOUS les champs email_* et email_*_objet à "" (chaîne vide).
Mets sequence_status à "hors_sequence".
Un lead froid ne doit pas recevoir de séquence — c'est une règle métier absolue.

Retourne UNIQUEMENT ce JSON valide :
{{
  "restaurant": "{name}",
  "adresse": "{address}",
  "gerant_nom": "{gerant.get('gerant_nom', 'non identifié')}",
  "gerant_linkedin": "{gerant.get('gerant_linkedin', '')}",
  "gerant_titre": "{gerant.get('gerant_titre', '')}",
  "type_etablissement": "description courte",
  "taille_estimee": "ex: 40 couverts, 8 employés",
  "systeme_actuel": "système de caisse détecté ou probable",
  "pain_points": ["pain point 1", "pain point 2", "pain point 3"],
  "signaux_positifs": ["signal 1", "signal 2"],
  "ca_estime": "estimation chiffre d'affaires annuel",
  "email_contact_probable": "format probable ex: prenom.nom@restaurant.fr",
  "vocabulaire_prospect": ["expression 1", "expression 2", "expression 3"],
  "score": 72,
  "score_justification": "explication en 1 phrase des points attribués",
  "statut": "prospect_chaud",
  "email_objet": "ligne objet de l'email J+0",
  "email_corps": "corps complet email J+0 avec signature {SALES_REP_NAME}, {COMPANY_NAME}",
  "email_relance_j3_objet": "objet J+3 — preuve sociale (max 50 car.)",
  "email_relance_j3": "corps J+3 — success story similaire + ROI chiffré (60 mots max) avec signature",
  "email_relance_j7_objet": "objet relance J+7 (max 50 car.)",
  "email_relance_j7": "corps relance J+7 + ADERA (100 mots max) avec signature",
  "email_appel_j14_objet": "objet demande appel J+14",
  "email_appel_j14": "3 lignes max — demande 15 min d'appel avec signature",
  "email_reactivation_j30_objet": "objet réactivation J+30 (max 50 car.)",
  "email_reactivation_j30": "corps réactivation J+30 — angle info marché (80 mots max) avec signature",
  "date_relance_j3": "{date_j3}",
  "date_relance_j7": "{date_j7}",
  "date_appel_j14": "{date_j14}",
  "date_reactivation_j30": "{date_j30}",
  "sequence_status": "J0_a_envoyer",
  "notes": "notes commerciales pour l'équipe de vente (objections probables, angle d'appel)"
}}"""

    if stream_callback:
        final_text = ""
        with CLIENT.messages.stream(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        ) as stream:
            for token in stream.text_stream:
                final_text += token
                stream_callback(final_text)
    else:
        response = CLIENT.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        final_text = next(
            (b.text for b in response.content if hasattr(b, "text") and b.text),
            ""
        )

    return _parse_json(final_text, name, restaurant_data)


def _parse_json(text: str, name: str, raw_data: dict) -> dict:
    """Parse JSON depuis la réponse Claude, avec fallback."""
    try:
        t = text.strip()
        if "```" in t:
            for part in t.split("```"):
                if part.startswith("json"):
                    t = part[4:].strip()
                    break
                elif "{" in part:
                    t = part.strip()
                    break
        data = json.loads(t)
        data["timestamp"] = datetime.now().isoformat()
        return data
    except json.JSONDecodeError:
        # Second try : demander à Claude d'extraire le JSON
        try:
            fix = CLIENT.messages.create(
                model=MODEL,
                max_tokens=2048,
                messages=[{
                    "role": "user",
                    "content": f"Extrais uniquement le JSON valide de ce texte :\n\n{text}"
                }]
            )
            fix_text = next((b.text for b in fix.content if hasattr(b, "text")), "{}")
            data = json.loads(fix_text.strip())
            data["timestamp"] = datetime.now().isoformat()
            return data
        except Exception:
            return {
                "restaurant": name,
                "score": 0,
                "statut": "erreur_parsing",
                "email_objet": "",
                "email_corps": "",
                "notes": f"Erreur parsing. Réponse brute : {text[:300]}",
                "timestamp": datetime.now().isoformat()
            }

# ─── Display ──────────────────────────────────────────────────────────────────

def display_result(data: dict):
    score = data.get("score", 0)
    statut = data.get("statut", "")
    sep = "=" * 60

    if score >= 66:
        badge = "🔥"
    elif score >= 41:
        badge = "✅"
    else:
        badge = "⚠️ "

    print(f"\n{sep}")
    print(f"  {badge} {data.get('restaurant')} — {score}/100 — {statut.upper()}")
    print(sep)
    print(f"  Système actuel : {data.get('systeme_actuel', '?')}")
    print(f"  Justification  : {data.get('score_justification', '')}")

    # Gérant identifié
    if data.get("gerant_nom") and data.get("gerant_nom") != "non identifié":
        print(f"\n  👤 Gérant : {data['gerant_nom']} ({data.get('gerant_titre', '?')})")
        if data.get("gerant_linkedin"):
            print(f"     LinkedIn : {data['gerant_linkedin']}")

    vocab = data.get("vocabulaire_prospect", [])
    if vocab:
        print(f"\n  Vocabulaire miroir : {', '.join(repr(v) for v in vocab)}")

    print(f"\n  Pain points :")
    for pp in data.get("pain_points", []):
        print(f"    - {pp}")

    # Séquence emails
    print(f"\n  {'─'*58}")
    print(f"  SÉQUENCE EMAILS — 5 touches planifiées")
    print(f"  {'─'*58}")

    print(f"\n  📧 J+0  — Objet : {data.get('email_objet', '')}")
    print(f"  {'─'*50}")
    print(data.get("email_corps", ""))

    if data.get("email_relance_j3"):
        print(f"\n  📧 J+3  ({data.get('date_relance_j3', '?')}) — Objet : {data.get('email_relance_j3_objet', '')}")
        print(f"  {'─'*50}")
        print(data.get("email_relance_j3", ""))

    if data.get("email_relance_j7"):
        print(f"\n  📧 J+7  ({data.get('date_relance_j7', '?')}) — Objet : {data.get('email_relance_j7_objet', '')}")
        print(f"  {'─'*50}")
        print(data.get("email_relance_j7", ""))

    if data.get("email_appel_j14"):
        print(f"\n  📞 J+14 ({data.get('date_appel_j14', '?')}) — Objet : {data.get('email_appel_j14_objet', '')}")
        print(f"  {'─'*50}")
        print(data.get("email_appel_j14", ""))

    if data.get("email_reactivation_j30"):
        print(f"\n  🔄 J+30 ({data.get('date_reactivation_j30', '?')}) — Objet : {data.get('email_reactivation_j30_objet', '')}")
        print(f"  {'─'*50}")
        print(data.get("email_reactivation_j30", ""))


# ─── Save ─────────────────────────────────────────────────────────────────────

def save_result(data: dict, output_dir: str = "outputs") -> str:
    os.makedirs(output_dir, exist_ok=True)
    name = data.get("restaurant", "unknown")
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    filepath = os.path.join(output_dir, f"{slug}.json")
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return filepath


def save_summary(results: list[dict], output_dir: str = "outputs"):
    """Sauvegarde un résumé trié par score."""
    os.makedirs(output_dir, exist_ok=True)
    sorted_results = sorted(results, key=lambda x: x.get("score", 0), reverse=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    filepath = os.path.join(output_dir, f"summary_{timestamp}.json")
    summary = [
        {
            "restaurant": r.get("restaurant"),
            "adresse": r.get("adresse", ""),
            "score": r.get("score", 0),
            "statut": r.get("statut", ""),
            "systeme_actuel": r.get("systeme_actuel", ""),
            "email_objet": r.get("email_objet", ""),
            "file": re.sub(r"[^a-z0-9]+", "_", r.get("restaurant", "").lower()).strip("_") + ".json"
        }
        for r in sorted_results
    ]
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return filepath, sorted_results


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Pipeline prospection IA v3 — Clay pour la restauration"
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--discover", metavar="SECTEUR",
                      help="Mode discovery via Outscraper : ex: 'fast food', 'franchise livraison'")
    mode.add_argument("--signals", action="store_true",
                      help="Mode signaux : BODACC + SIRENE + JobSpy → restaurants au bon moment")
    mode.add_argument("--single", metavar="NOM",
                      help="Mode single : analyse un restaurant connu par son nom")

    parser.add_argument("--city", default="Paris", help="Ville (défaut: Paris)")
    parser.add_argument("--limit", type=int, default=20,
                        help="Nombre max de restaurants (défaut: 20)")
    parser.add_argument("--days", type=int, default=7,
                        help="Fenêtre temporelle en jours pour le mode signaux (défaut: 7)")
    parser.add_argument("--sources", nargs="+",
                        default=["bodacc", "sirene", "jobspy"],
                        choices=["bodacc", "sirene", "jobspy", "wappalyzer"],
                        help="Sources pour le mode signaux")

    args = parser.parse_args()

    output_dir = os.path.join(os.path.dirname(__file__), "outputs")

    print("\n" + "=" * 55)
    print("  PIPELINE PROSPECTION IA v3")
    print("  Clay pour la restauration")
    print("=" * 55)

    # ── Mode signaux ──────────────────────────────────────────
    if args.signals:
        try:
            from pipeline_signals import get_signals
        except ImportError:
            print("❌ pipeline_signals.py introuvable dans le même dossier.")
            return

        print(f"\n  Mode    : Signaux temporels")
        print(f"  Ville   : {args.city}")
        print(f"  Fenêtre : {args.days} derniers jours")
        print(f"  Sources : {', '.join(args.sources).upper()}")
        print("─" * 55)

        signals = get_signals(
            city=args.city,
            days=args.days,
            limit=args.limit,
            sources=args.sources,
        )

        if not signals:
            print("\n  Aucun signal trouvé. Élargis la fenêtre ou change de ville.")
            return

        print(f"\n  [OK] {len(signals)} signal(s) — lancement de l'analyse Claude...\n")

        results = []
        for i, signal in enumerate(signals, 1):
            name = signal.get("name", "?")
            signal_label = signal.get("signal_label", "")
            readiness = signal.get("buyer_readiness", "")
            print(f"\n  [{i:02d}/{len(signals)}] {name}")
            print(f"         Signal : {signal_label} — {readiness}")

            # Construire les données restaurant depuis le signal
            adresse = signal.get("adresse", "")
            ville = signal.get("ville", "Paris")

            # Amélioration 1 — Enrichissement gérant (Exa)
            print(f"         Gérant : recherche LinkedIn...")
            gerant = enrich_gerant(name, ville)
            if gerant.get("gerant_nom"):
                print(f"         → {gerant['gerant_nom']} ({gerant.get('gerant_titre', '?')})")
            else:
                print(f"         → non trouvé")

            restaurant_data = {
                "name": name,
                "full_address": adresse,
                "type": signal.get("activite", "restaurant"),
                "_gerant": gerant,
                # Contexte signal injecté pour personnaliser l'email
                "_signal_context": (
                    f"Signal détecté : {signal_label} "
                    f"(source : {signal.get('source', '').upper()}, "
                    f"date : {signal.get('signal_date', '')}). "
                    f"Fenêtre de contact : {signal.get('contact_window', '')}. "
                    f"Pourquoi contacter maintenant : {signal.get('why', '')}"
                ),
            }

            data = analyze_restaurant(restaurant_data)
            data["signal"] = signal  # Conserver le signal dans l'output
            score = data.get("score", 0)
            statut = data.get("statut", "")
            print(f"         Score : {score}/100 — {statut}")
            filepath = save_result(data, output_dir)
            # Notion Kanban — carte créée en temps réel
            if NOTION_AVAILABLE and os.environ.get("NOTION_DATABASE_ID"):
                push_to_notion(data)
            results.append(data)

        summary_path, sorted_results = save_summary(results, output_dir)

        print("\n\n" + "=" * 55)
        print(f"  RÉSUMÉ SIGNAUX — {len(results)} prospects analysés")
        print("=" * 55)

        chauds = [r for r in sorted_results if r.get("score", 0) >= 66]
        tièdes = [r for r in sorted_results if 41 <= r.get("score", 0) < 66]

        if chauds:
            print(f"\n🔥 Prospects chauds ({len(chauds)}) — Contacter cette semaine :")
            for r in chauds:
                sig = r.get("signal", {})
                print(f"   {r['score']:3d}/100 — {r['restaurant']} [{sig.get('signal_label', '')}]")

        if tièdes:
            print(f"\n✅ Prospects tièdes ({len(tièdes)}) :")
            for r in tièdes:
                print(f"   {r['score']:3d}/100 — {r['restaurant']}")

        print(f"\n  Résumé : {os.path.basename(summary_path)}")
        print("=" * 55)
        return

    # ── Mode discovery ────────────────────────────────────────
    if args.discover:

        if not os.environ.get("OUTSCRAPER_API_KEY"):
            print("\n❌ OUTSCRAPER_API_KEY requise pour le mode discovery.")
            print("   $env:OUTSCRAPER_API_KEY = 'votre-clé'")
            return

        print(f"\n  Mode    : Discovery")
        print(f"  Secteur : {args.discover}")
        print(f"  Ville   : {args.city}")
        print(f"  Limite  : {args.limit} restaurants")
        print("─" * 55)

        restaurants = discover_restaurants(args.discover, args.city, args.limit)
        if not restaurants:
            print("\n  Aucun restaurant trouvé. Essaie un autre secteur.")
            return

        print(f"\n  [OK] {len(restaurants)} restaurant(s) à analyser\n")

        results = []
        for i, restaurant_data in enumerate(restaurants, 1):
            name = restaurant_data.get("name", "?")
            print(f"\n  [{i:02d}/{len(restaurants)}] {name}")
            data = analyze_restaurant(restaurant_data)
            score = data.get("score", 0)
            statut = data.get("statut", "")
            vocab = data.get("vocabulaire_prospect", [])
            print(f"         Score : {score}/100 — {statut}")
            if vocab:
                print(f"         Miroir : {', '.join(vocab)}")
            filepath = save_result(data, output_dir)
            if NOTION_AVAILABLE and os.environ.get("NOTION_DATABASE_ID"):
                push_to_notion(data)
            results.append(data)

        # Résumé final
        summary_path, sorted_results = save_summary(results, output_dir)

        print("\n\n" + "=" * 55)
        print(f"  RÉSUMÉ — {len(results)} prospects analysés")
        print("=" * 55)

        chauds = [r for r in sorted_results if r.get("score", 0) >= 66]
        tièdes = [r for r in sorted_results if 41 <= r.get("score", 0) < 66]
        froids = [r for r in sorted_results if r.get("score", 0) < 41]

        if chauds:
            print(f"\n🔥 Prospects chauds ({len(chauds)}) :")
            for r in chauds:
                print(f"   {r['score']:3d}/100 — {r['restaurant']}")

        if tièdes:
            print(f"\n✅ Prospects tièdes ({len(tièdes)}) :")
            for r in tièdes:
                print(f"   {r['score']:3d}/100 — {r['restaurant']}")

        if froids:
            print(f"\n⚠️  Prospects froids ({len(froids)}) :")
            for r in froids:
                print(f"   {r['score']:3d}/100 — {r['restaurant']}")

        print(f"\n  Fichiers sauvegardés dans : outputs/")
        print(f"  Résumé trié : {os.path.basename(summary_path)}")
        print("=" * 55)

    # ── Mode single ───────────────────────────────────────────
    else:
        print(f"\n  Mode       : Single")
        print(f"  Restaurant : {args.single}")
        print(f"  Ville      : {args.city}")
        print("─" * 55)

        restaurant_data = {}
        if os.environ.get("OUTSCRAPER_API_KEY") and OUTSCRAPER_AVAILABLE:
            print("  [1/3] Récupération données Google Maps...")
            restaurant_data = fetch_single_restaurant(args.single, args.city)
            if restaurant_data:
                rating = restaurant_data.get("rating", "?")
                reviews = restaurant_data.get("reviews", "?")
                print(f"         → {rating} étoiles, {reviews} avis")
            else:
                print("         → aucune donnée, analyse par Claude seul")
        else:
            print("  [1/3] (Outscraper non configuré — analyse par Claude seul)")
            restaurant_data = {"name": args.single, "full_address": args.city}

        # Amélioration 1 — Enrichissement gérant (Exa)
        print("  [2/3] Recherche LinkedIn gérant...")
        gerant = enrich_gerant(args.single, args.city)
        if gerant.get("gerant_nom"):
            print(f"         → {gerant['gerant_nom']} — {gerant.get('gerant_linkedin', '')}")
            restaurant_data["_gerant"] = gerant
        else:
            print("         → non trouvé (Exa)")

        print("  [3/3] Analyse Claude Opus 4.6 (séquence J0+J3+J7+J14+J30)...")
        data = analyze_restaurant(restaurant_data)

        score = data.get("score", "?")
        statut = data.get("statut", "")
        vocab = data.get("vocabulaire_prospect", [])
        print(f"  [OK]  Score : {score}/100  |  Statut : {statut}")
        if vocab:
            print(f"         Vocabulaire miroir : {', '.join(vocab)}")

        display_result(data)
        filepath = save_result(data, output_dir)
        print(f"\n  [OK] Sauvegardé : {filepath}")
        if NOTION_AVAILABLE and os.environ.get("NOTION_DATABASE_ID"):
            push_to_notion(data)
        print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
