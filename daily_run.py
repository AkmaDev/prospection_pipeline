#!/usr/bin/env python3
"""
daily_run.py — Script de lancement automatique quotidien
Lance le pipeline BODACC chaque matin → Notion + Gmail mis à jour automatiquement.

Ce script est appelé par le Planificateur de tâches Windows à 6h00.
Il détecte les nouvelles immatriculations BODACC de la nuit,
analyse chaque prospect, et sauvegarde les résultats.

Usage manuel :
    python daily_run.py
    python daily_run.py --days 3 --limit 20

Planificateur Windows (configuré par setup_cron_windows.bat) :
    Chaque jour à 6h00 → ce script tourne en arrière-plan
"""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import os
import json
import subprocess
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)


def log(msg: str, logfile):
    """Écrit dans le terminal ET dans le fichier de log."""
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line)
    logfile.write(line + "\n")
    logfile.flush()


def _compute_ito_top5(outputs_dir: Path, logfile, log_fn):
    """
    ITO simplifié (Indice de Timing Optimal) — classement des leads à contacter aujourd'hui.

    Formule approximative (faute de données comportementales complètes) :
      ITO = score_pipeline * 0.40          (qualité signal BODACC = activité récente)
          + recence_signal * 0.25          (BODACC < 30j = 25 pts, 30-60j = 15 pts, >60j = 5 pts)
          + urgence_sequence * 0.20        (J0_a_envoyer = 20, J7 dû = 15, J14 dû = 10)
          + penalite_anciennete * 0.15     (0 si créé < 7j, -5 si > 14j, -10 si > 30j)

    Seuls les leads dont sequence_status != "reponse_recue" et score >= 40 sont inclus.
    Source : tous les fichiers outputs/*.json (sauf summary_*).
    """
    from datetime import date
    today = date.today()

    candidates = []
    for fpath in outputs_dir.glob("*.json"):
        if fpath.name.startswith("summary_"):
            continue
        try:
            with open(fpath, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue

        score = d.get("score", 0)
        seq_status = d.get("sequence_status", "")
        if score < 40 or seq_status == "reponse_recue":
            continue

        # Composante 1 : score pipeline (activité récente / qualité signal)
        c1 = score * 0.40

        # Composante 2 : récence signal BODACC
        signal_date_str = d.get("signal", {}).get("signal_date", "") or d.get("timestamp", "")[:10]
        try:
            signal_date = date.fromisoformat(signal_date_str[:10])
            days_since = (today - signal_date).days
            if days_since <= 30:
                c2 = 25
            elif days_since <= 60:
                c2 = 15
            else:
                c2 = 5
        except Exception:
            c2 = 10

        # Composante 3 : urgence séquence (quel email est dû ?)
        seq_map = {
            "J0_a_envoyer": 20,
            "J0_envoye": 0,
            "J7_a_envoyer": 15,
            "J7_envoye": 0,
            "J14_a_envoyer": 10,
        }
        c3 = seq_map.get(seq_status, 5)

        # Check si une date de relance est dépassée (le lead est "dû")
        for date_field in ("date_relance_j3", "date_relance_j7", "date_appel_j14", "date_reactivation_j30"):
            due_str = d.get(date_field, "")
            if due_str:
                try:
                    due_date = date.fromisoformat(due_str)
                    if due_date <= today:
                        c3 = max(c3, 18)  # Boost si relance due ou en retard
                        break
                except Exception:
                    pass

        # Composante 4 : pénalité ancienneté
        created_str = d.get("timestamp", "")[:10]
        try:
            created = date.fromisoformat(created_str)
            age = (today - created).days
            if age > 30:
                c4 = -10
            elif age > 14:
                c4 = -5
            else:
                c4 = 0
        except Exception:
            c4 = 0

        ito = c1 + c2 * 0.25 + c3 * 0.20 + c4 * 0.15
        candidates.append({
            "restaurant": d.get("restaurant", fpath.stem),
            "score": score,
            "seq_status": seq_status,
            "ito": round(ito, 1),
            "file": fpath.name,
        })

    if not candidates:
        log_fn("  Aucun lead actif trouvé dans outputs/.", logfile)
        return [], []

    top5 = sorted(candidates, key=lambda x: x["ito"], reverse=True)[:5]
    for i, c in enumerate(top5, 1):
        log_fn(
            f"  {i}. [{c['ito']:5.1f} ITO] {c['score']:3d}/100 — {c['restaurant']}"
            f"  ({c['seq_status']})",
            logfile,
        )

    # ── IRP : Leads à risque de perte (contacter en urgence ou archiver) ──────
    irp_alerts = []
    for c in candidates:
        seq_status = c["seq_status"]
        # IRP élevé : signal BODACC > 60 jours sans contact (a probablement signé concurrent)
        fpath = Path(outputs_dir) / c["file"]
        try:
            with open(fpath, encoding="utf-8") as f:
                d = json.load(f)
            signal_date_str = d.get("signal", {}).get("signal_date", "") or d.get("timestamp", "")[:10]
            signal_date = date.fromisoformat(signal_date_str[:10])
            days_since = (today - signal_date).days
            if days_since > 60 and seq_status not in ("reponse_recue",):
                irp_alerts.append({
                    "restaurant": c["restaurant"],
                    "days": days_since,
                    "seq_status": seq_status,
                    "file": c["file"],
                })
                log_fn(f"  ⚠️ IRP ÉLEVÉ ({days_since}j sans contact) — {c['restaurant']}", logfile)
        except Exception:
            pass

    if irp_alerts:
        log_fn("\n── ALERTES IRP (risque de perte — agir ou archiver) ──", logfile)

    # ── Cadences de nurturing par segment ────────────────────────────────────
    hot    = [c for c in candidates if c["score"] >= 70]
    warm   = [c for c in candidates if 40 <= c["score"] < 70]
    cold   = [c for c in candidates if 10 <= c["score"] < 40]
    dormant = [c for c in candidates if c["score"] < 10]

    log_fn("\n── SEGMENTATION NURTURING ──", logfile)
    log_fn(f"  🔥 Chauds  (>70)  : {len(hot):2d} leads — cadence 2x/semaine", logfile)
    log_fn(f"  🟡 Tièdes (40-70) : {len(warm):2d} leads — cadence 1x/semaine", logfile)
    log_fn(f"  🔵 Froids (10-39) : {len(cold):2d} leads — cadence 1x/mois", logfile)
    log_fn(f"  ⚫ Dormants (<10)  : {len(dormant):2d} leads — cadence 1x/trimestre", logfile)

    return top5, irp_alerts


def send_daily_digest(top5: list, irp_alerts: list, date_str: str):
    """
    Envoie un digest email HTML au commercial avec le Top 5 ITO + alertes IRP.
    Variables .env requises : DIGEST_EMAIL_TO, DIGEST_EMAIL_FROM, DIGEST_EMAIL_PASSWORD.
    Si absentes → skip silencieux.
    """
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    to_addr   = os.environ.get("DIGEST_EMAIL_TO", "")
    from_addr = os.environ.get("DIGEST_EMAIL_FROM", "")
    password  = os.environ.get("DIGEST_EMAIL_PASSWORD", "")

    if not (to_addr and from_addr and password):
        print("  [Digest] DIGEST_EMAIL_TO/FROM/PASSWORD non configurés — skip.")
        return

    # ── Corps HTML ──────────────────────────────────────────────────────────
    rows_top5 = ""
    for i, c in enumerate(top5, 1):
        rows_top5 += (
            f"<tr>"
            f"<td style='padding:6px 12px;'><b>{i}</b></td>"
            f"<td style='padding:6px 12px;'>{c['restaurant']}</td>"
            f"<td style='padding:6px 12px;text-align:center;'><b>{c['ito']}</b></td>"
            f"<td style='padding:6px 12px;text-align:center;'>{c['score']}/100</td>"
            f"<td style='padding:6px 12px;'>{c['seq_status'].replace('_', ' ')}</td>"
            f"</tr>"
        )

    rows_irp = ""
    for a in irp_alerts:
        rows_irp += (
            f"<tr>"
            f"<td style='padding:6px 12px;color:#c0392b;'>⚠️ {a['restaurant']}</td>"
            f"<td style='padding:6px 12px;'>{a['days']} jours sans contact</td>"
            f"<td style='padding:6px 12px;'>{a['seq_status'].replace('_', ' ')}</td>"
            f"</tr>"
        )

    irp_section = ""
    if irp_alerts:
        irp_section = f"""
        <h3 style='color:#c0392b;'>⚠️ Alertes IRP — Leads à risque de perte</h3>
        <table border='1' cellspacing='0' cellpadding='0'
               style='border-collapse:collapse;font-size:14px;'>
          <thead style='background:#fdecea;'>
            <tr>
              <th style='padding:6px 12px;'>Restaurant</th>
              <th style='padding:6px 12px;'>Inactivité</th>
              <th style='padding:6px 12px;'>Statut</th>
            </tr>
          </thead>
          <tbody>{rows_irp}</tbody>
        </table>
        """

    html = f"""
    <html><body style='font-family:sans-serif;color:#222;'>
      <h2 style='color:#e74c3c;'>🔥 Top 5 leads — {date_str}</h2>
      <p>Voici les leads à contacter en priorité aujourd'hui (classement ITO).</p>

      <table border='1' cellspacing='0' cellpadding='0'
             style='border-collapse:collapse;font-size:14px;'>
        <thead style='background:#fdf3e3;'>
          <tr>
            <th style='padding:6px 12px;'>#</th>
            <th style='padding:6px 12px;'>Restaurant</th>
            <th style='padding:6px 12px;'>ITO</th>
            <th style='padding:6px 12px;'>Score</th>
            <th style='padding:6px 12px;'>Statut séquence</th>
          </tr>
        </thead>
        <tbody>{rows_top5}</tbody>
      </table>

      {irp_section}

      <p style='color:#888;font-size:12px;margin-top:24px;'>
        Généré automatiquement par le pipeline IA · {date_str}
      </p>
    </body></html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🔥 Top 5 leads — {date_str}"
    msg["From"]    = from_addr
    msg["To"]      = to_addr
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(from_addr, password)
            server.sendmail(from_addr, to_addr, msg.as_string())
        print(f"  [Digest] Email envoyé à {to_addr} ✅")
    except Exception as e:
        print(f"  [Digest] Erreur envoi email : {e}")


def run_daily(days: int = 1, limit: int = 15):
    """
    Lance le pipeline signaux BODACC et sauvegarde un rapport quotidien.

    days=1 : on récupère les signaux du jour (BODACC mis à jour chaque matin)
    """
    today = datetime.now().strftime("%Y-%m-%d")
    log_path = LOG_DIR / f"run_{today}.log"

    with open(log_path, "w", encoding="utf-8") as logfile:
        log(f"=== LANCEMENT QUOTIDIEN — {today} ===", logfile)
        log(f"Fenêtre : {days} jour(s) | Limite : {limit} prospects", logfile)
        log("", logfile)

        # Vérification clé API
        if not os.environ.get("ANTHROPIC_API_KEY"):
            log("ERREUR : ANTHROPIC_API_KEY manquante. Arrêt.", logfile)
            return

        # Lancement pipeline
        script = Path(__file__).parent / "pipeline.py"
        cmd = [
            sys.executable, str(script),
            "--signals",
            "--city", "Paris",
            "--days", str(days),
            "--limit", str(limit),
            "--sources", "bodacc",
        ]

        log(f"Commande : {' '.join(cmd)}", logfile)
        log("─" * 50, logfile)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(Path(__file__).parent),
            )
            log(result.stdout, logfile)
            if result.stderr:
                log(f"STDERR :\n{result.stderr}", logfile)
            log("─" * 50, logfile)
            log(f"Code de retour : {result.returncode}", logfile)
        except Exception as e:
            log(f"ERREUR subprocess : {e}", logfile)
            return

        # Résumé du dernier summary JSON généré
        outputs_dir = Path(__file__).parent / "outputs"
        summaries = sorted(outputs_dir.glob("summary_*.json"), reverse=True)
        if summaries:
            latest = summaries[0]
            log(f"\nDernier résumé : {latest.name}", logfile)
            try:
                with open(latest, encoding="utf-8") as f:
                    data = json.load(f)
                chauds = [r for r in data if r.get("score", 0) >= 66]
                log(f"Total prospects : {len(data)} | Chauds (>=66) : {len(chauds)}", logfile)
                for r in chauds:
                    log(f"  🔥 {r.get('score', 0):3d}/100 — {r.get('restaurant', '?')}", logfile)
            except Exception as e:
                log(f"Erreur lecture résumé : {e}", logfile)

        # ── ITO simplifié : Top 5 leads à contacter AUJOURD'HUI ──────────────
        log("\n── TOP 5 LEADS À CONTACTER AUJOURD'HUI (ITO) ──", logfile)
        top5, irp_alerts = [], []
        try:
            result = _compute_ito_top5(outputs_dir, logfile, log)
            if result:
                top5, irp_alerts = result
        except Exception as e:
            log(f"Erreur ITO : {e}", logfile)

        log("", logfile)
        log(f"=== FIN — log sauvegardé : {log_path} ===", logfile)

    # ── Digest email vers le commercial ──────────────────────────────────────
    send_daily_digest(top5, irp_alerts, today)

    print(f"\n  Log complet : {log_path}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Lancement quotidien pipeline BODACC")
    parser.add_argument("--days", type=int, default=1, help="Fenêtre BODACC en jours (défaut: 1)")
    parser.add_argument("--limit", type=int, default=15, help="Nombre max de prospects (défaut: 15)")
    args = parser.parse_args()
    run_daily(days=args.days, limit=args.limit)
