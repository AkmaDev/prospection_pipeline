#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agent de prospection commerciale - Demo Innovorder
Construit avec Claude Opus 4.6 + tool use

Usage:
    python agent.py
"""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import anthropic
import json
from datetime import datetime

# ============================================================
# CRM - BASE DE DONNÉES RESTAURANTS (données fictives réalistes)
# ============================================================

RESTAURANTS_DB = {
    "otacos_nation": {
        "id": "otacos_nation",
        "name": "O'Tacos Nation",
        "address": "12 avenue de la Nation, Paris 12ème",
        "contact_name": "Karim Benali",
        "email": "karim.b@otacos-nation.fr",
        "type": "Fast-food / Restauration rapide",
        "covers": 45,
        "staff_count": 8,
        "current_system": "Lightspeed Restaurant",
        "contract_expiry": "Juin 2026",
        "pain_points": [
            "Coût élevé du contrat Lightspeed (~€180/mois)",
            "Support technique lent (48h de délai)",
            "Interface complexe pour les nouveaux employés",
            "Pas d'intégration native avec Uber Eats et Deliveroo → gestion sur 3 tablettes séparées"
        ],
        "monthly_orders": 3200,
        "delivery_share": "45%",
        "avg_ticket": "14€",
        "annual_revenue": "~540k€",
        "status": "prospect_chaud",
        "notes": "Contrat Lightspeed expire dans 3 mois. Très ouvert à changer. Point de douleur principal : la gestion livraison fragmentée."
    },
    "brasserie_du_commerce": {
        "id": "brasserie_du_commerce",
        "name": "La Brasserie du Commerce",
        "address": "45 rue du Commerce, Paris 15ème",
        "contact_name": "Pierre Martin",
        "email": "pierre.martin@brasserieducommerce.fr",
        "type": "Brasserie traditionnelle",
        "covers": 80,
        "staff_count": 6,
        "current_system": "Caisse enregistreuse traditionnelle (non connectée, modèle 2015)",
        "pain_points": [
            "Erreurs de saisie fréquentes en heure de pointe (~5 par service)",
            "Clôture de caisse manuelle : 45 minutes par soir",
            "Aucune visibilité sur les plats les plus vendus",
            "Gestion des stocks entièrement sur papier"
        ],
        "avg_ticket": "28€",
        "peak_hours": "12h-14h / 19h30-22h",
        "annual_revenue": "~420k€",
        "status": "prospect_tiede",
        "notes": "Établissement familial depuis 1987. Pierre est réfractaire à la technologie mais sa fille (qui travaille avec lui) pousse pour moderniser."
    },
    "le_zinc_montmartre": {
        "id": "le_zinc_montmartre",
        "name": "Le Zinc de Montmartre",
        "address": "8 place du Tertre, Paris 18ème",
        "contact_name": "Sophie Leconte",
        "email": "contact@lezincmontmartre.fr",
        "type": "Bistrot / Café-restaurant touristique",
        "covers": 35,
        "staff_count": 4,
        "current_system": "SumUp (application iPad basique)",
        "pain_points": [
            "SumUp trop limité pour la gestion des tables",
            "Pas de gestion des réservations intégrée",
            "Interface uniquement en français → problème avec clientèle étrangère",
            "Aucune analytics : impossible de savoir quels plats performent"
        ],
        "avg_ticket": "22€",
        "tourist_share": "65%",
        "languages_needed": ["FR", "EN", "ES", "IT"],
        "annual_revenue": "~180k€",
        "status": "prospect_froid",
        "notes": "Forte clientèle touristique. Budget serré mais motivée. Interface multilingue = critère clé."
    }
}

# Historique des conversations (simule le CRM email)
CONVERSATION_HISTORY = {}

# ============================================================
# CLIENT ANTHROPIC
# ============================================================

client = anthropic.Anthropic()  # Lit ANTHROPIC_API_KEY automatiquement

# ============================================================
# OUTILS DISPONIBLES POUR L'AGENT
# ============================================================

TOOLS = [
    {
        "name": "lookup_restaurant",
        "description": (
            "Recherche les informations complètes d'un restaurant dans notre CRM Innovorder. "
            "Retourne : type d'établissement, système actuel, pain points, infos contact, notes commerciales, statut prospect."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "restaurant_id": {
                    "type": "string",
                    "description": "L'identifiant du restaurant (ex: otacos_nation, brasserie_du_commerce)"
                }
            },
            "required": ["restaurant_id"]
        }
    },
    {
        "name": "get_conversation_history",
        "description": "Récupère l'historique complet des échanges emails avec un restaurant prospect.",
        "input_schema": {
            "type": "object",
            "properties": {
                "restaurant_id": {
                    "type": "string",
                    "description": "L'identifiant du restaurant"
                }
            },
            "required": ["restaurant_id"]
        }
    },
    {
        "name": "update_crm",
        "description": "Met à jour le statut du prospect dans le CRM et ajoute une note commerciale.",
        "input_schema": {
            "type": "object",
            "properties": {
                "restaurant_id": {"type": "string"},
                "new_status": {
                    "type": "string",
                    "enum": ["prospect_froid", "prospect_tiede", "prospect_chaud", "demo_planifiee", "client"],
                    "description": "Nouveau statut du prospect"
                },
                "note": {
                    "type": "string",
                    "description": "Note commerciale à ajouter dans le CRM"
                }
            },
            "required": ["restaurant_id", "new_status"]
        }
    }
]


def execute_tool(tool_name: str, tool_input: dict) -> str:
    """Exécute un outil et retourne le résultat sous forme de texte."""

    if tool_name == "lookup_restaurant":
        rid = tool_input["restaurant_id"]
        data = RESTAURANTS_DB.get(rid)
        if data:
            return json.dumps(data, ensure_ascii=False, indent=2)
        return f"Aucun restaurant trouvé avec l'ID '{rid}'. IDs disponibles : {list(RESTAURANTS_DB.keys())}"

    elif tool_name == "get_conversation_history":
        rid = tool_input["restaurant_id"]
        history = CONVERSATION_HISTORY.get(rid, [])
        if not history:
            return "Aucun échange précédent avec ce prospect."
        return json.dumps(history, ensure_ascii=False, indent=2)

    elif tool_name == "update_crm":
        rid = tool_input["restaurant_id"]
        if rid in RESTAURANTS_DB:
            old_status = RESTAURANTS_DB[rid]["status"]
            RESTAURANTS_DB[rid]["status"] = tool_input["new_status"]
            if "note" in tool_input:
                date_str = datetime.now().strftime("%d/%m/%Y")
                RESTAURANTS_DB[rid]["notes"] += f"\n[{date_str}] {tool_input['note']}"
            return f"CRM mis à jour : {rid} | {old_status} → {tool_input['new_status']}"
        return f"Restaurant '{rid}' non trouvé dans le CRM."

    return f"Outil inconnu : {tool_name}"


# ============================================================
# SYSTEM PROMPT DE L'AGENT
# ============================================================

SYSTEM_PROMPT = """Tu es Alex, l'agent commercial IA d'Innovorder.

