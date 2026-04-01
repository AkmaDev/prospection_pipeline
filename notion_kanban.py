"""
notion_kanban.py — Écriture dans le Kanban Notion (pipeline Pipeline IA)

Structure du Kanban :
🆕 Nouveau lead → 📧 Email envoyé → ⏰ Relance J+7 → 📞 Appel planifié → ✅ Client

Setup (une seule fois) :
1. Créer une intégration sur https://www.notion.so/my-integrations
2. Copier le token dans .env : NOTION_API_KEY=secret_xxx
3. Créer une page Notion vide, partager avec l'intégration
4. Lancer : python notion_kanban.py --setup
   → crée la base de données Kanban automatiquement
   → affiche le NOTION_DATABASE_ID à coller dans .env
"""

import os
import json
import requests
import argparse
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

NOTION_VERSION = "2022-06-28"
BASE_URL = "https://api.notion.com/v1"


def _headers() -> dict:
    token = os.environ.get("NOTION_API_KEY", "")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION,
    }


# ─── Création de la base de données (setup one-shot) ──────────────────────────

def create_kanban_database(parent_page_id: str) -> str:
    """
    Crée la base de données Kanban dans la page Notion spécifiée.
    Retourne le database_id à sauvegarder dans .env.
    """
    payload = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "icon": {"type": "emoji", "emoji": "🎯"},
        "title": [{"type": "text", "text": {"content": "Pipeline Pipeline IA — Prospects"}}],
        "is_inline": False,
        "properties": {
            "Restaurant": {"title": {}},
            "Statut": {
                "select": {
                    "options": [
                        {"name": "🆕 Nouveau lead",    "color": "gray"},
                        {"name": "📧 Email envoyé",    "color": "blue"},
                        {"name": "⏰ Relance J+7",     "color": "yellow"},
                        {"name": "📞 Appel planifié",  "color": "orange"},
                        {"name": "✅ Client",           "color": "green"},
                        {"name": "❌ Pas intéressé",   "color": "red"},
                    ]
                }
            },
            "Score": {"number": {"format": "number"}},
            "Adresse": {"rich_text": {}},
            "Gérant": {"rich_text": {}},
            "LinkedIn gérant": {"url": {}},
            "Système actuel": {"rich_text": {}},
            "CA estimé": {"rich_text": {}},
            "Email contact": {"email": {}},
            "Objet J0": {"rich_text": {}},
            "Email J0": {"rich_text": {}},
            "Objet J3": {"rich_text": {}},
            "Email J3": {"rich_text": {}},
            "Date relance J3": {"date": {}},
            "Objet J7": {"rich_text": {}},
            "Email J7": {"rich_text": {}},
            "Date relance J7": {"date": {}},
            "Objet J14": {"rich_text": {}},
            "Email J14": {"rich_text": {}},
            "Date appel J14": {"date": {}},
            "Objet J30": {"rich_text": {}},
            "Email J30": {"rich_text": {}},
            "Date réactivation J30": {"date": {}},
            "Sequence statut": {
                "select": {
                    "options": [
                        {"name": "J0_a_envoyer",  "color": "gray"},
                        {"name": "J0_envoye",     "color": "blue"},
                        {"name": "J3_a_envoyer",  "color": "purple"},
                        {"name": "J3_envoye",     "color": "pink"},
                        {"name": "J7_a_envoyer",  "color": "yellow"},
                        {"name": "J7_envoye",     "color": "orange"},
                        {"name": "J14_a_envoyer", "color": "red"},
                        {"name": "J14_envoye",    "color": "brown"},
                        {"name": "J30_a_envoyer", "color": "default"},
                        {"name": "reponse_recue", "color": "green"},
                    ]
                }
            },
            "Score justification": {"rich_text": {}},
            "Pain points": {"rich_text": {}},
            "Source BODACC": {"url": {}},
            "Signal": {"rich_text": {}},
            "Créé le": {"date": {}},
        },
    }

    resp = requests.post(f"{BASE_URL}/databases", headers=_headers(), json=payload)
    if resp.status_code != 200:
        raise RuntimeError(f"Notion API erreur {resp.status_code} : {resp.text[:300]}")

    db_id = resp.json()["id"]
    print(f"\n  ✅ Base de données créée !")
    print(f"  ID : {db_id}")
    print(f"\n  Ajoute dans ton .env :")
    print(f"  NOTION_DATABASE_ID={db_id}")
    return db_id


# ─── Écriture d'un prospect dans le Kanban ────────────────────────────────────

def _rt(text: str) -> list:
    """Convertit une chaîne en rich_text Notion (tronqué à 2000 car.)."""
    if not text:
        return []
    return [{"type": "text", "text": {"content": str(text)[:2000]}}]


