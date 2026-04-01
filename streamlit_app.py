#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
streamlit_app.py — Pipeline de Prospection IA
Lancement : streamlit run streamlit_app.py
"""

import sys
import json
import os
import smtplib
from pathlib import Path
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

COMPANY_NAME   = os.environ.get("COMPANY_NAME", "Pipeline IA")
DEMO_MODE      = os.environ.get("DEMO_MODE", "false").lower() == "true"
# DEMO_LIMIT : nombre max d'analyses gratuites par session.
# Mettre 0 pour désactiver la limite (usage local / tests).
DEMO_LIMIT     = int(os.environ.get("DEMO_LIMIT", "3" if DEMO_MODE else "0"))

sys.path.insert(0, str(Path(__file__).parent))
from pipeline import analyze_restaurant, enrich_gerant, save_result

try:
    from pipeline import discover_restaurants, fetch_single_restaurant
    OUTSCRAPER_AVAILABLE = bool(os.environ.get("OUTSCRAPER_API_KEY"))
except ImportError:
    OUTSCRAPER_AVAILABLE = False

import streamlit as st

# ─── ITO computation ─────────────────────────────────────────────────────────

def compute_ito_data(outputs_dir: Path):
    from datetime import date
    today = date.today()
    candidates = []

    for fpath in outputs_dir.glob("*.json"):
        if fpath.name.startswith("summary_"):
            continue
        try:
            with open(fpath, encoding="utf-8") as f:
                d = json.load(f)
            if not isinstance(d, dict):
                continue

            score = d.get("score", 0)
            seq_status = d.get("sequence_status", "")
            if score < 40 or seq_status == "reponse_recue":
                continue

            c1 = score * 0.40
            sig = d.get("signal", {})
            signal_date_str = (sig.get("signal_date", "") if isinstance(sig, dict) else "") \
                              or d.get("timestamp", "")[:10]
            try:
                signal_date = date.fromisoformat(signal_date_str[:10])
                days_since = (today - signal_date).days
                c2 = 25 if days_since <= 30 else (15 if days_since <= 60 else 5)
            except Exception:
                days_since = 0
                c2 = 10

            seq_map = {"J0_a_envoyer": 20, "J0_envoye": 0, "J7_a_envoyer": 15,
                       "J7_envoye": 0, "J14_a_envoyer": 10}
            c3 = seq_map.get(seq_status, 5)
            for date_field in ("date_relance_j3", "date_relance_j7", "date_appel_j14", "date_reactivation_j30"):
                due_str = d.get(date_field, "")
                if due_str:
                    try:
                        if date.fromisoformat(due_str) <= today:
                            c3 = max(c3, 18)
                            break
                    except Exception:
                        pass

            created_str = d.get("timestamp", "")[:10]
            try:
                age = (today - date.fromisoformat(created_str)).days
                c4 = -10 if age > 30 else (-5 if age > 14 else 0)
            except Exception:
                c4 = 0

            ito = c1 + c2 * 0.25 + c3 * 0.20 + c4 * 0.15
            candidates.append({
                "restaurant": d.get("restaurant", fpath.stem),
                "score": score,
                "seq_status": seq_status,
                "ito": round(ito, 1),
                "file": fpath.name,
                "fpath": fpath,
                "data": d,
                "days_since": days_since,
            })
        except Exception:
            continue

    if not candidates:
        return [], [], {"hot": 0, "warm": 0, "cold": 0, "dormant": 0}

    top5 = sorted(candidates, key=lambda x: x["ito"], reverse=True)[:5]
    irp_alerts = [c for c in candidates if c["days_since"] > 60]
    segments = {
        "hot":     len([c for c in candidates if c["score"] >= 70]),
        "warm":    len([c for c in candidates if 40 <= c["score"] < 70]),
        "cold":    len([c for c in candidates if 10 <= c["score"] < 40]),
        "dormant": len([c for c in candidates if c["score"] < 10]),
    }
    return top5, irp_alerts, segments


# ─── Email sending ───────────────────────────────────────────────────────────

def send_j0_email(to_addr: str, subject: str, body: str) -> tuple[bool, str]:
    from_addr = os.environ.get("DIGEST_EMAIL_FROM", "")
    password  = os.environ.get("DIGEST_EMAIL_PASSWORD", "")
    if not (from_addr and password):
        return False, "DIGEST_EMAIL_FROM ou DIGEST_EMAIL_PASSWORD manquant dans .env"
    if not to_addr or "@" not in to_addr:
        return False, "Adresse email invalide"
    body_html = body.replace("\n", "<br>")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = from_addr
    msg["To"]      = to_addr
    msg.attach(MIMEText(body, "plain", "utf-8"))
    msg.attach(MIMEText(f"<html><body style='font-family:sans-serif;line-height:1.6'>{body_html}</body></html>", "html", "utf-8"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(from_addr, password)
            server.sendmail(from_addr, to_addr, msg.as_string())
        return True, f"Email envoyé à {to_addr}"
    except Exception as e:
        return False, f"Erreur SMTP : {e}"


# ─── Constants ───────────────────────────────────────────────────────────────

SEQUENCE_OPTIONS = [
    "hors_sequence",
    "J0_a_envoyer", "J0_envoye",
    "J3_a_envoyer", "J3_envoye",
    "J7_a_envoyer", "J7_envoye",
    "J14_a_envoyer", "J14_envoye",
    "J30_a_envoyer", "reponse_recue",
]

SEQ_LABELS = {
    "hors_sequence": ("Lead froid",    "#94A3B8"),
    "J0_a_envoyer":  ("À envoyer",    "#E67E22"),
    "J0_envoye":     ("J0 envoyé",    "#27AE60"),
    "J3_a_envoyer":  ("Relance J3 ↗", "#E67E22"),
    "J3_envoye":     ("J3 envoyé",    "#27AE60"),
    "J7_a_envoyer":  ("Relance J7 ↗", "#E74C3C"),
    "J7_envoye":     ("J7 envoyé",    "#27AE60"),
    "J14_a_envoyer": ("Appel J14 ↗",  "#8E44AD"),
    "J14_envoye":    ("J14 envoyé",   "#27AE60"),
    "J30_a_envoyer": ("Réactivation", "#2980B9"),
    "reponse_recue": ("Réponse reçue","#1ABC9C"),
}

OUTPUT_DIR = Path(__file__).parent / "outputs"


# ─── Demo state ──────────────────────────────────────────────────────────────

if "demo_count" not in st.session_state:
    st.session_state.demo_count = 0


def _show_upsell():
    st.markdown("""