INNOVORDER EN BREF :
Innovorder est le leader français des solutions de caisse et gestion pour la restauration.
2% des Français utilisent nos solutions chaque jour (1 commande sur 50 en France passe par Innovorder).

CE QUE NOTRE SOLUTION FAIT :
- Gestion unifiée des commandes : salle, comptoir, click & collect, livraison — TOUT dans une seule interface
- Intégration automatique de Uber Eats, Deliveroo, Just Eat (fini les 3 tablettes séparées)
- Analytics en temps réel : top des plats, heures de pointe, CA par serveur
- Formation des nouveaux employés en < 30 minutes (interface intuitive)
- Réduction des erreurs de caisse de 85% en moyenne
- Clôture automatique en fin de service

TON RÔLE :
1. EMAIL SORTANT → Rédige un email de prospection ultra-personnalisé basé sur les données CRM du restaurant.
2. RÉPONSE ENTRANTE → Lis le contexte complet, comprends leur situation précise, réponds intelligemment.

RÈGLES ABSOLUES :
- TOUJOURS consulter le CRM ET l'historique avant d'écrire quoi que ce soit
- Mettre à jour le CRM après chaque interaction
- Chaque message cite des éléments PRÉCIS du restaurant (leur système actuel, leurs pain points nommés)
- Ton : professionnel, direct, chaleureux — jamais générique
- Inclure des chiffres concrets

FORMAT DE RÉPONSE OBLIGATOIRE :
Tu DOIS toujours produire le texte de l'email directement, prêt à être envoyé, dans ce format exact :

Objet : [ligne d'objet de l'email]

[Corps de l'email complet, avec formule d'appel, corps, signature]