def push_to_notion(data: dict) -> str | None:
    """
    Crée une carte dans le Kanban Notion pour un prospect analysé.
    Retourne l'URL de la page créée, ou None si erreur.

    data : dict retourné par analyze_restaurant() dans pipeline.py
    """
    db_id = os.environ.get("NOTION_DATABASE_ID", "")
    if not db_id:
        print("  [Notion] NOTION_DATABASE_ID non défini — carte ignorée")
        return None

    token = os.environ.get("NOTION_API_KEY", "")
    if not token:
        print("  [Notion] NOTION_API_KEY non défini — carte ignorée")
        return None

    # Pain points → texte condensé
    pain_points = data.get("pain_points", [])
    pain_text = " | ".join(pain_points) if pain_points else ""

    # Signal BODACC (injecté dans pipeline.py)
    signal = data.get("signal", {})
    signal_label = signal.get("signal_label", "")
    bodacc_url = signal.get("url", "")
    signal_text = f"{signal_label} — {signal.get('why', '')}" if signal_label else ""

    # Date de création (sans timezone — Notion utilise le fuseau du workspace)
    created_at = datetime.now().strftime("%Y-%m-%d")

    properties = {
        "Restaurant": {
            "title": _rt(data.get("restaurant", "?"))
        },
        "Statut": {
            "select": {"name": "🆕 Nouveau lead"}
        },
        "Score": {
            "number": int(data.get("score", 0))
        },
        "Adresse": {
            "rich_text": _rt(data.get("adresse", ""))
        },
        "Gérant": {
            "rich_text": _rt(data.get("gerant_nom", ""))
        },
        "Système actuel": {
            "rich_text": _rt(data.get("systeme_actuel", ""))
        },
        "CA estimé": {
            "rich_text": _rt(data.get("ca_estime", ""))
        },
        "Objet J0": {
            "rich_text": _rt(data.get("email_objet", ""))
        },
        "Email J0": {
            "rich_text": _rt(data.get("email_corps", ""))
        },
        "Objet J3": {
            "rich_text": _rt(data.get("email_relance_j3_objet", ""))
        },
        "Email J3": {
            "rich_text": _rt(data.get("email_relance_j3", ""))
        },
        "Objet J7": {
            "rich_text": _rt(data.get("email_relance_j7_objet", ""))
        },
        "Email J7": {
            "rich_text": _rt(data.get("email_relance_j7", ""))
        },
        "Objet J14": {
            "rich_text": _rt(data.get("email_appel_j14_objet", ""))
        },
        "Email J14": {
            "rich_text": _rt(data.get("email_appel_j14", ""))
        },
        "Objet J30": {
            "rich_text": _rt(data.get("email_reactivation_j30_objet", ""))
        },
        "Email J30": {
            "rich_text": _rt(data.get("email_reactivation_j30", ""))
        },
        "Sequence statut": {
            "select": {"name": data.get("sequence_status", "J0_a_envoyer")}
        },
        "Score justification": {
            "rich_text": _rt(data.get("score_justification", ""))
        },
        "Pain points": {
            "rich_text": _rt(pain_text)
        },
        "Signal": {
            "rich_text": _rt(signal_text)
        },
        "Créé le": {
            "date": {"start": created_at}
        },
    }

    # Champs optionnels (présents seulement si non vides)
    if data.get("gerant_linkedin"):
        properties["LinkedIn gérant"] = {"url": data["gerant_linkedin"]}

    if data.get("email_contact_probable"):
        email = data["email_contact_probable"]
        # Notion rejette les emails malformés
        if "@" in email and "." in email.split("@")[-1]:
            properties["Email contact"] = {"email": email}

    if bodacc_url:
        properties["Source BODACC"] = {"url": bodacc_url}

    if data.get("date_relance_j3"):
        properties["Date relance J3"] = {"date": {"start": data["date_relance_j3"]}}

    if data.get("date_relance_j7"):
        properties["Date relance J7"] = {"date": {"start": data["date_relance_j7"]}}

    if data.get("date_appel_j14"):
        properties["Date appel J14"] = {"date": {"start": data["date_appel_j14"]}}

    if data.get("date_reactivation_j30"):
        properties["Date réactivation J30"] = {"date": {"start": data["date_reactivation_j30"]}}

    payload = {
        "parent": {"database_id": db_id},
        "properties": properties,
    }

    resp = requests.post(f"{BASE_URL}/pages", headers=_headers(), json=payload)

    if resp.status_code == 200:
        page_url = resp.json().get("url", "")
        print(f"  [Notion] ✅ Carte créée : {data.get('restaurant', '?')} → {page_url}")
        return page_url
    else:
        print(f"  [Notion] ❌ Erreur {resp.status_code} : {resp.text[:200]}")
        return None


# ─── Setup interactif ─────────────────────────────────────────────────────────