<div style="background:#F0F9FF;border:1px solid #BAE6FD;border-radius:12px;
padding:28px 32px;margin-top:16px">
<h3 style="margin:0 0 8px 0;color:#1E3A5F">Vous avez utilisé vos 3 analyses gratuites</h3>
<p style="color:#475569;margin-bottom:16px">
Cette démo utilise Claude Haiku (résultats réels, modèle allégé).
La version complète ajoute :
</p>
<ul style="color:#334155;line-height:2">
<li>Claude Opus — emails de qualité supérieure, score plus précis</li>
<li>Envoi automatique de la séquence J0 → J30 via Gmail</li>
<li>Scan BODACC automatique chaque matin à 6h</li>
<li>Toutes les villes françaises, aucune limite d'analyses</li>
<li>Synchronisation CRM Notion (Kanban auto)</li>
<li>Digest email quotidien Top 5 leads</li>
</ul>
<a href="https://linkedin.com/in/manasse-akpovi" target="_blank"
style="display:inline-block;margin-top:16px;background:#1E3A5F;color:white;
padding:10px 24px;border-radius:6px;text-decoration:none;font-weight:600">
Contacter pour un accès complet →
</a>
</div>
""", unsafe_allow_html=True)


# ─── Config ──────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Pipeline Prospection IA",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
/* Global */
html, body, [class*="css"] { font-family: 'Inter', 'Segoe UI', sans-serif; }

/* Supprime le padding Streamlit en haut de page */
[data-testid="block-container"] { padding-top: 1rem !important; }

/* Hide Streamlit branding */
#MainMenu { visibility: hidden; }
footer { visibility: hidden; }
header { visibility: hidden; }

/* Tabs */
.stTabs [data-baseweb="tab-list"] {
    gap: 0px;
    border-bottom: 2px solid #E2E8F0;
    background: transparent;
}
.stTabs [data-baseweb="tab"] {
    padding: 12px 24px;
    font-size: 14px;
    font-weight: 500;
    color: #64748B;
    border-bottom: 2px solid transparent;
    margin-bottom: -2px;
    background: transparent;
}
.stTabs [aria-selected="true"] {
    color: #1E3A5F !important;
    border-bottom: 2px solid #1E3A5F !important;
    font-weight: 600;
}

/* Cards */
[data-testid="stVerticalBlock"] > [data-testid="stVerticalBlock"] {
    border-radius: 8px;
}

/* Buttons */
.stButton > button[kind="primary"] {
    background-color: #1E3A5F;
    border: none;
    font-weight: 600;
    padding: 10px 24px;
    border-radius: 6px;
}
.stButton > button[kind="primary"]:hover {
    background-color: #2563EB;
}

/* Metrics */
[data-testid="stMetricValue"] {
    font-size: 28px;
    font-weight: 700;
    color: #0F1723;
}

/* Sidebar */
[data-testid="stSidebar"] {
    background-color: #F8FAFC;
    border-right: 1px solid #E2E8F0;
}

/* Dataframe */
[data-testid="stDataFrame"] { border-radius: 8px; overflow: hidden; }
</style>
""", unsafe_allow_html=True)


