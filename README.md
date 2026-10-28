# fnac-zelda-bot

Surveille toutes les 5 minutes (GitHub Actions) la page Fnac de la
**Nintendo Switch 2 Édition Limitée 40e anniversaire The Legend of Zelda**
et envoie une notification push via [ntfy.sh](https://ntfy.sh).

- `check.py` : charge la page avec Playwright (Chromium, fr-FR) et détermine
  l'état `dispo`, `file`, `rupture` ou `bloqué`.
- `state.json` : dernier état connu (commité automatiquement quand il change),
  pour n'envoyer une alerte qu'au changement d'état.
- Secret requis : `NTFY_TOPIC` (nom du canal ntfy).

## Sur un VPS (sans interface graphique), avec alerte par e-mail

```bash
git clone https://github.com/mathisgpnpv/fnac.git fnac-zelda-bot && cd fnac-zelda-bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
sudo .venv/bin/python -m playwright install-deps chromium   # librairies système
.venv/bin/python -m playwright install chromium
cp config.env.example config.env && nano config.env          # adresse + SMTP
./run.sh                                                     # test : lire la ligne « ÉTAT = … »
```

Puis toutes les 5 minutes via cron (`crontab -e`) :

```
*/5 * * * * /chemin/vers/fnac-zelda-bot/run.sh >> /chemin/vers/fnac-zelda-bot/bot.log 2>&1
```

Ici `state.json` reste sur le VPS. Pensez à désactiver le workflow GitHub
si vous n'utilisez plus que le VPS.
