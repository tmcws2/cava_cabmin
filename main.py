"""
Bot de suivi des nominations en cabinets ministériels (JORF via API PISTE/Légifrance)
Publie sur Bluesky et Telegram les nouveaux arrêtés de nomination.
"""

import os
import json
import re
import requests
from datetime import datetime, timedelta
from pathlib import Path
from atproto import Client

# ──────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────

SEEN_IDS_FILE = Path("seen_ids.json")
PISTE_TOKEN_URL = "https://oauth.piste.gouv.fr/api/oauth/token"
PISTE_API_BASE  = "https://api.piste.gouv.fr/dila/legifrance/lf-engine-app"

# Mots-clés de recherche dans le JORF
SEARCH_TERMS = [
    "collaborateur de cabinet",
    "directeur de cabinet",
    "directrice de cabinet",
    "chef de cabinet",
    "conseiller de cabinet",
    "conseillère de cabinet",
    "attaché de cabinet",
    "chargé de mission au cabinet",
]


# ──────────────────────────────────────────────
# PERSISTANCE
# ──────────────────────────────────────────────

def load_seen_ids() -> set:
    if SEEN_IDS_FILE.exists():
        return set(json.loads(SEEN_IDS_FILE.read_text()))
    return set()


def save_seen_ids(ids: set):
    SEEN_IDS_FILE.write_text(json.dumps(sorted(ids), indent=2, ensure_ascii=False))


# ──────────────────────────────────────────────
# API PISTE / LÉGIFRANCE
# ──────────────────────────────────────────────

def get_piste_token() -> str:
    """Authentification OAuth2 client credentials sur PISTE."""
    resp = requests.post(PISTE_TOKEN_URL, data={
        "grant_type":    "client_credentials",
        "client_id":     os.environ["PISTE_CLIENT_ID"],
        "client_secret": os.environ["PISTE_CLIENT_SECRET"],
        "scope":         "openid",
    }, timeout=15)
    resp.raise_for_status()
    return resp.json()["access_token"]


def search_jorf(token: str, term: str, since_date: str) -> list[dict]:
    """
    Cherche les arrêtés JORF contenant `term` publiés depuis `since_date`.
    Retourne une liste de hits avec id, titre, date, url.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }
    payload = {
        "recherche": {
            "champs": [{"typeChamp": "ALL", "criteres": [{"typeRecherche": "EXACTE", "valeur": term}], "operateur": "ET"}],
            "filtres": [
                {"facette": "NATURE", "valeur": "ARRETE"},
                {"facette": "DATE_PUBLICATION", "valeurDebut": since_date},
            ],
            "fromAdvancedRecherche": False,
            "operateur": "ET",
            "pageNumber": 1,
            "pageSize": 20,
            "sort": "PUBLICATION_DATE_DESC",
            "typePagination": "DEFAUT",
        },
        "fond": "JORF",
    }
    resp = requests.post(f"{PISTE_API_BASE}/search", headers=headers, json=payload, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    return data.get("results", [])


def fetch_jorf_text(token: str, cid: str) -> str | None:
    """Récupère le texte complet d'un acte JORF par son CID."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    resp = requests.get(f"{PISTE_API_BASE}/consult/jorf/{cid}", headers=headers, timeout=20)
    if resp.status_code != 200:
        return None
    data = resp.json()
    # Le texte est dans article[0].content ou dans texte.articles
    articles = data.get("articles", [])
    if articles:
        return " ".join(a.get("content", "") for a in articles[:5])
    return data.get("texte", {}).get("content", "")


# ──────────────────────────────────────────────
# PARSING
# ──────────────────────────────────────────────

def parse_arrete(hit: dict, full_text: str | None) -> dict | None:
    """
    Extrait les infos utiles d'un arrêté.
    Retourne None si le document ne concerne pas une nomination en cabinet.
    """
    titre = hit.get("title", "")
    cid   = hit.get("id", "")
    date_pub = hit.get("publicationDate", "")[:10] if hit.get("publicationDate") else ""

    # Vérification rapide : le titre doit mentionner cabinet ou nomination
    titre_lower = titre.lower()
    if not any(kw in titre_lower for kw in ["cabinet", "nomination"]):
        return None

    # Extraction du/des noms dans le titre via regex heuristiques
    personne   = extract_person_from_title(titre)
    ministere  = extract_ministere_from_title(titre)
    poste      = extract_poste_from_title(titre, full_text or "")
    mouvement  = detect_movement(titre, full_text or "")

    return {
        "id":        cid,
        "date":      format_date(date_pub),
        "titre":     titre,
        "personne":  personne,
        "poste":     poste,
        "ministere": ministere,
        "mouvement": mouvement,
        "url":       f"https://www.legifrance.gouv.fr/jorf/id/{cid}",
    }


def extract_person_from_title(titre: str) -> str:
    """Tente d'extraire un prénom/nom du titre de l'arrêté."""
    # Pattern : "M. Prénom NOM" ou "Mme Prénom NOM"
    m = re.search(r"\b(M\.?|Mme\.?)\s+([A-ZÀ-Ÿa-zà-ÿ\-]+(?:\s+[A-ZÀ-Ÿa-zà-ÿ\-]+){0,3})", titre)
    if m:
        return f"{m.group(1)} {m.group(2).strip()}"
    return "Un·e collaborateur·rice"