# ─── Sidebar ─────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown(f"### {COMPANY_NAME}")
    st.caption(datetime.now().strftime('%A %d %B %Y'))
    st.divider()

    api_ok    = bool(os.environ.get("ANTHROPIC_API_KEY"))
    exa_ok    = bool(os.environ.get("EXA_API_KEY"))
    notion_ok = bool(os.environ.get("NOTION_API_KEY"))
    gmail_ok  = bool(os.environ.get("DIGEST_EMAIL_FROM"))

    def _svc(label, ok, required=True):
        dot   = "🟢" if ok else ("🔴" if required else "⚪")
        state = "OK" if ok else ("requis" if required else "optionnel")
        st.markdown(
            f"<div style='font-size:13px;padding:2px 0'>{dot} <b>{label}</b>"
            f"<span style='color:#94A3B8;float:right'>{state}</span></div>",
            unsafe_allow_html=True,
        )

    _svc("Claude API", api_ok, required=True)
    _svc("Exa / LinkedIn", exa_ok, required=False)
    _svc("Notion CRM", notion_ok, required=False)
    _svc("Gmail SMTP", gmail_ok, required=False)

    st.divider()

    n_total = n_to_contact = 0
    if OUTPUT_DIR.exists():
        json_files = [f for f in OUTPUT_DIR.glob("*.json") if not f.name.startswith("summary_")]
        n_total = len(json_files)
        for f in json_files:
            try:
                d = json.load(open(f, encoding="utf-8"))
                if d.get("sequence_status") == "J0_a_envoyer":
                    n_to_contact += 1
            except Exception:
                pass

    col_a, col_b = st.columns(2)
    col_a.metric("Leads", n_total)
    col_b.metric("À envoyer", n_to_contact)

    st.divider()
    st.markdown(
        "<p style='font-size:12px;color:#64748B;line-height:1.6'>"
        "① Détectez les signaux BODACC<br>"
        "② Envoyez le J0 aux leads chauds<br>"
        "③ Suivez jusqu'à la réponse"
        "</p>",
        unsafe_allow_html=True,
    )


# ─── Page header ─────────────────────────────────────────────────────────────

st.markdown("""
<div style="
    background: linear-gradient(135deg, #1E3A5F 0%, #2563EB 100%);
    border-radius: 12px;
    padding: 28px 32px;
    margin-bottom: 24px;
    color: white;
">
    <h1 style="margin:0;font-size:24px;font-weight:700;color:white">
        Pipeline de Prospection IA
    </h1>
    <p style="margin:8px 0 0 0;font-size:14px;opacity:0.85;color:white">
        ① Détectez les nouveaux restaurants via BODACC &nbsp;·&nbsp;
        ② Envoyez l'email J0 aux leads prioritaires &nbsp;·&nbsp;
        ③ Suivez la séquence jusqu'à la réponse
    </p>
</div>
""", unsafe_allow_html=True)

if not api_ok:
    st.error("ANTHROPIC_API_KEY manquante — vérifiez votre fichier `.env`.")


# ─── Card renderer ───────────────────────────────────────────────────────────

