# Guide complet — Agent de prospection Innovorder (v2)

## Ce que ce système fait

En une commande, il transforme un nom de restaurant en :
- Une fiche prospect complète (score, pain points, infos enrichies)
- Un email personnalisé avec **le langage du restaurant lui-même**
- Un enregistrement dans le CRM Notion
- Un brouillon dans Gmail

**Ce n'est pas un chatbot.** C'est une pipeline d'automatisation qui écrit dans tes vrais outils.

---

## Architecture (à comprendre absolument)

```
Tu tapes : python pipeline.py "O'Tacos" "Paris 12"
                    │
                    ▼
     ┌──────────────────────────────┐
     │  [1/3] Outscraper            │  ← scraping Google Maps
     │  Si clé dispo : données réelles  │  (note, avis, description, site…)
     │  Sinon : fallback Claude seul│
     └──────────────┬───────────────┘
                    │ JSON structuré (ou rien si pas de clé)
                    ▼
     ┌──────────────────────────────┐
     │  [2/3] Claude Opus 4.6       │  ← UN seul appel API
     │  Analyse les données         │
     │  Score selon critères        │
     │  Détecte le vocabulaire      │
     │  Rédige l'email              │
     └──────────────┬───────────────┘
                    │ JSON structuré
                    ▼
               output.json
                    │
           ┌────────┴────────┐
           ▼                 ▼
      Notion CRM          Gmail
      (fiche créée)    (brouillon)
```

**Différence v1 → v2 :**

| v1 | v2 |
|----|-----|
| Boucle agentique (while True) | Un seul appel Claude |
| web_search Anthropic (13 requêtes) | Outscraper Google Maps (1 requête) |
| Pas de langage miroir | `vocabulaire_prospect` détecté automatiquement |
| ~0,15€/prospect | ~0,05€/prospect |

---

## Les fichiers

### `pipeline.py` — Le moteur principal (v2)

**Ce qu'il fait :** prend un restaurant en entrée, enrichit via Outscraper, analyse avec Claude, sauvegarde output.json.

**Les 3 étapes en code :**

```python
# Étape 1 — Outscraper (si clé configurée)
outscraper_data = fetch_restaurant_data(restaurant_name, city)
# → renvoie : note, nb avis, description, site, heures, type, etc.
# → renvoie {} si pas de clé OUTSCRAPER_API_KEY

# Étape 2 — Claude (un seul appel)
response = client.messages.create(
    model="claude-opus-4-6",
    max_tokens=2048,
    system=SYSTEM_PROMPT,
    messages=[{"role": "user", "content": user_prompt}]
)
# → pas de while True, pas de boucle, pas de tools=[]

# Étape 3 — Parser et sauvegarder
data = json.loads(response.content[0].text)
save_output(data)
```

**Le SYSTEM_PROMPT :** c'est la "personnalité" de l'agent.
- Contient les critères de scoring Innovorder
- Contient la règle du **langage miroir** (voir plus bas)
- Si tu veux changer le comportement → change le SYSTEM_PROMPT, pas le code

**Sans clé Outscraper :** le script affiche `[1/3] (Outscraper non configuré — analyse par Claude seul)` et continue normalement. Claude fait son analyse sur la base de ses connaissances générales du restaurant.

**Comment le lancer :**
```bash
python pipeline.py "Nom du restaurant" "Ville"
```

**Ce qu'il produit :**
- Affichage terminal (score, vocabulaire miroir, pain points, email)
- `output.json` avec toutes les données structurées

---

### `agent.py` — L'agent de réponse aux emails entrants

**Ce qu'il fait :** gère les échanges multi-tours avec un prospect (suite à un email envoyé).
- `task="outreach"` → génère un premier email sortant
- `task="reply"` → répond à un message entrant

**Différence avec pipeline.py :**
- `agent.py` = multi-tours (conversation, mémoire des échanges)
- `pipeline.py` = une seule analyse, sortie structurée JSON

**Les outils dans agent.py :**
```python
TOOLS = [
    "lookup_restaurant",        # lit RESTAURANTS_DB
    "get_conversation_history", # lit l'historique
    "update_crm"                # modifie le statut
]
```
Ces outils sont "maison" (pas serveur Anthropic). Claude les demande, Python les exécute en lisant/écrivant le dictionnaire local `RESTAURANTS_DB`.

---

### `demo_live.py` — Script de démo

Lance la pipeline sur 2 restaurants en séquence, avec pauses. Utilisé pour les démonstrations en visio.

```bash
python demo_live.py
```

---

## Les concepts à maîtriser

### 1. Tool use (utilisation d'outils)

