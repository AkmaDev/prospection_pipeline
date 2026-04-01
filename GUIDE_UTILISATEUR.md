# Guide utilisateur — Pipeline de prospection Innovorder

**Pour qui** : commercial ou chef de projet, sans connaissance technique requise.
**Objectif** : trouver des restaurants à contacter, gérer la séquence emails, suivre les leads.

---

## 1. Démarrage rapide

### Ouvrir l'interface (à faire une fois par jour)

Ouvre un terminal (touche `Windows` → taper `cmd` → Entrée), puis colle ces deux lignes :

```
cd C:\Users\manew\Documents\MesProjets\ANTIGRAVITY\monmaster\agent_innovorder
streamlit run streamlit_app.py
```

Une page s'ouvre automatiquement dans ton navigateur à l'adresse `http://localhost:8501`.

> Si la page ne s'ouvre pas seule, tape `http://localhost:8501` dans Chrome ou Edge.

---

## 2. Chaque matin — L'email automatique

À **6h00 chaque matin**, le système tourne tout seul et t'envoie un email à `manews193@gmail.com` avec :

- **Top 5 leads à contacter aujourd'hui** (classés par urgence)
- **Alertes IRP** : leads en danger (signal BODACC > 60 jours sans contact)

Tu n'as rien à faire pour recevoir cet email — il arrive automatiquement.

---

## 3. L'interface Streamlit — Les 5 onglets

### 📅 Aujourd'hui *(commence ici chaque matin)*
Vue d'ensemble opérationnelle : Top 5 leads ITO + alertes IRP + segmentation.
Pour chaque lead du Top 5 : l'email J+0 est disponible en un clic.

### 📡 Signaux BODACC
Détecte les nouveaux restaurants qui viennent d'ouvrir ou de changer de gérant.
Choisir une ville, une fenêtre de jours, et cliquer "Détecter les signaux".

### 🔍 Discovery Google Maps
Recherche des restaurants par type (ex: "fast food", "brasserie") dans une ville.
Nécessite une clé Outscraper (optionnel — fonctionne sans via scraping gratuit).

### 🏪 Restaurant unique
Analyse un restaurant spécifique dont tu connais déjà le nom.
Utile pour préparer un appel ou un rendez-vous spontané.

### 📊 Tous les résultats
Liste de tous les prospects déjà analysés, avec tableau récapitulatif.
**C'est ici que tu mets à jour les statuts et assigne les leads.**

---

## 4. Mettre à jour un lead

Dans l'onglet **📊 Tous les résultats** :

1. Sélectionne le restaurant dans le menu déroulant
2. Fais défiler jusqu'en bas de la fiche
3. Tu vois deux champs :
   - **Statut séquence** : sélectionne l'étape actuelle (ex: `J0_envoye` après avoir envoyé le premier email)
   - **Assigné à** : tape le prénom du commercial responsable (ex: `Paul`)
4. Clique **💾 Sauver** → le statut est mis à jour et synchronisé avec Notion

### Les statuts possibles

| Statut | Signification |
|--------|---------------|
| `J0_a_envoyer` | Premier email prêt, pas encore envoyé |
| `J0_envoye` | Premier email envoyé |
| `J3_a_envoyer` | Relance J+3 à envoyer |
| `J3_envoye` | Relance J+3 envoyée |
| `J7_a_envoyer` | Relance J+7 à envoyer |
| `J7_envoye` | Relance J+7 envoyée |
| `J14_a_envoyer` | Demande d'appel à envoyer |
| `J14_envoye` | Demande d'appel envoyée |
| `J30_a_envoyer` | Email de réactivation à envoyer |
| `reponse_recue` | Le prospect a répondu — lead sorti de la séquence |

---

## 5. Ce qui tourne automatiquement

Le **Planificateur de tâches Windows** (`InnovorderDailyPipeline`) fait ceci chaque matin à 6h :

1. Interroge BODACC pour les nouvelles immatriculations de restaurants
2. Analyse chaque prospect avec Claude (score + 5 emails personnalisés)
3. Sauvegarde les résultats dans `outputs/`
4. Met à jour Notion
5. T'envoie l'email digest

> Pour lancer manuellement le pipeline sans attendre 6h :
> Ouvre cmd dans le dossier `agent_innovorder` et tape : `python daily_run.py`

---

## 6. Activer l'automatisation (à faire une seule fois)

Dans le dossier `agent_innovorder`, faire un **clic droit** sur `setup_cron_windows.bat` → **"Exécuter en tant qu'administrateur"**.

Une fenêtre noire s'ouvre, attend qu'elle affiche `[OK] Tâche planifiée créée avec succès !`, puis ferme.

Pour vérifier que ça fonctionne :
- Ouvre "Planificateur de tâches" Windows (chercher dans le menu Démarrer)
- Tu dois voir une tâche nommée `InnovorderDailyPipeline`

---

## 7. FAQ

**Q : Le pipeline n'a rien trouvé ce matin.**
R : Normal si aucune nouvelle immatriculation BODACC dans ta ville ce jour-là. Essaie d'élargir la fenêtre à 3 jours : `python daily_run.py --days 3`

**Q : Je n'ai pas reçu l'email digest.**
R : Vérifie les spams. Si absent : ouvre cmd dans `agent_innovorder` et tape `python daily_run.py` — les erreurs éventuelles s'afficheront.

**Q : Comment changer la ville analysée automatiquement ?**
R : Ouvre `daily_run.py`, ligne 211 — change `"Paris"` par ta ville.

**Q : Un prospect a dit non. Comment l'archiver ?**
R : Mets son statut à `reponse_recue` — il disparaîtra du Top 5 ITO.

**Q : Peut-on utiliser le pipeline pour un autre secteur que la restauration ?**
R : Oui. Il suffit de changer le `SYSTEM_PROMPT` dans `pipeline.py` (ligne 55). Le reste fonctionne identiquement pour n'importe quel secteur couvert par BODACC (tous les secteurs français).
