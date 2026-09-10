# Veille IA — journal des modifications et suite du projet

Dernière mise à jour : **09/09/2026**

---

## 1. En bref

L'application tourne. Deux exécutions réelles à ce jour, 46 articles envoyés sur
Telegram. Depuis aujourd'hui, un **déclenchement automatique quotidien à 05h00** est
en place : plus besoin d'ouvrir la fenêtre pour lancer la veille.

---

## 1 bis. Ce qui a changé le 09/09/2026

Demande de Nicolas depuis Telegram, deux points sur l'écoute des audios.

### « Artiste inconnu » remplacé par le numéro et la date

Telegram affiche sous le titre d'un audio son champ `performer` ; il n'était pas
renseigné, d'où « Artiste inconnu » sur le téléphone. Chaque audio d'article porte
désormais `Article 3 · mercredi 9 septembre 2026` : le numéro de l'article dans la
veille du jour (ordre d'envoi) et la date de l'exécution. Le titre reste celui de
l'article. (`telegram.py` : paramètre `performer` de `send_audio` ;
`pipeline.py` : `audio_performer`.)

### L'audio de présentation part en dernier

Les audios s'écoutent du plus récent au plus ancien, la présentation du jour envoyée
en premier arrivait donc en dernier à l'oreille. Elle est maintenant envoyée **après
le dernier article et avant le récapitulatif** (`Pipeline.send_presentation`), le
message texte d'intro restant en tête. Comme on connaît alors le nombre d'articles,
elle l'annonce : « Bonjour. Veille IA du mercredi 9 septembre 2026, 05:00. 12 articles
au programme aujourd'hui. » Son titre est `Veille IA du <date>`, son artiste
`Présentation · 12 articles`.

Effectif dès l'exécution du 10/09 à 05h00 (processus neuf). Aucune fenêtre n'était
ouverte au moment de la modification. 8 tests ajoutés (40 au total).

---

## 2. Ce qui a changé le 25/08/2026

### Déclenchement automatique à 05h00

Jusqu'ici aucun lancement automatique n'existait : pas de timer systemd, pas de cron,
rien dans `~/.config/autostart/`, et la fenêtre n'a pas de planificateur interne. Les
deux exécutions passées avaient été lancées à la main depuis les boutons.

Deux unités systemd utilisateur ont été créées :

**`~/.config/systemd/user/veille-ia.service`**

```ini
[Unit]
Description=Veille IA (execution quotidienne)
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/home/morlot/code/veille-ia
ExecStart=/home/morlot/code/veille-ia/run.sh --cli
TimeoutStartSec=0
```

**`~/.config/systemd/user/veille-ia.timer`**

```ini
[Unit]
Description=Declenche la veille IA chaque jour a 05h00

[Timer]
OnCalendar=*-*-* 05:00:00
Persistent=true
AccuracySec=1min

[Install]
WantedBy=timers.target
```

Puis activation :

```bash
systemctl --user daemon-reload
systemctl --user enable --now veille-ia.timer
```

### Détails qui comptent

- **`Persistent=true`** : si le PC est éteint ou en veille à 05h00, l'exécution est
  rattrapée au démarrage suivant. Aucune journée de veille n'est perdue.
- **Pas d'exécution parasite à l'installation.** Comme 05h00 était déjà passé au
  moment de l'activation, `Persistent=true` aurait déclenché un rattrapage immédiat,
  en doublon de l'exécution qui tournait alors. Le fichier d'horodatage
  `~/.local/share/systemd/timers/stamp-veille-ia.timer` a donc été créé *avant*
  l'activation, ce qui fait croire à systemd que la journée est déjà traitée.
  Vérifié après coup : `veille-ia.service` est resté `inactive`.
- **`Linger=yes`** est actif sur le compte : le gestionnaire systemd utilisateur
  survit à la déconnexion, donc le timer part à 05h00 même sans session ouverte.

### Vérifications

```bash
systemctl --user list-timers veille-ia.timer   # NEXT = mer. 26/08/2026 05:00 CEST
systemctl --user is-active veille-ia.service   # inactive (normal hors exécution)
journalctl --user -u veille-ia.service -n 50   # sortie de la dernière exécution
```

Pour changer l'heure plus tard : éditer `OnCalendar` dans le `.timer`, puis
`systemctl --user daemon-reload && systemctl --user restart veille-ia.timer`.

---

## 3. État actuel du système

### Ce qui tourne

| Élément | État |
|---|---|
| Fenêtre Veille IA | ouverte depuis le 24/08 14:41 (PID 3252838, ~1 % CPU) |
| Timer quotidien | actif, prochaine exécution 26/08 à 05h00 |
| Ollama | `llama3.1:8b` (filtre) + `mistral-small3.2:latest` (résumé) |
| Telegram | bot `@ActuAIBot`, destinataire Nicolas (chat privé) |
| Voix | Piper local, alternance `fr_FR-siwis-medium` (femme) / `fr_FR-tom-medium` (homme) un article sur deux, modèles dans `~/code/jarvis/voiced/models` |
| Flux | 14 configurés, 14 lus sans erreur sur les deux exécutions |