Claude ne peut pas faire d'actions lui-même. Il "demande" des outils.
Tu définis les outils avec une description et un schéma JSON.
Claude lit la description et décide quand les appeler.

**La description est cruciale :**
```python
{
    "name": "lookup_restaurant",
    "description": "Recherche les infos d'un restaurant dans le CRM",
    # ↑ Claude lit ça pour décider s'il doit appeler cet outil
}
```

**Dans pipeline.py v2, il n'y a pas d'outils maison.** Claude ne prend pas de décision d'outil — il reçoit les données d'Outscraper dans le prompt et répond directement en JSON. C'est plus simple et plus rapide.

### 2. System prompt

C'est la "configuration" de l'agent. Il définit :
- Son rôle ("Tu es un expert en prospection B2B pour la restauration")
- Ses règles ("Ligne 1 : citer leur système actuel")
- Son format de sortie ("Retourne UNIQUEMENT ce JSON")

**Règle d'or :** Pour changer le comportement de l'agent, change d'abord le system prompt. Le code ne change presque jamais.

### 3. Langage miroir (vocabulaire_prospect) ← nouvelle feature v2

C'est la feature la plus importante pour la personnalisation.

**Le problème des emails génériques :**
Envoyer "Bonjour, nous proposons une solution de caisse" à 500 restaurants → tout le monde voit que c'est un template.

**La solution :**
Dans le SYSTEM_PROMPT :
```
REGLE LINGUISTIQUE :
Dans les données reçues (description, avis, site web), identifie
2-3 mots ou expressions que CE restaurant utilise lui-même.
Réintègre ces expressions dans l'email de manière naturelle.
```

**Résultat :** Si la description Google Maps dit "cuisine du terroir" et "produits de saison" :
- Email générique : "Pour votre restaurant, nous proposons..."
- Email avec langage miroir : "Pour votre cuisine du terroir, unifier les commandes Uber Eats avec votre service en salle vous permettrait..."

**Le prospect sent qu'on a regardé son profil.** Pas envoyé un template.

Le champ `vocabulaire_prospect` dans output.json contient les expressions détectées.

### 4. Outscraper — enrichissement Google Maps

**Ce que c'est :** un service de scraping légal de Google Maps. Pour chaque restaurant, il retourne :
- Note Google (ex : 4.3 étoiles)
- Nombre d'avis (ex : 287)
- Description (texte que le restaurant a écrit lui-même)
- Catégories (ex : "Restaurant de cuisine française")
- Heures d'ouverture
- Site web, téléphone
- Avis des clients (les tags les plus fréquents)

**Pourquoi c'est utile :** la description que le restaurant écrit sur Google = son propre vocabulaire. C'est exactement ce dont le langage miroir a besoin.

**C'est optionnel dans notre pipeline.** Sans clé, Claude analyse quand même. Avec clé, l'email est mieux personnalisé.

**Coût :** ~$0.003 par restaurant (3 cents pour 1 000 restaurants). Minimum $10 pour créer un compte.

### 5. stop_reason (v1 uniquement, pour référence)

Dans la v1 (boucle agentique), Claude répondait avec un `stop_reason` :
- `"end_turn"` → fini, réponse finale
- `"tool_use"` → veut appeler un outil
- `"pause_turn"` → outil serveur en cours (web_search), renvoyer le message

**Dans v2, ce concept n'est pas utilisé.** Un seul appel → une seule réponse → stop_reason est toujours `"end_turn"`.

---

## Comment adapter le système

### Changer les critères de scoring

Dans `pipeline.py`, modifier le `SYSTEM_PROMPT` section "CRITERES DE SCORING" :
```python
CRITERES DE SCORING (0-100) :
+25 pts : Utilise Lightspeed, Zelty ou concurrent direct
+20 pts : Fort volume commandes
# ← ajoute/modifie ici
```
**Note :** Anne-Sophie ou ton équipe ne peut pas les voir directement dans le code. Pour les rendre visibles/modifiables sans coder, il faudrait les stocker dans une page Notion "Configuration" — le script la lirait au démarrage. C'est faisable (~1h de travail).

### Ajouter un restaurant au CRM de démo

Dans `agent.py`, ajouter un bloc dans `RESTAURANTS_DB` :
```python
"nom_cle": {
    "name": "Nom du restaurant",
    "current_system": "Lightspeed",
    "pain_points": ["pain 1", "pain 2"],
    ...
}
```

### Brancher un vrai CRM (HubSpot, Notion, Airtable)