---
Ne génère PAS de tableaux récapitulatifs. Ne commente PAS l'email. Écris JUSTE l'email prêt à envoyer."""


# ============================================================
# BOUCLE AGENTIQUE PRINCIPALE
# ============================================================

def run_agent(restaurant_id: str, task: str, incoming_message: str = None) -> str:
    """
    Lance l'agent pour une tâche commerciale.

    Args:
        restaurant_id: ID du restaurant dans le CRM
        task: "outreach" (email sortant) ou "reply" (réponse à message entrant)
        incoming_message: le message reçu du prospect (si task == "reply")

    Returns:
        Le texte de l'email généré par l'agent
    """

    # Construire le prompt selon la tâche
    if task == "outreach":
        user_prompt = (
            f"Génère un email de prospection pour le restaurant ID : {restaurant_id}\n"
            "Commence par consulter le CRM pour avoir toutes les informations sur ce restaurant, "
            "puis rédige un email de prospection personnalisé qui montre qu'on connaît leur situation spécifique."
        )

    elif task == "reply":
        # Enregistrer le message entrant dans l'historique
        if restaurant_id not in CONVERSATION_HISTORY:
            CONVERSATION_HISTORY[restaurant_id] = []
        CONVERSATION_HISTORY[restaurant_id].append({
            "from": "prospect",
            "date": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "message": incoming_message
        })

        user_prompt = (
            f"Tu viens de recevoir cette réponse du restaurant ID '{restaurant_id}' :\n\n"
            f"---\n{incoming_message}\n---\n\n"
            "Consulte le CRM et l'historique complet de nos échanges avec ce prospect, "
            "puis rédige une réponse appropriée et commercialement efficace."
        )

    else:
        return "Erreur : task doit être 'outreach' ou 'reply'"

    # Boucle agentique
    messages = [{"role": "user", "content": user_prompt}]

    while True:
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages
        )

        # Afficher les outils utilisés
        for block in response.content:
            if block.type == "tool_use":
                print(f"  🔧 [{block.name}] → {block.input}")

        # L'agent a terminé
        if response.stop_reason == "end_turn":
            final_text = next(
                (block.text for block in response.content if hasattr(block, "text")),
                ""
            )
            # Sauvegarder dans l'historique
            if final_text and restaurant_id:
                if restaurant_id not in CONVERSATION_HISTORY:
                    CONVERSATION_HISTORY[restaurant_id] = []
                CONVERSATION_HISTORY[restaurant_id].append({
                    "from": "innovorder_agent",
                    "date": datetime.now().strftime("%d/%m/%Y %H:%M"),
                    "message": final_text
                })
            return final_text

        # L'agent veut utiliser des outils
        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = execute_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result
                    })

            messages.append({"role": "user", "content": tool_results})

        else:
            break

    return "Erreur : stop_reason inattendu"


# ============================================================
# DÉMO
# ============================================================

def demo():
    separator = "=" * 65

    print(separator)
    print("  🤖 AGENT DE PROSPECTION INNOVORDER — DEMO")
    print(separator)

    # ── SCÉNARIO 1 : Email sortant ──────────────────────────────

    print("\n📋 SCÉNARIO 1 — EMAIL DE PROSPECTION SORTANT")
    print("   Restaurant cible : O'Tacos Nation (prospect chaud, contrat concurrent expirant)")
    print()

    email_sortant = run_agent("otacos_nation", "outreach")

    print("\n📧 EMAIL GÉNÉRÉ PAR L'AGENT :")
    print("-" * 65)
    print(email_sortant)

    # ── SCÉNARIO 2 : Réponse entrante ───────────────────────────

    print("\n" + separator)
    print("📋 SCÉNARIO 2 — RÉPONSE ENTRANTE DU PROSPECT")
    print(separator)

    message_karim = """Bonjour,

Merci pour votre message. Je suis effectivement en train de regarder des alternatives à notre solution actuelle.

Mais concrètement, en quoi vous seriez mieux pour nous ? On fait beaucoup de livraison et c'est souvent le chaos — on jongle entre 3 tablettes différentes et des commandes qui se perdent.

Quel est votre tarif ?

Karim"""

    print("\n📩 MESSAGE REÇU DU PROSPECT :")
    print("-" * 65)
    print(message_karim)

    print("\n   → L'agent analyse et répond...\n")
    reponse_agent = run_agent("otacos_nation", "reply", message_karim)

    print("\n📧 RÉPONSE DE L'AGENT :")
    print("-" * 65)
    print(reponse_agent)

    # ── SCÉNARIO 3 : Objection ──────────────────────────────────

    print("\n" + separator)
    print("📋 SCÉNARIO 3 — GESTION D'OBJECTION")
    print(separator)

    objection_karim = """OK je comprends l'intérêt pour la livraison.

Mais on vient juste de signer un partenariat avec Lightspeed pour 2 ans, donc on ne peut pas changer maintenant.

Revenez me voir dans 2 ans."""

    print("\n📩 OBJECTION DU PROSPECT :")
    print("-" * 65)
    print(objection_karim)

    print("\n   → L'agent traite l'objection...\n")
    reponse_objection = run_agent("otacos_nation", "reply", objection_karim)

    print("\n📧 RÉPONSE DE L'AGENT :")
    print("-" * 65)
    print(reponse_objection)

    print("\n" + separator)
    print("  ✅ DÉMO TERMINÉE")
    print(separator)


if __name__ == "__main__":
    demo()
