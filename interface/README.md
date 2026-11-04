# Voix-là : l'interface graphique

Interface GTK 4 / libadwaita de `doubler`. On y gère les voix, on lance des doublages et on suit leur progression,
puis on écoute et on compare les résultats. Elle ne fait aucun calcul lourd : tout passe par la commande `doubler`.

## Lancer

```sh
interface/installer.sh   # une fois : lanceur ~/.local/bin/voix-la, entrée du menu, icône
voix-la                  # ou « Voix-là » dans le menu des applications
cd interface && python3 -m voixla   # sans installation
interface/installer.sh --retirer    # tout enlever (le code et les données restent)
```

Prérequis du poste : Python 3, PyGObject avec GTK 4, libadwaita 1.5 et GStreamer (`gstreamer1.0-plugins-good`,
`-bad`, `-libav`, `libgtk-4-media-gstreamer`), `ffmpeg`/`ffprobe` et `pactl`. Aucune dépendance pip.

## Pages

| Page | Ce qu'elle fait |
|---|---|
| **Doubler** | Choisir une ou plusieurs vidéos, par le sélecteur (il ouvre `Vidéos/` des données, sinon le dossier Téléchargements) ou par glisser-déposer. Pour chaque vidéo, un extrait facultatif se règle dans un petit lecteur (« Début ici », « Fin ici », écouter l'extrait). Réglages : voix, mode, octave, pas, niveau de la voix, reverb, ancien mixage v1, et, si `doubler` les connaît, la chaîne de conversion : « Par défaut » (voix principale et chœurs séparés, reverb retirée, YingMusic-SVC), « Chaîne d'avant » (Seed-VC, sans nettoyage, plus rapide) ou « Sur mesure » (moteur et nettoyage au choix). L'estimation compte environ deux fois plus de GPU pour le chant nettoyé. La page estime la durée d'après les doublages passés, puis le bouton « Lancer » envoie le travail dans la file. |
| **Travaux** | La file. Un seul `doubler` tourne à la fois. Les étapes s'affichent en direct, avec le temps écoulé et le temps restant estimé, l'état du noyau Kaggle et le journal complet dépliable. On peut annuler un travail. « Voir le résultat » ouvre le doublage fini. |
| **Résultats** | Les doublages (titre, voix, mode, durée, date), avec une recherche. Le détail d'un doublage montre : la lecture de la vidéo ou du MP3 et la bascule **A original ⇄ B doublé à la même position** (touche B) ; la frise d'octave, où un clic va à ce moment ; les **visualisations du son** (ci-dessous) ; les informations (moteur réellement utilisé et repli éventuel sur Seed-VC, nettoyage appliqué ou sa raison avec les niveaux de la voix principale, des chœurs et de la reverb retirée, décision d'octave et sa raison) et les durées par étape (nettoyage, YingMusic-SVC, estimation de doubler comprise) ; le remix rapide ; l'ouverture du dossier et la mise à la corbeille. Si la vidéo source a changé de place, « Choisir… » l'indique à la main pour la bascule A/B (`travail/source.json` n'est pas modifié). |
| **Voix** | Les voix : nom, durée de la référence, hauteur de parole, alertes. On peut écouter la référence, choisir la voix par défaut, ajouter une voix depuis un fichier (`doubler --ajouter-voix`) ou au micro (texte à lire, compte à rebours, contrôle de qualité), la renommer ou la mettre à la corbeille. La page gère aussi les prises de chant pour l'affinage, avec le total cumulé face à l'objectif de 5 à 10 min. |
| **Écoute à l'aveugle** | Les versions A, B, C… de chaque extrait du banc d'essai (`banc/ecoute/`) : mix et voix seule. Vous les notez de 1 à 5. La correspondance lettre → version n'apparaît qu'après votre vote, qui va dans `banc/ecoute/votes.json`. Vu-mètre, spectrogramme et forme d'onde de ce qui joue, si on les allume. |
| **État** | Kaggle : présence du jeton (son contenu n'est jamais lu), compte, statut des noyaux connus. `doubler` : options reconnues, verrou libre ou pris (verrou commun `~/.local/state/voix-la/verrou-kaggle` et `sorties/.kaggle/verrou`). Le poste : RAM, charge, `file-attente --etat`. Kaggle se rafraîchit à la demande, ou toutes les 60 s au plus quand la page est visible. |
| **Réglages** (menu ☰, Ctrl+,) | Dossier de données, compte et noms Kaggle et moteur du chant (écrits par `doubler --config`), voix et réglages de doublage par défaut, micro et durées d'enregistrement, autre dossier de prises de chant, visualisations. |

Raccourcis : Alt+1 à Alt+6 ouvrent les pages, F5 actualise, Ctrl+, ouvre les réglages, Ctrl+? les raccourcis.

## Visualisations du son

Chacune s'allume ou s'éteint dans le menu « œil » d'un résultat (ou dans les Réglages) ; le choix est gardé dans
`interface.json`.

| Visualisation | Par défaut | Ce qu'elle montre |
|---|---|---|
| **Courbe de mélodie** | allumée | La voix d'origine (trait fin), la consigne, c'est-à-dire l'origine décalée du décalage de chaque passage (pointillés), et la voix convertie (trait plein, aux couleurs de la frise d'octave). Fenêtre de 12 s qui suit la lecture, échelle en notes (do, ré… ; la3 = 440 Hz) ou en Hz, clic pour aller à un moment. |
| **Spectrogramme** | éteint | Défile avec la lecture ; la bascule A/B change de spectrogramme (A : son d'origine, B : doublage). Échelle de fréquence logarithmique, couleurs lisibles en clair comme en sombre. |
| **Forme d'onde** | éteinte | Sous la frise d'octave : crêtes de toute la piste, partie déjà lue en couleur d'accent, curseur, clic pour y aller. |
| **Vu-mètre** | éteint | 24 bandes de 60 Hz à 8 kHz et niveau global, animés pendant la lecture. |

- **Données de la mélodie** : `travail/<voix>/courbes.json`, écrit par `doubler` (format 1 : `pas_s`, `source_hz`,
  `convertie_hz`, `decalage_dt`, et `chœurs_hz` facultatif). Pour un doublage plus ancien, l'interface calcule la
  hauteur elle-même (YIN avec numpy, son ramené à 16 kHz) à partir de `travail/voix_source.*` et de
  `travail/<voix>/voix_convertie.flac`. Le résultat est gardé dans `travail/<voix>/courbes-locales.json`, au même
  format, avec la signature des fichiers analysés : il est refait si la voix convertie change.
- **Spectrogramme, forme d'onde, vu-mètre** : une seule lecture du son par piste (ffmpeg, puis numpy), mise en cache
  dans `~/.cache/voix-la/pistes/`, par fichier et par produit. Un fichier refait (taille ou date) est réanalysé.
- **Sobriété** :
  - chaque calcul tourne dans un processus à part, sous `nice`, un seul à la fois ;
  - au-delà d'une minute de son, il passe par `file-attente`, la file des travaux lourds du poste ;
  - l'affichage ne recalcule rien : il redessine 25 fois par seconde au plus, seulement pendant la lecture et si la
    fenêtre est visible.

## Où sont les choses

- **Données** (`voix/`, `sorties/`, `Vidéos/`) : `VOIXLA_DONNEES`, sinon la clé `donnees` de
  `~/.config/voix-la/config.json`, sinon `~/Music/Voix-là`. C'est le même ordre que `doubler`, et jamais à côté du
  code.
- **Préférences de l'interface** : `~/.config/voix-la/interface.json` (réglages par défaut, visualisations, vidéos
  sources choisies à la main). L'interface ne touche pas au `config.json` de
  `doubler`, sauf par `doubler --config`.
- **Voix par défaut** : celle choisie dans l'interface (page Voix ou Réglages, gardée dans `interface.json`), sinon
  celle de `doubler` (clé `voix` de `config.json`, annoncée par `doubler --voix-liste --json` et
  `doubler --config --json`), sinon la seule voix. Avec plusieurs voix et aucune par défaut, rien n'est présélectionné :
  il faut en choisir une avant de lancer.
- **File des travaux** : `~/.local/state/voix-la/travaux/<id>/`. On y trouve `demande.json`, `etat.json`,
  `journal.txt` et `evenements.jsonl`.
- **Enregistrements** :
  - parole brute : `voix/.enregistrements/`, puis `voix/<nom>/enregistrement-….wav` une fois la voix créée ;
  - prises de chant : `voix/<nom>/chant/chant-AAAAMMJJ-HHMMSS.wav`, en 48 kHz et 24 bits. Un autre dossier de prises
    `chant-*.wav` (faites hors de l'interface) peut être compté en lecture seule pour la voix par défaut (Réglages,
    préférence `prises_externes`).
- **Votes de l'écoute à l'aveugle** : `banc/ecoute/votes.json`.

```json
{"format": 1, "votes": [{"extrait": "refrain", "date": "…", "notes": {"A": 4, "B": 2}, "classement": ["A", "B"],
 "commentaire": "", "correspondance": {"A": "V1", "B": "V0"}}]}
```

## Fonctionnement

- **Un seul module parle à `doubler`** : `voixla/moteur.py`. Il repère les options par `doubler --help` et utilise
  les plus récentes quand elles existent. Sinon, il se replie :

  | Option | Repli |
  |---|---|
  | `--evenements` | lecture de la sortie texte (`LectureTexte`) |
  | `--donnees` | `--sortie` et `DOUBLER_VOIX` |
  | `--config` | écriture directe de `config.json` |
  | `--voix-liste --json` | lecture de `voix/*/voix.json` |
- **Les travaux survivent à la fenêtre.** L'interface écrit la demande, puis lance l'exécuteur détaché
  (`python3 -m voixla.executeur`, dans une session à part). Il prend les travaux un par un, et chaque `doubler` tourne
  dans son propre groupe de processus. Au relancement, l'interface relit le journal et les événements depuis le
  début. Si la fenêtre est fermée, l'exécuteur prévient par une notification du bureau à la fin de chaque travail.
- **Annuler** pose une marque. L'exécuteur envoie alors SIGTERM au groupe de processus, puis SIGKILL au bout de 8 s.
  La session Kaggle déjà partie **continue seule** et occupe une place GPU jusqu'au bout. Son résultat n'est pas
  rapatrié.
- **Une seule session Kaggle pour tout le lot.** Toutes les vidéos d'un lancement partent dans un seul appel
  `doubler`, donc une seule session Kaggle, chacune avec son extrait (`vidéo@60+40`). Repli pour un `doubler` qui ne
  connaît pas `vidéo@…` : comme `--debut`/`--duree` valent alors pour tout l'appel, il y a un appel (une session) par
  extrait différent, l'un après l'autre.
- **Session interrompue.** Si `doubler` a laissé une session Kaggle interrompue (`session-en-cours.json`, verrou
  libre), la page Travaux propose « Reprendre », qui met `doubler --reprendre` dans la file : rapatriement, mixage et
  vidéo, sans GPU.
- **Le remix ne passe pas par la file.** Il est léger (quelques secondes, sans Kaggle), il est lancé tout de suite, un
  seul à la fois. Il est refusé si un doublage en cours écrit dans le même dossier, si la vidéo source manque, ou si le
  dossier ne porte plus le nom que `doubler` attend.
- **L'interface ne se fige jamais.** Tout ce qui est lent passe dans un fil et revient par `GLib.idle_add` :
  `doubler`, `kaggle` (limite de 120 s), `ffprobe`, `ffmpeg`, la liste des résultats.

## Essais

```sh
cd interface && nice python3 -m unittest discover -s tests -t .      # tests de la logique (sans écran)
VOIXLA_DOUBLER="python3 tests/faux_doubler.py" python3 -m voixla    # file des travaux avec un faux doubler
outils/capturer.sh /tmp/capture.png sombre "resultats:detail" 1180x800   # capture sous Xvfb (une à la fois)
```

Variables pour les essais :

| Variable | Rôle |
|---|---|
| `VOIXLA_DONNEES` | dossier de données |
| `VOIXLA_CONFIG` | dossier de `config.json` et `interface.json` |
| `VOIXLA_ETAT` | dossier de la file des travaux |
| `VOIXLA_ECOUTE` | banc d'écoute |
| `VOIXLA_CACHE` | cache des analyses du son |
| `VOIXLA_DOUBLER` | commande `doubler` |
| `VOIXLA_KAGGLE` | commande `kaggle` |
| `VOIXLA_MICRO_ESSAI=1` | son de synthèse au lieu du micro |
| `VOIXLA_APP_ID` | instance distincte de l'application |

## Limites

- **Enregistrement** : seulement par un micro du PC (PulseAudio/PipeWire) ; pas de micro de téléphone.
- **Bascule A/B en vidéo** : elle charge deux lecteurs, l'original et le doublage. Si la vidéo source a été déplacée,
  la bascule est désactivée et l'interface dit pourquoi.
- **Renommer l'identifiant d'une voix** : les doublages déjà faits gardent l'ancien dossier `travail/<ancien>/`, et
  leur remix ne retrouve plus la voix. Changer seulement le nom affiché ne pose pas ce problème.
- **Fenêtre fermée** : les travaux en attente démarrent quand même, car l'exécuteur détaché les enchaîne. Ils ne
  s'affichent qu'à la réouverture.
