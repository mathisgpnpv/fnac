# fnac-zelda-bot

Surveille toutes les 5 minutes (GitHub Actions) la page Fnac de la
**Nintendo Switch 2 Édition Limitée 40e anniversaire The Legend of Zelda**
et envoie une notification push via [ntfy.sh](https://ntfy.sh).

- `check.py` : charge la page avec Playwright (Chromium, fr-FR) et détermine
  l'état `dispo`, `file`, `rupture` ou `bloqué`.
- `state.json` : dernier état connu (commité automatiquement quand il change),
  pour n'envoyer une alerte qu'au changement d'état.
- Secret requis : `NTFY_TOPIC` (nom du canal ntfy).