Dans `execute_tool()` (agent.py), remplacer le dict local par un appel API :
```python
def execute_tool("lookup_restaurant"):
    # AVANT (démo) :
    return RESTAURANTS_DB["otacos_nation"]

    # APRÈS (production) :
    return requests.get(
        "https://api.hubspot.com/crm/v3/contacts/...",
        headers={"Authorization": f"Bearer {HUBSPOT_KEY}"}
    ).json()
```
**Le reste du code ne change pas.** Le CRM fournit sa clé API — c'est leur responsabilité.

### Activer Outscraper

```bash
# Windows PowerShell
$env:OUTSCRAPER_API_KEY = "ta-cle-outscraper"

# Linux/Mac
export OUTSCRAPER_API_KEY="ta-cle-outscraper"
```
Minimum $10 pour créer un compte sur outscraper.com. Pour la démo Anne-Sophie, **pas nécessaire** — Claude seul suffit.

---

## Comment expliquer en entretien

### Si on te demande "comment ça marche ?"

> "On a trois étapes. D'abord on enrichit le prospect via Outscraper — on récupère ses données Google Maps : note, description, avis. Ensuite Claude Opus analyse ces données, score le prospect sur nos critères et rédige un email personnalisé en réutilisant les mots que le restaurant utilise lui-même pour se décrire. Enfin le résultat s'écrit automatiquement dans Notion et Gmail. Un seul appel API, moins de 30 secondes."

### Si on te demande "comment l'email est personnalisé ?"

> "Le système détecte dans la description Google Maps du restaurant 2 ou 3 expressions qu'ils utilisent eux-mêmes — 'cuisine du marché', 'produits locaux', peu importe. Ces expressions sont réinjectées dans l'email. Le prospect voit ses propres mots, pas un template. C'est ce qu'on appelle le langage miroir."

### Si on te demande "tu peux l'adapter pour notre cas ?"

> "Oui. Le comportement vient à 80% du system prompt — si vous voulez scorer différemment ou cibler d'autres signaux, on change le prompt. Pour brancher votre CRM, on remplace une fonction de 5 lignes. Pour la source de données, on peut utiliser Outscraper, votre propre base, ou un autre enrichisseur. L'architecture est modulaire."

### Si on te demande "combien ça coûte ?"

> "Environ 0,05€ par prospect analysé avec Claude Opus. Outscraper en option, ~0,003€ par restaurant. Pour 100 prospects par semaine, c'est moins de 20€/mois. Versus Apollo à 99$/mois, Clay à 149$/mois — et en plus c'est paramétré exactement sur vos critères métier."

### Si on te demande "c'est quoi la différence avec ChatGPT ?"

> "ChatGPT génère du texte dans une conversation. Ici, le système récupère des vraies données Google Maps, les analyse, écrit dans Notion, crée des brouillons Gmail — tout ça automatiquement. Ce n'est pas une conversation, c'est une pipeline qui agit sur de vrais outils."

---

## Commandes à retenir

```bash
# Analyser un restaurant (Outscraper optionnel)
python pipeline.py "Nom" "Ville"

# Lancer la démo complète (2 restaurants en séquence)
python demo_live.py

# Tester l'agent de réponse emails
python agent.py
```

**Variables d'environnement :**
```bash
# Windows PowerShell
$env:ANTHROPIC_API_KEY  = "sk-ant-..."       # obligatoire
$env:OUTSCRAPER_API_KEY = "ta-cle-..."       # optionnel

# Linux/Mac
export ANTHROPIC_API_KEY="sk-ant-..."
export OUTSCRAPER_API_KEY="ta-cle-..."
```

---

## Glossaire rapide

| Terme | Définition simple |
|-------|-------------------|
| Agent | Programme qui prend des décisions et fait des actions, pas seulement générer du texte |
| Tool use | Mécanisme qui permet à Claude d'appeler des fonctions Python (utilisé dans agent.py) |
| Boucle agentique | La boucle while True de la v1 — remplacée par un appel unique dans v2 |
| System prompt | Les instructions permanentes données à Claude (son "profil" et ses règles) |
| stop_reason | Pourquoi Claude a arrêté : "end_turn" (fini), "tool_use" (veut un outil) — v1 surtout |
| MCP | Model Context Protocol — façon standard de connecter Notion/Gmail à Claude Code |
| Outscraper | Service de scraping Google Maps (note, avis, description) — optionnel dans la pipeline |
| Langage miroir | Technique : réutiliser les mots du prospect dans l'email pour qu'il sente qu'on l'a vraiment regardé |
| vocabulaire_prospect | Champ JSON contenant les 2-3 expressions détectées chez ce restaurant |
| output.json | Fichier de sortie structuré produit par pipeline.py — lu ensuite par Claude Code pour Notion/Gmail |
| LLM | Large Language Model — le modèle d'IA (ici Claude Opus 4.6) |
