"""Surveille le stock Fnac de la Switch 2 Édition Limitée Zelda 40e anniversaire
et envoie une notification ntfy.sh quand l'état change."""

import base64
import json
import os
import re
import smtplib
import sys
import time
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

PRODUCT_URL = (
    "https://www.fnac.com/Console-Nintendo-Switch-2-Edition-Limitee-40eme-anniversaire-"
    "The-Legend-of-Zelda/a21424371/w-4"
)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
STATE_FILE = Path(__file__).with_name("state.json")
DEBUG_DIR = Path(__file__).with_name("debug")
# Si l'état oscille entre "bloqué" et autre chose, pas plus d'une alerte "bloqué" par période.
BLOCKED_ALERT_COOLDOWN = 12 * 3600

BUY_RE = re.compile(r"ajouter au panier|pr[ée]commander", re.I)
QUEUE_RE = re.compile(r"salle d'attente|file d'attente|queue-it|vous êtes dans la file", re.I)
BLOCK_RE = re.compile(r"captcha-delivery|datadome|geo\.captcha|access denied|accès refusé", re.I)
OOS_RE = re.compile(
    r"rupture de stock|stock épuisé|épuisé|indisponible|plus disponible|"
    r"non disponible|bientôt disponible|m'alerter|me prévenir",
    re.I,
)
# Zones de la page où un bouton "Ajouter au panier" ne concerne pas ce produit.
NOISE_SELECTORS = (
    "[class*='carousel' i], [class*='Carousel'], [class*='slider' i], "
    "[class*='recommend' i], [class*='crossSell' i], [class*='bundle' i], "
    "header, footer, nav"
)


def log(*args):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}]", *args, flush=True)