### Emplacements

| Quoi | Où |
|---|---|
| Application | `~/code/veille-ia` |
| Configuration | `~/.config/veille-ia/config.toml` (droits 600) |
| Base + journal | `~/.local/state/veille-ia/{veille.sqlite,veille.log}` |
| Unités systemd | `~/.config/systemd/user/veille-ia.{service,timer}` |

### Réglages en vigueur

- `max_total = 0` et `max_per_feed = 0` : **aucun plafond**, tout ce qui passe le
  filtre est envoyé. Choix assumé.
- `skip_rejected = true` : un article déjà écarté n'est pas re-soumis au filtre.
- `send_audio = true` : chaque article part aussi en note vocale.
- `between_messages_s = 5` et `between_audio_s = 4` : anti rate-limit Telegram. Ce
  sont ces pauses qui expliquent la durée des exécutions, pas le LLM.

---

## 4. Historique des exécutions

| # | Date | Durée | Flux | Candidats | Nouveaux | Retenus | Envoyés | Erreurs |
|---|---|---|---|---|---|---|---|---|
| 1 | 24/08 14:41 → 14:55 | 14 min | 14/14 | 70 | 70 | 9 | 9 | 5 |
| 2 | 25/08 07:54 → 08:50 | 56 min | 14/14 | 308 | 299 | 37 | 37 | 16 |

Total à ce jour : **46 articles envoyés**, 92 liens connus en base.

Le taux de sélection du filtre est stable, autour de 12 %. La déduplication
fonctionne : sur l'exécution 2, 9 articles sur 308 étaient déjà connus et ont été
écartés sans repasser par le LLM.

---

## 5. Points en attente

Constats relevés le 25/08, par ordre d'importance.

### La fenêtre ouverte tourne sur du code périmé

Le processus a démarré le 24/08 à **14:41:15**, mais `config.py`, `pipeline.py`,
`llm.py` et `store.py` ont été modifiés à **14:43:45**, et `gui.py` et `journal.py` à
**14:46:35**. Python charge le code au démarrage : la fenêtre actuellement ouverte
exécute donc la version d'avant ces corrections.

Conséquence visible : le module `journal` a justement été ajouté à 14:46 pour qu'une
exécution lancée depuis la fenêtre laisse une trace sur disque. Cette version n'étant
pas chargée, les deux exécutions passées n'ont **rien écrit** dans `veille.log` — le
fichier s'arrête au 24/08 14:44 et ne contient que des lancements console.

À faire : fermer et rouvrir la fenêtre. L'exécution automatique de 05h00 démarre un
processus neuf, elle n'est pas concernée.

### 16 erreurs sur l'exécution du 25/08

Comptabilisées en base, mais sans détail conservé — précisément à cause du point
ci-dessus. À reprendre après le prochain passage du timer, quand le journal sera
alimenté : `journalctl --user -u veille-ia.service` et `veille.log` donneront le
détail. 5 erreurs sur 70 candidats la première fois, 16 sur 308 la seconde :
proportion stable, ça ressemble à des articles inaccessibles au téléchargement plutôt
qu'à une panne.

### `install.sh --timer` réécrit l'heure à 20h00

Le script d'installation génère les unités avec `OnCalendar=*-*-* 20:00:00`. Relancer
`./install.sh --timer` un jour écraserait le réglage de 05h00 sans prévenir. À
corriger dans `install.sh`, et à répercuter dans le `README.md` qui annonce lui aussi
20h00 à deux endroits.

### Le projet n'est pas sous git

`~/code/veille-ia` n'est pas un dépôt : aucun historique, aucun moyen de revenir en
arrière, et c'est ce qui a rendu la reconstitution ci-dessus plus laborieuse que
nécessaire. Un `git init` avec le `.gitignore` déjà présent (qui exclut bien la
config et la base) suffirait.

---

## 6. Projet — la suite

> **Section à compléter.** Dis-moi ce que tu veux faire évoluer et je remplis ici.
> Les pistes ci-dessous ne sont que les questions ouvertes que le code laisse voir,
> pas des décisions prises.

Questions ouvertes repérées dans le code et la configuration :

- **Récapitulatif quotidien.** `send_recap = true` envoie un message de fin
  d'exécution. Avec 37 articles d'un coup, une synthèse du jour aurait peut-être plus
  de valeur que le compte rendu chiffré.
- **Commandes du bot.** `show_commands = false` : le bloc `/info /rate /list` est
  prévu dans la configuration mais aucun bot n'y répond aujourd'hui. Le sujet est
  ouvert si tu veux interagir avec la veille depuis Telegram.
- **Prompt de filtrage.** Il cible « ingénieur IA à Lyon », avec trois critères dont
  les aides publiques Auvergne-Rhône-Alpes et UE. `./run.sh --reset-rejected` permet
  de réévaluer les articles écartés après l'avoir modifié.
- **Fenêtre ou service.** Maintenant que le timer existe, la fenêtre ne sert plus qu'à
  lancer à la demande et à lire le journal. À décider si elle reste ouverte en
  permanence.