def extract_ministere_from_title(titre: str) -> str:
    """Extrait la mention du ministère."""
    patterns = [
        r"cabinet (?:du|de la|de l'|des)\s+([^-,\.]+)",
        r"auprès (?:du|de la|de l')\s+([^-,\.]+ministre[^-,\.]*)",
    ]
    for p in patterns:
        m = re.search(p, titre, re.IGNORECASE)
        if m:
            return m.group(1).strip().rstrip(".")
    return "un ministre"


def extract_poste_from_title(titre: str, full_text: str) -> str:
    """Détecte le poste (directeur, conseiller, attaché…)."""
    postes = [
        "directeur de cabinet", "directrice de cabinet",
        "chef de cabinet", "cheffe de cabinet",
        "conseiller de cabinet", "conseillère de cabinet",
        "collaborateur de cabinet", "collaboratrice de cabinet",
        "chargé de mission", "chargée de mission",
        "attaché de cabinet", "attachée de cabinet",
        "secrétaire général",
    ]
    combined = (titre + " " + full_text).lower()
    for poste in postes:
        if poste in combined:
            return poste.capitalize()
    return "Collaborateur·rice de cabinet"


def detect_movement(titre: str, full_text: str) -> str:
    """Entrée, sortie ou reconduction ?"""
    combined = (titre + " " + full_text).lower()
    if any(w in combined for w in ["est nommé", "est nommée", "nomination"]):
        return "nomination"
    if any(w in combined for w in ["est mis fin", "cessation", "est relevé"]):
        return "fin de fonctions"
    if any(w in combined for w in ["est reconduit", "renouvellement"]):
        return "renouvellement"
    return "mouvement"


def format_date(date_str: str) -> str:
    """2025-04-01 → 1 avril 2025"""
    if not date_str:
        return ""
    mois = ["", "janvier", "février", "mars", "avril", "mai", "juin",
            "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
    try:
        y, m, d = date_str.split("-")
        return f"{int(d)} {mois[int(m)]} {y}"
    except Exception:
        return date_str


# ──────────────────────────────────────────────
# PUBLICATION
# ──────────────────────────────────────────────

EMOJIS = {
    "nomination":       "🟢",
    "fin de fonctions": "🔴",
    "renouvellement":   "🔵",
    "mouvement":        "⚪",
}


def build_message(arrete: dict) -> str:
    """Construit le message texte commun Bluesky/Telegram."""
    emoji = EMOJIS.get(arrete["mouvement"], "⚪")
    lines = [
        f"{emoji} {arrete['mouvement'].upper()} EN CABINET",
        f"👤 {arrete['personne']}",
        f"🏛️ {arrete['poste']}",
        f"🔹 Cabinet : {arrete['ministere']}",
        f"📅 JO du {arrete['date']}",
    ]
    return "\n".join(lines)


def post_bluesky(arrete: dict):
    """Publie sur Bluesky avec un lien facette."""
    handle   = os.environ["BLUESKY_HANDLE"]
    password = os.environ["BLUESKY_PASSWORD"]

    client = Client()
    client.login(handle, password)

    text = build_message(arrete) + f"\n🔗 {arrete['url']}"

    # Facette (lien cliquable) sur l'URL
    url_start = text.index("🔗 ") + len("🔗 ")
    url_end   = url_start + len(arrete["url"])

    client.send_post(
        text=text,
        facets=[{
            "$type": "app.bsky.richtext.facet",
            "index": {"byteStart": url_start, "byteEnd": url_end},
            "features": [{"$type": "app.bsky.richtext.facet#link", "uri": arrete["url"]}],
        }],
    )
    print(f"[Bluesky] Publié : {arrete['id']}")


def post_telegram(arrete: dict):
    """Publie sur Telegram via Bot API."""
    token   = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    text = build_message(arrete) + f"\n\n🔗 <a href='{arrete['url']}'>Voir au Journal Officiel</a>"

    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=15,
    )
    resp.raise_for_status()
    print(f"[Telegram] Publié : {arrete['id']}")


# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────

def main():
    seen_ids = load_seen_ids()
    token    = get_piste_token()

    # On recherche sur les 3 derniers jours (filet de sécurité)
    since = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")

    new_arretes: list[dict] = []
    seen_in_run: set[str]   = set()

    for term in SEARCH_TERMS:
        try:
            results = search_jorf(token, term, since)
        except Exception as e:
            print(f"[WARN] Recherche '{term}' échouée : {e}")
            continue

        for hit in results:
            cid = hit.get("id", "")
            if not cid or cid in seen_ids or cid in seen_in_run:
                continue

            seen_in_run.add(cid)

            # Récupération optionnelle du texte complet pour enrichir le parsing
            try:
                full_text = fetch_jorf_text(token, cid)
            except Exception:
                full_text = None

            arrete = parse_arrete(hit, full_text)
            if arrete:
                new_arretes.append(arrete)

    print(f"{len(new_arretes)} nouvel·le·s arrêté·s à publier.")

    for arrete in new_arretes:
        try:
            post_bluesky(arrete)
        except Exception as e:
            print(f"[ERR Bluesky] {arrete['id']} : {e}")

        try:
            post_telegram(arrete)
        except Exception as e:
            print(f"[ERR Telegram] {arrete['id']} : {e}")

        seen_ids.add(arrete["id"])

    save_seen_ids(seen_ids)
    print("Terminé.")


if __name__ == "__main__":
    main()
# post_bluesky(arrete)
# post_telegram(arrete)