def analyse_page():
    """Retourne (état, détail) avec état parmi dispo, file, rupture, bloqué."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            locale="fr-FR",
            timezone_id="Europe/Paris",
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 900},
            extra_http_headers={"Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"},
        )
        page = context.new_page()
        try:
            resp = page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=45000)
        except PWTimeout:
            browser.close()
            return "bloqué", "timeout au chargement"

        status = resp.status if resp else 0
        log(f"HTTP {status} — URL finale : {page.url}")
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except PWTimeout:
            log("networkidle non atteint, on continue")
        page.wait_for_timeout(2000)

        html = page.content()
        try:
            text = page.inner_text("body", timeout=5000)
        except PWTimeout:
            text = ""
        title = page.title()
        log(f"Titre : {title!r} — {len(text)} caractères de texte")
        log("Début du texte :", " ".join(text.split())[:300])

        DEBUG_DIR.mkdir(exist_ok=True)
        (DEBUG_DIR / "page.html").write_text(html, encoding="utf-8")
        try:
            page.screenshot(path=str(DEBUG_DIR / "page.png"), full_page=False)
        except Exception as e:  # capture facultative
            log("capture impossible :", e)

        if QUEUE_RE.search(page.url) or QUEUE_RE.search(text) or QUEUE_RE.search(title):
            browser.close()
            return "file", "file / salle d'attente détectée"

        if status in (403, 429) or BLOCK_RE.search(page.url) or (
            BLOCK_RE.search(html) and len(text) < 2000
        ):
            browser.close()
            return "bloqué", f"anti-bot (HTTP {status})"
        if len(text.strip()) < 200:
            browser.close()
            return "bloqué", f"page vide (HTTP {status}, {len(text)} car.)"

        # Boutons d'achat visibles, hors carrousels / recommandations.
        buttons = page.locator("button, a, input[type=submit]").filter(has_text=BUY_RE)
        hits = []
        for i in range(min(buttons.count(), 40)):
            b = buttons.nth(i)
            try:
                if not b.is_visible() or b.is_disabled():
                    continue
                in_noise = b.evaluate(
                    "(el, sel) => !!el.closest(sel)", NOISE_SELECTORS
                )
                label = " ".join(b.inner_text().split())[:60]
            except Exception:
                continue
            log(f"  bouton : {label!r}{' (ignoré: carrousel)' if in_noise else ''}")
            if not in_noise:
                hits.append(label)
        browser.close()

        if hits:
            return "dispo", f"bouton(s) : {', '.join(hits[:3])}"
        oos = OOS_RE.search(text)
        if oos:
            return "rupture", f"mention : {oos.group(0)!r}"
        if "zelda" not in (title + text).lower():
            return "bloqué", "page inattendue (produit introuvable dans la page)"
        return "rupture", "aucun bouton d'achat trouvé"


def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def notify(title, message, tags):
    """Envoie sur chaque canal configuré (ntfy, e-mail). Vrai si au moins un envoi a réussi."""
    channels = []
    if os.environ.get("NTFY_TOPIC", "").strip():
        channels.append(notify_ntfy)
    if os.environ.get("MAIL_TO", "").strip():
        channels.append(notify_mail)
    if not channels:
        log("Ni NTFY_TOPIC ni MAIL_TO : notification NON envoyée")
        return False
    results = [send(title, message, tags) for send in channels]
    return any(results)


def notify_mail(title, message, tags):
    to = os.environ["MAIL_TO"].strip()
    host = os.environ.get("SMTP_HOST", "").strip()
    user = os.environ.get("SMTP_USER", "").strip()
    msg = EmailMessage()
    msg["Subject"] = title
    msg["From"] = os.environ.get("MAIL_FROM", "").strip() or user or to
    msg["To"] = to
    msg.set_content(f"{message}\n\n{PRODUCT_URL}\n")
    try:
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=30) as s:
            s.starttls()
            if user:
                s.login(user, os.environ.get("SMTP_PASSWORD", ""))
            s.send_message(msg)
        log(f"E-mail envoyé à {to}")
        return True
    except Exception as e:
        log("Échec de l'envoi de l'e-mail :", e)
        return False


def notify_ntfy(title, message, tags):
    topic = os.environ["NTFY_TOPIC"].strip()
    # Les en-têtes HTTP n'acceptent pas l'UTF-8 brut : encodage RFC 2047, compris par ntfy.
    enc_title = "=?UTF-8?B?" + base64.b64encode(title.encode()).decode() + "?="
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=message.encode("utf-8"),
        method="POST",
        headers={
            "Title": enc_title,
            "Priority": "urgent",
            "Tags": tags,
            "Click": PRODUCT_URL,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            log(f"Notification envoyée (HTTP {r.status})")
            return True
    except Exception as e:
        log("Échec de l'envoi ntfy :", e)
        return False


def main():
    try:
        status, detail = analyse_page()
    except Exception as e:
        status, detail = "bloqué", f"erreur : {type(e).__name__}: {e}"[:300]
    log(f"ÉTAT = {status} ({detail})")

    state = load_state()
    previous = state.get("status")
    now = int(time.time())
    log(f"État précédent : {previous}")

    sent = True
    if status != previous:
        if status in ("dispo", "file"):
            msg = "Le bouton d'achat est là !" if status == "dispo" else "File d'attente ouverte !"
            sent = notify("ZELDA DISPO À LA FNAC", f"{msg} ({detail})", "rotating_light,video_game")
        elif status == "bloqué":
            if now - state.get("last_blocked_alert", 0) >= BLOCKED_ALERT_COOLDOWN:
                sent = notify(
                    "Bot Fnac Zelda bloqué",
                    f"La Fnac bloque la vérification ({detail}). Pas d'autre alerte tant que ça dure.",
                    "warning",
                )
                if sent:
                    state["last_blocked_alert"] = now
            else:
                log("Alerte 'bloqué' déjà envoyée récemment, on n'en renvoie pas")

    # On ne mémorise le nouvel état que si l'alerte associée est bien partie,
    # sinon elle sera retentée au prochain passage.
    if status != previous and sent:
        state.update(status=status, detail=detail, since=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        save_state(state)
        log("state.json mis à jour")
    return 0


if __name__ == "__main__":
    sys.exit(main())
