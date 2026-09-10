# Veille IA

Application Ubuntu autonome qui remplace le workflow n8n de veille : lecture des
flux RSS, filtrage et résumé par Ollama en local, envoi Telegram texte + audio
avec la voix Piper. Aucun n8n, aucun service à démarrer, aucun appel à un service
externe en dehors des flux RSS et de l'API Telegram.

Lancement : **double-clic sur « Veille IA »** (bureau ou menu des applications).

## Installation

```bash
cd ~/code/veille-ia
./install.sh              # environnement Python, voix, lanceur double-clic
./install.sh --timer      # en plus : déclenchement automatique chaque jour à 20h00
./install.sh --no-tts     # sans piper-tts embarqué (~400 Mo en moins)
./install.sh --uninstall  # retire lanceurs et timer, garde config et historique
```

Puis renseigner le token du bot dans `~/.config/veille-ia/config.toml`
(bouton **Configuration** dans l'application) et cliquer sur **Revérifier**.

## Ce que fait l'application

1. Message d'intro Telegram (date, heure)
2. Lecture des 14 flux RSS en parallèle
3. Déduplication persistante : un article déjà envoyé ne revient jamais
4. Téléchargement de l'article, extraction du texte utile (le menu, les scripts
   et le pied de page sont retirés)
5. Filtrage par `llama3.1:8b` : réponse `$$oui` / `$$non`
6. Résumé par `mistral-small3.2` en 5 phrases maximum
7. Message Telegram formaté en HTML, puis synthèse vocale Piper et envoi audio.
   Le lecteur affiche le titre de l'article et, à la place de « Artiste inconnu »,
   `Article 3 · mercredi 9 septembre 2026` : le numéro dans la veille du jour et la date
8. Audio de présentation du jour, **envoyé après le dernier article** : les audios
   s'écoutent du plus récent au plus ancien, c'est donc le premier entendu, et il
   annonce le nombre d'articles
9. Message de récapitulatif

## Utilisation en console

```bash
./run.sh              # interface graphique
./run.sh --cli        # exécution en console (utilisé par le timer)
./run.sh --dry-run    # simulation : tout sauf les envois et l'écriture d'état
./run.sh --test       # envoi réel d'un article, sur une base jetable
./run.sh --test 3     # idem avec 3 articles
./run.sh --check      # diagnostics Ollama / Telegram / voix / base
./run.sh --reset-rejected   # réévalue les articles écartés (après changement de prompt)
```

## Vérifier que ça arrive bien sur Telegram

Trois niveaux, du moins au plus engageant :

1. `./run.sh --check` : vérifie le token avec `getMe` et l'accès au chat avec
   `getChat`. **Aucun message n'est envoyé.** Affiche le `@nom` du bot et le nom du
   destinataire. Si le chat est injoignable, envoie `/start` au bot depuis ce compte.
2. `./run.sh --dry-run` : déroule toute la chaîne, y compris la synthèse vocale,
   sans rien envoyer ni rien marquer comme vu. Valide les flux, Ollama et Piper.
3. `./run.sh --test` ou le bouton **📨 Test (1 article)** : envoi réel. Tu dois
   recevoir dans l'ordre le message d'intro, un article, son audio, l'audio de
   présentation du jour, puis le récapitulatif. En mode test le verdict du filtre est affiché mais l'envoi
   est forcé, pour que le test valide toujours la livraison. La base utilisée est
   `test.sqlite`, jetable : l'article testé reviendra dans l'exécution normale.

Le journal complet de chaque exécution est dans
`~/.local/state/veille-ia/veille.log`, que l'exécution vienne de la fenêtre ou de la
console, et le bouton **Journal** l'ouvre.

Attention : la configuration est lue **au démarrage** de l'application. Une
modification du fichier ne s'applique pas à une exécution déjà en cours.

## Emplacements

| Quoi | Où |
|---|---|
| Application | `~/code/veille-ia` |
| Configuration | `~/.config/veille-ia/config.toml` (droits 600, hors dépôt git) |
| Base et journal | `~/.local/state/veille-ia/{veille.sqlite,veille.log}` |
| Voix Piper | `~/piper-models` ou `~/code/jarvis/voiced/models` (siwis + tom) |

## Réglages utiles

Dans `~/.config/veille-ia/config.toml` :

- `fetch.max_per_feed` et `fetch.max_total` valent `0` : **aucun plafond**, tous les
  nouveaux articles sont traités. Les 14 flux exposent 308 entrées, donc la première
  exécution est longue (compter 2 h 30 et environ 180 messages Telegram). Les suivantes
  ne traitent que la nouveauté du jour, soit 30 à 60 minutes. Mettre une valeur non
  nulle rétablit un plafond ; les articles en trop reviennent à l'exécution suivante.
- `fetch.skip_rejected` (vrai par défaut) mémorise les articles écartés par le filtre.
  Sans lui, les mêmes centaines d'articles refusés seraient réévalués à chaque
  exécution, pour rien. Après un changement de prompt ou de modèle de filtrage,
  `./run.sh --reset-rejected` les rend à nouveau candidats.
- `ollama.summary_num_predict` (500) borne la longueur du résumé. Sans borne, un
  modèle qui part en digression peut occuper plusieurs minutes sur un seul article.
- `ollama.filter_model` : `llama3.1:8b` est rapide mais permissif. `mistral-small3.2`
  ou `gemma3:27b` (à télécharger) filtrent plus finement, pour environ 20 s par article.
- `tts.backend` : `local` (Piper dans le processus), `http` (serveur `piper_server.py`
  sur le port 5000), `off`, ou `auto` qui prend le local puis bascule sur le HTTP.
- `tts.voice` et `tts.voice_alt` : les articles sont lus en alternance, une voix de
  femme (`fr_FR-siwis-medium`) puis une voix d'homme (`fr_FR-tom-medium`), un article
  sur deux. Vider `voice_alt` revient à une voix unique. La présentation du jour
  garde `tts.voice`.
- `prompts.filter_prompt` et `prompts.summary_prompt` reprennent mot pour mot ceux
  du workflow n8n. Les champs `{title}` et `{content}` sont substitués.

## Différences volontaires avec le workflow n8n

| Point | n8n | Ici |
|---|---|---|
| Déduplication | node Remove Duplicates, marquée dès la lecture | SQLite, marquée **après** un envoi Telegram réussi : un plantage ne perd aucun article. Les refus du filtre sont mémorisés à part et effaçables |
| Contenu envoyé au LLM | page entière | plafonné à 8000 caractères, ce qui accélère beaucoup Ollama |
| Modèle gardé en mémoire | rechargé selon les cas | `keep_alive` de 10 minutes entre deux articles |
| Nettoyage du résumé | un ``` final pouvait rester collé | retiré, cas couvert par les tests |
| Lecture des flux | 14 nodes en série | 14 requêtes en parallèle, environ 1 s au total |
| Déclenchement | Schedule Trigger dans n8n | double-clic, ou timer systemd avec `--timer` |

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

40 tests couvrent la déduplication (paramètres de tracking, titres accentués),
l'analyse des réponses du filtre, le nettoyage des résumés, l'extraction HTML,
le formatage des dates, la persistance SQLite, l'alternance des voix, les
métadonnées des audios et l'ordre d'envoi de la présentation.