def _render_card(data: dict, key_prefix: str = "", file_path: Path = None):
    """Fiche complète d'un prospect."""
    score = data.get("score", 0)
    name  = data.get("restaurant", "?")
    seq   = data.get("sequence_status", "J0_a_envoyer")
    s_label, s_color = SEQ_LABELS.get(seq, (seq, "#64748B"))
    score_color = "#22C55E" if score >= 66 else ("#F59E0B" if score >= 40 else "#EF4444")

    with st.container(border=True):
        c_name, c_score = st.columns([5, 1])
        with c_name:
            st.markdown(f"### {name}")
            st.caption(data.get("adresse", ""))
            if data.get("gerant_nom") and data.get("gerant_nom") != "non identifié":
                g = f"👤 **{data['gerant_nom']}**"
                if data.get("gerant_titre"):
                    g += f" — {data['gerant_titre']}"
                if data.get("gerant_linkedin"):
                    g += f"  [LinkedIn ↗]({data['gerant_linkedin']})"
                st.markdown(g)
                if data.get("gerant_confidence") == "low":
                    st.markdown(
                        "<span style='background:#FEF3C7;color:#92400E;padding:2px 8px;"
                        "border-radius:4px;font-size:11px;font-weight:600'>"
                        "⚠ Profil non vérifié — confirmer avant d'envoyer</span>",
                        unsafe_allow_html=True,
                    )
            st.markdown(
                f"<span style='background:{s_color}20;color:{s_color};padding:2px 10px;"
                f"border-radius:4px;font-size:12px;font-weight:600'>{s_label}</span>",
                unsafe_allow_html=True,
            )
        with c_score:
            st.markdown(
                f"<div style='text-align:center'>"
                f"<div style='font-size:28px;font-weight:700;color:{score_color}'>{score}</div>"
                f"<div style='font-size:11px;color:#64748B'>/100</div>"
                f"<div style='font-size:11px;color:#64748B;margin-top:2px'>{data.get('statut','').replace('_',' ')}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

        st.caption(f"*{data.get('score_justification', '')}*")

        # ── Bannière lead froid ──
        _is_cold = score < 40 or seq == "hors_sequence"
        if _is_cold:
            st.markdown(
                "<div style='background:#F1F5F9;border-left:4px solid #94A3B8;"
                "padding:10px 16px;border-radius:4px;margin:8px 0'>"
                "<b style='color:#475569'>Lead froid — aucune action recommandée</b><br>"
                "<span style='font-size:13px;color:#64748B'>Score insuffisant pour une "
                "prise de contact. Attendez un signal BODACC ou revenez dans 30 jours."
                "</span></div>",
                unsafe_allow_html=True,
            )

        ci1, ci2, ci3 = st.columns(3)
        ci1.markdown(f"**Système actuel**  \n{data.get('systeme_actuel', '—')}")
        ci2.markdown(f"**CA estimé**  \n{data.get('ca_estime', '—')}")
        ci3.markdown(f"**Type**  \n{data.get('type_etablissement', '—')}")

        pain_points = data.get("pain_points", [])
        if pain_points:
            st.markdown("**Points de douleur détectés**")
            for pp in pain_points:
                st.markdown(f"— {pp}")

        if _is_cold:
            # Pas de séquence emails pour les leads froids
            st.caption("Séquence email non générée — score < 40.")
        else:
            st.markdown("---")
            st.markdown("**Séquence emails — 5 touches**")
            email_steps = [
                ("J+0 — Premier contact",
                 data.get("email_objet"), data.get("email_corps")),
                (f"J+3 — Preuve sociale  ({data.get('date_relance_j3','')})",
                 data.get("email_relance_j3_objet"), data.get("email_relance_j3")),
                (f"J+7 — Relance + ADERA  ({data.get('date_relance_j7','')})",
                 data.get("email_relance_j7_objet"), data.get("email_relance_j7")),
                (f"J+14 — Demande d'appel  ({data.get('date_appel_j14','')})",
                 data.get("email_appel_j14_objet"), data.get("email_appel_j14")),
                (f"J+30 — Réactivation  ({data.get('date_reactivation_j30','')})",
                 data.get("email_reactivation_j30_objet"), data.get("email_reactivation_j30")),
            ]
            for label, objet, corps in email_steps:
                if corps:
                    with st.expander(label + (f" — *{objet}*" if objet else "")):
                        st.code(corps, language=None)

        if file_path and data.get("email_corps") and seq == "J0_a_envoyer":
            st.markdown("---")
            st.markdown(
                "<div style='background:#EFF6FF;border:1px solid #BFDBFE;border-radius:8px;"
                "padding:12px 16px;margin-bottom:8px'>"
                "<b style='color:#1E3A5F'>Envoyer le premier email (J0)</b></div>",
                unsafe_allow_html=True,
            )
            c_to, c_btn = st.columns([3, 1])
            with c_to:
                to = st.text_input(
                    "Email destinataire",
                    value=data.get("email", "") or data.get("email_contact_probable", ""),
                    placeholder="contact@restaurant.fr",
                    key=f"to_{key_prefix}",
                )
            with c_btn:
                st.write("")
                st.write("")
                if st.button("Envoyer J0", key=f"send_{key_prefix}", type="primary"):
                    with st.spinner("Envoi en cours..."):
                        ok, msg = send_j0_email(to, data.get("email_objet",""), data.get("email_corps",""))
                    if ok:
                        data["sequence_status"] = "J0_envoye"
                        data["email_j0_sent_to"] = to
                        data["email_j0_sent_at"] = datetime.now().isoformat()[:19]
                        json.dump(data, open(file_path, "w", encoding="utf-8"),
                                  ensure_ascii=False, indent=2)
                        st.success(f"Email envoyé à {to}")
                        st.rerun()
                    else:
                        st.error(msg)
        elif data.get("email_j0_sent_to"):
            st.markdown(
                f"<div style='background:#F0FDF4;border:1px solid #BBF7D0;border-radius:6px;"
                f"padding:8px 14px;font-size:13px;color:#166534'>"
                f"✓ J0 envoyé à <b>{data['email_j0_sent_to']}</b>"
                + (f" le {data.get('email_j0_sent_at','')[:10]}" if data.get("email_j0_sent_at") else "")
                + "</div>",
                unsafe_allow_html=True,
            )

        if data.get("notes"):
            with st.expander("Brief commercial — à lire avant l'appel"):
                st.info(data["notes"])
        vocab = data.get("vocabulaire_prospect", [])
        if vocab:
            st.caption(f"Vocabulaire miroir : {' · '.join(vocab)}")

        if file_path:
            st.markdown("---")
            cs, ca, cb = st.columns([2, 2, 1])
            current_idx = SEQUENCE_OPTIONS.index(seq) if seq in SEQUENCE_OPTIONS else 0
            with cs:
                new_status = st.selectbox("Statut séquence", SEQUENCE_OPTIONS,
                                           index=current_idx, key=f"status_{key_prefix}")
            with ca:
                new_assigned = st.text_input("Assigné à", value=data.get("assigned_to", ""),
                                              placeholder="Prénom du commercial",
                                              key=f"assign_{key_prefix}")
            with cb:
                st.write("")
                st.write("")
                if st.button("Sauver", key=f"save_{key_prefix}"):
                    data["sequence_status"] = new_status
                    data["assigned_to"] = new_assigned
                    json.dump(data, open(file_path, "w", encoding="utf-8"),
                              ensure_ascii=False, indent=2)
                    if os.environ.get("NOTION_API_KEY"):
                        try:
                            from notion_kanban import push_to_notion
                            push_to_notion([data])
                        except Exception:
                            pass
                    st.toast("Sauvegardé ✓")
                    st.rerun()

        st.download_button(
            "Télécharger JSON",
            data=json.dumps(data, ensure_ascii=False, indent=2),
            file_name=f"{name[:30].lower().replace(' ','_').replace('/','_')}.json",
            mime="application/json",
            key=f"dl_{key_prefix}_{name[:15]}",
        )


# ─── Tabs ────────────────────────────────────────────────────────────────────

tab_detect, tab_act, tab_track, tab_manual, tab_context = st.tabs([
    "① Détecter les signaux",
    "② Agir aujourd'hui",
    "③ Suivre les leads",
    "Analyse manuelle",
    "ℹ Contexte",
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — DÉTECTER
# ══════════════════════════════════════════════════════════════════════════════

with tab_detect:
    st.markdown("### ① Détecter les signaux BODACC")
    st.caption(
        "Interroge le registre officiel français — nouvelles ouvertures, cessions, recrutements actifs. "
        "Fenêtre optimale : 7 à 30 jours après publication."
    )
    st.divider()

    col1, col2, col3 = st.columns(3)
    with col1:
        bodacc_city = st.text_input("Ville cible", value="Paris", key="b_city",
                                     help="Ex : Lyon, Bordeaux, Marseille")
    with col2:
        bodacc_days = st.slider("Fenêtre (jours)", 1, 30, 7, key="b_days",
                                 help="Nombre de jours en arrière à analyser")
    with col3:
        bodacc_limit = st.slider("Nombre de leads", 1, 30, 10, key="b_limit",
                                  help="Chaque analyse consomme ~1 appel API Claude")

    st.markdown("")
    run_btn = st.button("Lancer la détection BODACC", type="primary", key="b_run",
                         disabled=not api_ok)

    if run_btn:
        try:
            from pipeline_signals import get_signals
        except ImportError:
            st.error("Fichier `pipeline_signals.py` introuvable dans le dossier.")
            st.stop()

        with st.spinner(f"Interrogation BODACC — {bodacc_city}, {bodacc_days} derniers jours..."):
            signals = get_signals(city=bodacc_city, days=bodacc_days,
                                  limit=bodacc_limit, sources=["bodacc"])

        if not signals:
            st.warning("Aucun signal trouvé. Essayez d'élargir la fenêtre ou de changer de ville.")
        else:
            if DEMO_MODE and st.session_state.demo_count >= DEMO_LIMIT:
                _show_upsell()
                st.stop()

            st.success(f"**{len(signals)} signal(s) détecté(s).** Lancement de l'analyse Claude...")
            if DEMO_MODE:
                st.info(f"Mode démo — modèle Claude Haiku · {DEMO_LIMIT - st.session_state.demo_count} analyse(s) restante(s)")
            OUTPUT_DIR.mkdir(exist_ok=True)

            results = []
            progress = st.progress(0)
            n = len(signals)
            for i, item in enumerate(signals):
                name = item.get("name", "?")
                restaurant_data = {
                    "name": name,
                    "full_address": item.get("adresse", ""),
                    "type": item.get("activite", "restaurant"),
                    "_signal_context": (
                        f"Signal : {item.get('signal_label', '')} "
                        f"(source : {item.get('source', '').upper()}, "
                        f"date : {item.get('signal_date', '')}). "
                        f"Fenêtre : {item.get('contact_window', '')}. "
                        f"Pourquoi : {item.get('why', '')}"
                    ),
                }
                _fatal_api_error = False
                with st.status(f"Analyse {i+1}/{n} — {name}", expanded=True) as status:
                    st.write("Recherche du gérant (LinkedIn / Exa)...")
                    gerant = enrich_gerant(name, item.get("ville", bodacc_city))
                    if gerant.get("gerant_nom"):
                        st.write(f"→ Gérant : **{gerant['gerant_nom']}**")
                    else:
                        st.write("→ Gérant non trouvé")
                    restaurant_data["_gerant"] = gerant
                    st.write("Analyse Claude en cours — génération du score et des emails...")
                    stream_box = st.empty()
                    def _cb(text, box=stream_box):
                        box.code(text[-400:].lstrip(), language=None)
                    try:
                        data = analyze_restaurant(restaurant_data, stream_callback=_cb)
                        stream_box.empty()
                        data["signal"] = item
                        saved_path = Path(save_result(data, str(OUTPUT_DIR)))
                        results.append((data, saved_path))
                        status.update(
                            label=f"✓ {name} — Score {data.get('score', '?')}/100",
                            state="complete",
                            expanded=False,
                        )
                    except Exception as _e:
                        stream_box.empty()
                        _emsg = str(_e)
                        if "credit balance" in _emsg.lower() or "402" in _emsg:
                            status.update(label=f"❌ Crédits API insuffisants", state="error")
                            st.error(
                                "**Solde Anthropic insuffisant.** "
                                "Rechargez vos crédits sur "
                                "[console.anthropic.com/settings/billing]"
                                "(https://console.anthropic.com/settings/billing) "
                                "puis relancez l'analyse."
                            )
                            _fatal_api_error = True
                        else:
                            status.update(label=f"⚠ Erreur — {name}", state="error")
                            st.warning(f"Analyse échouée pour {name} : {_emsg[:150]}")
                progress.progress((i + 1) / n)
                if _fatal_api_error:
                    break
                if DEMO_MODE:
                    st.session_state.demo_count += 1
                    if st.session_state.demo_count >= DEMO_LIMIT:
                        break

            progress.empty()

            results_sorted = sorted(results, key=lambda x: x[0].get("score", 0), reverse=True)
            chauds = [r for r in results_sorted if r[0].get("score", 0) >= 66]

            col_m1, col_m2, col_m3 = st.columns(3)
            col_m1.metric("Leads analysés", len(results_sorted))
            col_m2.metric("Chauds (≥66)", len(chauds))
            col_m3.metric("Score moyen", f"{int(sum(r[0].get('score',0) for r in results_sorted)/max(len(results_sorted),1))}/100")

            st.success("Analyse terminée. Allez dans **② Agir aujourd'hui** pour contacter les leads prioritaires.")
            st.divider()

            for i, (data, fpath_saved) in enumerate(results_sorted, 1):
                _render_card(data, key_prefix=f"detect_{i}", file_path=fpath_saved)

            if DEMO_MODE and st.session_state.demo_count >= DEMO_LIMIT and results_sorted:
                _show_upsell()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — AGIR AUJOURD'HUI
# ══════════════════════════════════════════════════════════════════════════════

with tab_act:
    st.markdown("### ② Agir aujourd'hui")
    st.caption("Leads classés par urgence (score ITO). Envoyez le J0 en un clic.")
    st.divider()

    if not OUTPUT_DIR.exists() or not any(
        f for f in OUTPUT_DIR.glob("*.json") if not f.name.startswith("summary_")
    ):
        st.info("Aucun lead analysé pour l'instant. Commencez par **① Détecter les signaux**.")
    else:
        top5, irp_alerts, seg = compute_ito_data(OUTPUT_DIR)

        # Segmentation
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Chauds (≥70)", seg["hot"])
        c2.metric("Tièdes (40–70)", seg["warm"])
        c3.metric("Froids (10–39)", seg["cold"])
        c4.metric("Dormants (<10)", seg["dormant"])

        st.divider()

        # Top 5
        st.markdown("#### Top 5 — Leads à contacter maintenant")
        if not top5:
            st.info("Aucun lead actif (score ≥ 40).")
        else:
            for i, c in enumerate(top5, 1):
                d  = c["data"]
                fp = c["fpath"]
                seq = d.get("sequence_status", "J0_a_envoyer")
                label, color = SEQ_LABELS.get(seq, (seq, "#64748B"))

                with st.container(border=True):
                    h1, h2, h3 = st.columns([1, 6, 3])

                    with h1:
                        st.markdown(f"<p style='font-size:32px;font-weight:700;color:#1E3A5F;margin:0'>#{i}</p>",
                                    unsafe_allow_html=True)
                        st.caption(f"ITO {c['ito']}")

                    with h2:
                        st.markdown(f"**{d.get('restaurant','?')}**")
                        st.caption(d.get("adresse", ""))
                        info_parts = [f"Score {c['score']}/100", f"Signal {c['days_since']}j"]
                        if d.get("gerant_nom") and d["gerant_nom"] != "non identifié":
                            info_parts.append(f"Gérant : {d['gerant_nom']}")
                        st.caption(" · ".join(info_parts))
                        st.markdown(
                            f"<span style='background:{color}20;color:{color};padding:2px 8px;"
                            f"border-radius:4px;font-size:12px;font-weight:600'>{label}</span>",
                            unsafe_allow_html=True,
                        )

                    with h3:
                        # Envoi J0 direct si pas encore envoyé
                        if seq == "J0_a_envoyer" and d.get("email_corps"):
                            email_val = d.get("email", "") or d.get("email_contact_probable", "")
                            to = st.text_input("Email destinataire", value=email_val,
                                               placeholder="contact@restaurant.fr",
                                               key=f"act_to_{i}", label_visibility="collapsed")
                            if st.button("Envoyer J0", key=f"act_send_{i}", type="primary"):
                                with st.spinner("Envoi..."):
                                    ok, msg = send_j0_email(to, d.get("email_objet",""), d.get("email_corps",""))
                                if ok:
                                    d["sequence_status"] = "J0_envoye"
                                    d["email_j0_sent_to"] = to
                                    d["email_j0_sent_at"] = datetime.now().isoformat()[:19]
                                    json.dump(d, open(fp, "w", encoding="utf-8"),
                                              ensure_ascii=False, indent=2)
                                    st.success("Envoyé ✓")
                                    st.rerun()
                                else:
                                    st.error(msg)
                        elif d.get("email_j0_sent_to"):
                            st.caption(f"✓ J0 envoyé le {d.get('email_j0_sent_at','')[:10]}")
                            st.caption(d.get("email_j0_sent_to",""))
                        else:
                            st.caption(f"Statut : {label}")

                    # Email J0 lisible en dessous
                    if d.get("email_corps"):
                        with st.expander(f"Voir l'email J0 — *{d.get('email_objet','')}*"):
                            st.code(d["email_corps"], language=None)

        # Alertes IRP
        if irp_alerts:
            st.divider()
            st.markdown("#### Alertes — Leads à risque de perte")
            st.caption("Signal BODACC vieux de plus de 60 jours sans contact. Agir ou archiver.")
            for a in irp_alerts:
                label, color = SEQ_LABELS.get(a["seq_status"], (a["seq_status"], "#64748B"))
                st.warning(
                    f"**{a['restaurant']}** — {a['days_since']} jours sans contact · "
                    f"Statut : {label}"
                )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — SUIVRE LES LEADS
# ══════════════════════════════════════════════════════════════════════════════

with tab_track:
    st.markdown("### ③ Suivre les leads")
    st.caption("Tableau de bord complet — mettez à jour les statuts, assignez, archivez.")
    st.divider()

    if not OUTPUT_DIR.exists() or not any(
        f for f in OUTPUT_DIR.glob("*.json") if not f.name.startswith("summary_")
    ):
        st.info("Aucun lead analysé. Commencez par **① Détecter les signaux**.")
    else:
        json_files = sorted(
            [f for f in OUTPUT_DIR.glob("*.json") if not f.name.startswith("summary_")],
            key=lambda f: f.stat().st_mtime, reverse=True,
        )

        rows = []
        all_data = {}
        for f in json_files:
            try:
                d = json.load(open(f, encoding="utf-8"))
                score = d.get("score", 0)
                seq   = d.get("sequence_status", "")
                label, _ = SEQ_LABELS.get(seq, (seq, ""))
                rows.append({
                    "Restaurant":   d.get("restaurant", f.stem),
                    "Score":        score,
                    "Statut":       label,
                    "Gérant":       d.get("gerant_nom", "—"),
                    "Système":      d.get("systeme_actuel", "—"),
                    "Assigné à":    d.get("assigned_to", "—"),
                    "Analysé le":   d.get("timestamp", "")[:10],
                })
                all_data[d.get("restaurant", f.stem)] = (d, f)
            except Exception:
                pass

        if rows:
            # Filtres rapides
            f1, f2 = st.columns([3, 1])
            with f2:
                filter_status = st.selectbox(
                    "Filtrer par statut",
                    ["Tous"] + list(dict.fromkeys(r["Statut"] for r in rows)),
                    key="track_filter",
                )
            filtered_rows = rows if filter_status == "Tous" else [r for r in rows if r["Statut"] == filter_status]

            st.dataframe(filtered_rows, width="stretch", hide_index=True,
                         column_config={
                             "Score": st.column_config.ProgressColumn(
                                 "Score", min_value=0, max_value=100, format="%d/100"
                             )
                         })

        st.divider()
        st.markdown("**Détail et mise à jour d'un lead**")
        names = [r["Restaurant"] for r in rows]
        if names:
            selected = st.selectbox("Sélectionner un lead", names, key="track_select")
            if selected and selected in all_data:
                sel_data, sel_path = all_data[selected]
                _render_card(sel_data, key_prefix="track", file_path=sel_path)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — ANALYSE MANUELLE
# ══════════════════════════════════════════════════════════════════════════════

with tab_manual:
    st.markdown("### Analyse manuelle")
    st.caption("Analysez un restaurant dont vous connaissez le nom — utile avant un appel ou une démo.")
    st.divider()

    col1, col2 = st.columns(2)
    with col1:
        single_name = st.text_input("Nom du restaurant", key="s_name",
                                     placeholder="Ex : O'Tacos République, Mamie Pizza Lyon")
    with col2:
        single_city = st.text_input("Ville", value="Paris", key="s_city")

    if DEMO_MODE:
        remaining = DEMO_LIMIT - st.session_state.demo_count
        if remaining <= 0:
            _show_upsell()
        else:
            st.info(f"Mode démo — Claude Haiku · {remaining} analyse(s) restante(s)")

    if (not DEMO_MODE or st.session_state.demo_count < DEMO_LIMIT) and \
            st.button("Analyser ce restaurant", type="primary", key="s_run", disabled=not api_ok):
        if not single_name.strip():
            st.warning("Entrez le nom du restaurant.")
            st.stop()

        restaurant_data = {"name": single_name, "full_address": single_city}

        if OUTSCRAPER_AVAILABLE:
            with st.spinner("Données Google Maps..."):
                enriched = fetch_single_restaurant(single_name, single_city)
                if enriched:
                    restaurant_data = enriched

        with st.status(f"Analyse — {single_name}", expanded=True) as status:
            st.write("Recherche du gérant (LinkedIn / Exa)...")
            gerant = enrich_gerant(single_name, single_city)
            if gerant.get("gerant_nom"):
                st.write(f"→ Gérant : **{gerant['gerant_nom']}**")
                restaurant_data["_gerant"] = gerant
            st.write("Analyse Claude en cours — génération du score et des emails...")
            stream_box_m = st.empty()
            def _cb_manual(text, box=stream_box_m):
                box.code(text[-400:].lstrip(), language=None)
            try:
                data = analyze_restaurant(restaurant_data, stream_callback=_cb_manual)
                stream_box_m.empty()
                status.update(label=f"✓ {single_name} — Score {data.get('score','?')}/100",
                              state="complete", expanded=False)
            except Exception as _e:
                stream_box_m.empty()
                _emsg = str(_e)
                if "credit balance" in _emsg.lower() or "402" in _emsg:
                    status.update(label="❌ Crédits API insuffisants", state="error")
                    st.error(
                        "**Solde Anthropic insuffisant.** "
                        "Rechargez sur [console.anthropic.com/settings/billing]"
                        "(https://console.anthropic.com/settings/billing)."
                    )
                    st.stop()
                else:
                    status.update(label=f"⚠ Erreur analyse", state="error")
                    st.error(f"Erreur : {_emsg[:200]}")
                    st.stop()

        OUTPUT_DIR.mkdir(exist_ok=True)
        save_result(data, str(OUTPUT_DIR))
        if DEMO_MODE:
            st.session_state.demo_count += 1

        score = data.get("score", 0)
        st.success(f"Analyse terminée — Score : **{score}/100**")
        st.divider()
        _render_card(data, key_prefix="manual")

        if DEMO_MODE and st.session_state.demo_count >= DEMO_LIMIT:
            _show_upsell()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — CONTEXTE
# ══════════════════════════════════════════════════════════════════════════════

with tab_context:
    st.markdown("### Comprendre ce pipeline")
    st.caption("Lisez ceci avant de tester — cela vous donnera un regard critique sur les résultats.")
    st.divider()

    _company  = os.environ.get("COMPANY_NAME", "Non configuré")
    _context  = os.environ.get("COMPANY_CONTEXT", "Non configuré")
    _rep      = os.environ.get("SALES_REP_NAME", "Non configuré")

    col_left, col_right = st.columns([3, 2])

    with col_left:
        st.markdown("#### Qui utilise ce pipeline ?")
        st.markdown(
            f"**Entreprise cliente :** {_company}  \n"
            f"**Commercial :** {_rep}  \n"
            f"**Produit :** {_context}"
        )
        st.markdown("")
        st.markdown("#### Ce que fait le pipeline")
        st.markdown(
            "1. **Détecte** les restaurants qui viennent d'ouvrir ou de changer de gérant "
            "via BODACC — le registre officiel français des immatriculations.\n"
            "2. **Score** chaque restaurant sur 15 critères (signal temporel, système POS "
            "concurrent, multi-établissements, gérant identifié...).\n"
            "3. **Génère** une séquence de 5 emails personnalisés avec Claude — uniquement "
            "si le score est ≥ 40.\n"
            "4. **Enrichit** le contact : scraping du site web, TripAdvisor, LinkedIn via Exa."
        )
        st.markdown("")
        st.markdown("#### Pourquoi BODACC ?")
        st.info(
            "Un restaurant qui vient d'ouvrir n'a pas encore signé avec un fournisseur POS. "
            "Le gérant prend encore les décisions lui-même. "
            "C'est la fenêtre de contact optimale : J0 à J60 après l'immatriculation."
        )

    with col_right:
        st.markdown("#### Ce que les emails ne font PAS")
        st.markdown(
            "<div style='background:#FEF2F2;border:1px solid #FECACA;border-radius:8px;"
            "padding:16px'>"
            "<p style='margin:0 0 8px 0;font-weight:600;color:#991B1B'>Règles strictes</p>"
            "<ul style='margin:0;padding-left:18px;color:#7F1D1D;line-height:2'>"
            "<li>Aucun nom de client inventé</li>"
            "<li>Aucune stat non vérifiable</li>"
            "<li>Aucun chiffre local fabriqué</li>"
            "<li>Séquence désactivée si score < 40</li>"
            "<li>Gérant non vérifié → badge orange, prénom non utilisé</li>"
            "</ul></div>",
            unsafe_allow_html=True,
        )
        st.markdown("")
        st.markdown("#### Personnaliser pour votre contexte")
        st.code(
            "# Dans le fichier .env :\n"
            "COMPANY_NAME=Votre entreprise\n"
            "COMPANY_CONTEXT=Description de votre produit\n"
            "SALES_REP_NAME=Prénom Nom\n"
            "DEMO_MODE=false  # true = Claude Haiku + limite 3 analyses",
            language="bash",
        )

    st.divider()
    st.markdown("#### Limites connues")
    st.markdown(
        "- **Gérant LinkedIn** : la recherche Exa peut retourner un faux positif. "
        "Un badge orange s'affiche si le profil n'est pas vérifié.\n"
        "- **Score** : basé sur des signaux publics. Un restaurant sans site web ni "
        "présence en ligne sera sous-scoré même s'il est un bon prospect.\n"
        "- **Emails** : générés par Claude sur la base des données disponibles. "
        "Toujours relire avant d'envoyer."
    )