def setup_wizard():
    """
    Crée la base de données Kanban dans Notion.
    L'utilisateur fournit l'ID de la page parent (visible dans l'URL Notion).
    """
    print("\n" + "=" * 62)
    print("  SETUP NOTION KANBAN — Pipeline Pipeline IA")
    print("=" * 62)

    token = os.environ.get("NOTION_API_KEY", "")
    if not token:
        print("""
  ❌ NOTION_API_KEY manquante.

  Étapes :
  1. Va sur https://www.notion.so/my-integrations
  2. Clique "New integration" → nom : "Pipeline IA Pipeline"
  3. Copie le token (commence par secret_...)
  4. Ajoute dans monmaster/.env :
     NOTION_API_KEY=secret_xxxxxxx
  5. Relance ce script.
""")
        return

    print(f"\n  Token Notion : OK ({token[:15]}...)")
    print("""
  Maintenant :
  1. Ouvre Notion
  2. Crée une nouvelle page vide (ex: "Pipeline Pipeline IA")
  3. Clique sur les 3 points ••• en haut à droite
  4. "Connections" → ajoute "Pipeline IA Pipeline"
  5. Dans l'URL de la page, copie l'ID :
     notion.so/Mon-titre-[ICI-32-caractères]
     Ex : https://notion.so/Pipeline-Pipeline IA-abc123def456...
     → l'ID est : abc123def456...  (ou avec tirets : abc123de-f456-...)
""")

    page_id = input("  Colle l'ID de la page parent ici : ").strip()
    page_id = page_id.replace("-", "").replace(" ", "")

    if len(page_id) != 32:
        print(f"  ❌ ID invalide (longueur {len(page_id)}, attendu 32 caractères)")
        return

    print(f"\n  Création de la base de données...")
    try:
        db_id = create_kanban_database(page_id)
        print(f"""
  🎉 Tout est prêt !

  Ajoute ces 2 lignes dans monmaster/.env :
  NOTION_API_KEY={token}
  NOTION_DATABASE_ID={db_id}

  Puis configure la vue Kanban dans Notion :
  - Ouvre la base de données créée
  - Clique "+ Add a view" → Board
  - Champ de groupement : "Statut"
  → Tu vois le Kanban avec les 5 colonnes.
""")
    except RuntimeError as e:
        print(f"  ❌ Erreur : {e}")


# ─── Test unitaire ────────────────────────────────────────────────────────────

def test_push():
    """Pousse un prospect de test dans le Kanban."""
    from datetime import timedelta
    today = datetime.now()
    dummy = {
        "restaurant": "Big Fernand — Test",
        "adresse": "75009 Paris",
        "gerant_nom": "Jean-Baptiste Dupont",
        "gerant_linkedin": "https://linkedin.com/in/jb-dupont",
        "gerant_titre": "Directeur général",
        "systeme_actuel": "Lightspeed Restaurant",
        "ca_estime": "480 000 €",
        "email_contact_probable": "contact@bigfernand.fr",
        "pain_points": [
            "3 tablettes livraison séparées",
            "Support Lightspeed trop lent",
            "Pas d'analytics temps réel",
        ],
        "score": 78,
        "score_justification": "+25 concurrent Lightspeed, +20 fort volume, +15 multi-sites, +10 croissance, +8 signal",
        "statut": "prospect_chaud",
        "email_objet": "Big Fernand — unifier vos 3 tablettes livraison ?",
        "email_corps": "Bonjour Jean-Baptiste,\n\nJ'ai vu que vous gérez la livraison sur 3 tablettes séparées...",
        "email_relance_j7_objet": "Relance — ROI : 1 tablet vs 3",
        "email_relance_j7": "Petite relance. En chiffres : 85% de réduction des erreurs de commande...",
        "email_appel_j14_objet": "15 minutes ?",
        "email_appel_j14": "Dernier message de ma part.\n15 min cette semaine pour voir si ça colle ?",
        "date_relance_j7": (today + timedelta(days=7)).strftime("%Y-%m-%d"),
        "date_appel_j14": (today + timedelta(days=14)).strftime("%Y-%m-%d"),
        "sequence_status": "J0_a_envoyer",
        "signal": {
            "signal_label": "Créations",
            "why": "Nouveau fonds de commerce enregistré au BODACC",
            "url": "https://www.bodacc.fr/pages/annonces-commerciales-detail/?q.id=id:A202600611356",
        },
        "timestamp": today.isoformat(),
    }
    url = push_to_notion(dummy)
    if url:
        print(f"\n  Ouvre Notion et vérifie : {url}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Notion Kanban — Setup & test")
    parser.add_argument("--setup", action="store_true", help="Créer la base de données Kanban")
    parser.add_argument("--test", action="store_true", help="Pousser un prospect de test")
    args = parser.parse_args()

    if args.setup:
        setup_wizard()
    elif args.test:
        test_push()
    else:
        parser.print_help()
