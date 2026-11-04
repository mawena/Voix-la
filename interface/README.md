# Voix-là : l'interface graphique

Interface GTK 4 / libadwaita de `doubler`. On y gère les voix, on lance des doublages et on suit leur progression,
puis on écoute et on compare les résultats. Elle ne fait aucun calcul lourd : tout passe par la commande `doubler`.

## Lancer

Avec le paquet Debian (voir le README principal), `voix-la` ou « Voix-là » dans le menu des applications : le code
est dans `/usr/lib/voix-la/`, et l'interface lance `/usr/bin/doubler`. Depuis le dépôt, pour le développement :

```sh
interface/installer.sh   # une fois : lanceur ~/.local/bin/voix-la, entrée du menu, icône
voix-la                  # ou « Voix-là » dans le menu des applications
bin/voix-la              # sans installation (ou : cd interface && python3 -m voixla)
interface/installer.sh --retirer    # tout enlever (le code et les données restent)
```

Prérequis du poste : Python 3, PyGObject avec GTK 4, libadwaita 1.5 et GStreamer (`gstreamer1.0-plugins-good`,
`-bad`, `-libav`, `libgtk-4-media-gstreamer`), `ffmpeg`/`ffprobe` et `pactl`. Aucune dépendance pip.

## Pages

| Page | Ce qu'elle fait |
|---|---|
| **Doubler** | Choisir une ou plusieurs vidéos, par le sélecteur (il ouvre `Vidéos/` des données, sinon le dossier Téléchargements) ou par glisser-déposer. Pour chaque vidéo, un extrait facultatif se règle dans un petit lecteur (« Début ici », « Fin ici », écouter l'extrait). Réglages : voix, mode, octave, pas, niveau de la voix, reverb, ancien mixage v1, et, si `doubler` les connaît, la chaîne de conversion : « Par défaut » (voix principale et chœurs séparés, reverb retirée, YingMusic-SVC), « Chaîne d'avant » (Seed-VC, sans nettoyage, plus rapide) ou « Sur mesure » (moteur et nettoyage au choix), et l'interrupteur **Paroles** (transcription et traduction française ; éteint, il ajoute `--sans-paroles` ; allumé par défaut, d'après la clé `paroles` de la configuration de `doubler`). L'estimation compte environ deux fois plus de GPU pour le chant nettoyé. La page estime la durée d'après les doublages passés, puis le bouton « Lancer » envoie le travail dans la file. |
| **Travaux** | La file. Un seul `doubler` tourne à la fois. Les étapes s'affichent en direct, avec le temps écoulé et le temps restant estimé, l'état du noyau Kaggle et le journal complet dépliable. On peut annuler un travail. « Voir le résultat » ouvre le doublage fini. |
| **Résultats** | Les doublages (titre, voix, mode, durée, date), avec une recherche. Le détail d'un doublage montre : la lecture de la vidéo ou du MP3 et le **choix de ce qu'on écoute, à la même position** : Doublage, Original, Beat seul, Ma voix seule, Voix d'origine seule (voir [Pistes séparées](#pistes-séparées) ; touche B : doublage ⇄ original) ; les **paroles** synchronisées (ci-dessous) ; la frise d'octave, où un clic va à ce moment ; les **visualisations du son** (ci-dessous) ; les informations (moteur réellement utilisé et repli éventuel sur Seed-VC, nettoyage appliqué ou sa raison avec les niveaux de la voix principale, des chœurs et de la reverb retirée, décision d'octave et sa raison) ; les pistes séparées, avec « Enregistrer le beat… » et « Enregistrer ma voix seule… » ; le remix rapide ; les durées par étape (nettoyage, YingMusic-SVC, estimation de doubler comprise) ; l'ouverture du dossier et la mise à la corbeille. Si la vidéo source a changé de place, « Choisir… » l'indique à la main (`travail/source.json` n'est pas modifié) ; en attendant, l'original se lit sans image, d'après `travail/original.wav`. |
| **Voix** | Les voix : nom, durée de la référence, hauteur de parole, alertes. On peut écouter la référence, choisir la voix par défaut, ajouter une voix depuis un fichier (`doubler --ajouter-voix`) ou au micro (texte à lire, compte à rebours, contrôle de qualité), la renommer ou la mettre à la corbeille. La page gère aussi les prises de chant pour l'affinage, avec le total cumulé face à l'objectif de 5 à 10 min. |
| **Écoute à l'aveugle** | Les versions A, B, C… de chaque extrait du banc d'essai (`banc/ecoute/`) : mix et voix seule. L'interrupteur **Voix seule** de la barre du bas (touche V) fait passer la version en cours de son mix à sa voix seule (`X_voix.mp3`) et retour, à la même position. Vous notez les versions de 1 à 5. La correspondance lettre → version n'apparaît qu'après votre vote, qui va dans `banc/ecoute/votes.json`. Vu-mètre, spectrogramme et forme d'onde de ce qui joue, si on les allume. |
| **État** | Kaggle : présence du jeton (son contenu n'est jamais lu), compte, statut des noyaux connus. `doubler` : options reconnues, verrou libre ou pris (verrou commun `~/.local/state/voix-la/verrou-kaggle` et `sorties/.kaggle/verrou`). Le poste : RAM, charge, `file-attente --etat`. Kaggle se rafraîchit à la demande, ou toutes les 60 s au plus quand la page est visible. |
| **Réglages** (menu ☰, Ctrl+,) | Dossier de données, compte et noms Kaggle, moteur du chant et paroles par défaut (écrits par `doubler --config`, par exemple `doubler --config paroles non`), voix et réglages de doublage par défaut, micro et durées d'enregistrement, autre dossier de prises de chant, visualisations et paroles. |

Raccourcis : Alt+1 à Alt+6 ouvrent les pages, F5 actualise, Ctrl+, ouvre les réglages, Ctrl+? les raccourcis. Dans un
résultat, B bascule doublage ⇄ original et Espace lance ou arrête la lecture. Dans l'écoute à l'aveugle, V bascule
mix ⇄ voix seule.

## Pistes séparées

Dans le détail d'un résultat, les bascules au-dessus du lecteur choisissent ce qu'on écoute. On change de piste sans
perdre la position, et la lecture continue (ou reste en pause).

| Piste | Fichier lu | Si le fichier manque |
|---|---|---|
| **Doublage** (par défaut) | la vidéo doublée, ou son MP3 | grisée |
| **Original** | la vidéo d'origine (`travail/source.json`, ou celle choisie à la main), au décalage de l'extrait ; en MP3, `travail/original.wav` | sans la vidéo d'origine : `travail/original.wav`, sans image |
| **Beat seul** | `travail/instrumental.wav` : la musique sans aucune voix (original − voix isolée) | grisée, avec la raison |
| **Ma voix seule** | la voix convertie telle qu'elle sonne dans le mix (ci-dessous), préparée en cache au premier usage | ancien doublage sans voix sèche : `travail/<voix>/voix_convertie.flac` brute ; sans rien : grisée |
| **Voix d'origine seule** | `travail/voix_source.wav` : la voix d'origine isolée | grisée, avec la raison |

- **Ma voix seule** refait la part de la voix dans le mix de `doubler` (sa fonction `mixer`), sans la musique :
  - `seche_voix_convertie.wav` au gain `mix.gains_db.seche` de `rapport.json` ;
  - en chant, plus `reverb_voix_convertie.wav` au gain `mix.gains_db.reverb` ;
  - les deux sont additionnés sans normalisation, puis passés au même limiteur (crête vraie, `mix.limite_dbtp`), et
    coupés à la durée de `instrumental.wav`.

  Un essai fait mixer `doubler` sur une musique muette : sa sortie et la nôtre sont identiques à 2·10⁻⁴ près. En
  parole, il n'y a pas de réverbération, même si un ancien mix en a laissé le fichier. Sans gains dans le rapport, la
  voix sèche se lit à son propre niveau, et l'interface le dit. Le calcul tourne sous `nice`, dans la même file que
  les analyses du son, et passe par `file-attente` au-delà d'une minute de son. Il ne bloque jamais l'interface : le
  doublage continue en attendant. Le résultat va dans `~/.cache/voix-la/voix/` (FLAC, 300 Mo au plus, les moins
  récemment écoutés effacés d'abord) ; un remix change la clé et refait la voix.
- **Sans image** : à côté d'une vidéo, le beat et les voix seules se lisent sous l'image de la vidéo arrêtée, avec le
  bandeau « son seul, image arrêtée ». L'écran ne disparaît pas (la page ne saute pas) et rien n'est décodé deux fois.
  Les visualisations, elles, suivent la piste écoutée : le spectrogramme du beat, de la voix seule…
- **Enregistrer le beat…** et **Enregistrer ma voix seule…** (groupe « Pistes séparées ») ouvrent un sélecteur sur le
  dossier du doublage, avec le nom des sorties : `<titre> - voix <Nom> (beat).mp3` et
  `<titre> - voix <Nom> (voix seule).mp3`. On écrit en MP3 320 kb/s, ou en FLAC (ou WAV) si l'on change l'extension.
  L'écriture est atomique, sous `nice`. Ces fichiers sont écartés de la liste des sorties de `doubler`, et partent à
  la corbeille avec les fichiers de leur voix.

## Paroles

Sous le lecteur s'affichent la ligne en cours et la suivante, synchronisées sur la lecture. Le mot en cours est
souligné quand `doubler` donne les mots, et un clic sur une ligne va à son début. Le choix « Originale », « Français »
ou « Les deux » (la traduction sous chaque ligne) n'apparaît que s'il y a deux langues ; il est gardé dans
`interface.json` (`paroles_langue`). L'interrupteur « Paroles » se trouve dans le menu « œil » et dans les Réglages
(`paroles`, allumé par défaut).

- **`travail/<voix>/paroles.json`**, écrit par `doubler` (format 1) ; `mots` et `traductions` sont facultatifs :

  ```json
  {"format": 1, "langue": "es", "source": "whisper-large-v3",
   "segments": [{"debut": 64.5, "fin": 66.0, "texte": "…", "mots": [{"debut": 64.5, "fin": 64.8, "mot": "…"}]}],
   "traductions": {"fr": {"source": "<modèle>", "segments": [{"debut": 64.5, "fin": 66.0, "texte": "…"}]}}}
  ```

  Les segments ou les mots abîmés sont ignorés un par un. Le fichier n'est jamais modifié.
- **Repli : les sous-titres de la vidéo doublée.** C'est le cas des doublages sans `paroles.json`. Chaque flux de
  sous-titres en texte (mov_text, subrip, ass…) est extrait par `ffmpeg -map 0:s:N -f srt`, sous `nice`, dans
  `~/.cache/voix-la/sous-titres/` (20 Mo au plus). L'affichage indique leur langue (tag `language`) et s'ils sont
  « forcés ». Avec deux flux, dont un en français, le choix de langue les montre ensemble. Les sous-titres en image
  (PGS, DVD) ne sont pas lus. Rien n'est écrit à côté de la vidéo.

## Visualisations du son

Chacune s'allume ou s'éteint dans le menu « œil » d'un résultat (ou dans les Réglages) ; le choix est gardé dans
`interface.json`.

| Visualisation | Par défaut | Ce qu'elle montre |
|---|---|---|
| **Courbe de mélodie** | allumée | La voix d'origine (trait fin), la consigne, c'est-à-dire l'origine décalée du décalage de chaque passage (pointillés), et la voix convertie (trait plein, aux couleurs de la frise d'octave). Fenêtre de 12 s qui suit la lecture, échelle en notes (do, ré… ; la3 = 440 Hz) ou en Hz, clic pour aller à un moment. |
| **Spectrogramme** | éteint | Défile avec la lecture et montre la piste écoutée (doublage, son d'origine, beat seul, ma voix seule, voix d'origine). Échelle de fréquence logarithmique, couleurs lisibles en clair comme en sombre. |
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
- **Préférences de l'interface** : `~/.config/voix-la/interface.json` (réglages par défaut, visualisations, paroles et
  leur langue, vidéos sources choisies à la main). L'interface ne touche pas au `config.json` de
  `doubler`, sauf par `doubler --config`.
- **Caches** (`~/.cache/voix-la/`, `VOIXLA_CACHE` pour les essais), chacun purgé à part, les fichiers les moins
  récemment utilisés d'abord : `pistes/` (analyses du son, 500 Mo), `voix/` (ma voix seule, 300 Mo), `sous-titres/`
  (20 Mo).
- **Beat et voix seule enregistrés** : à côté des sorties, `<titre> - voix <Nom> (beat).mp3` et
  `<titre> - voix <Nom> (voix seule).mp3` (ou ailleurs, par le sélecteur).
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
| `VOIXLA_CACHE` | caches : analyses du son, ma voix seule, sous-titres |
| `VOIXLA_DOUBLER` | commande `doubler` |
| `VOIXLA_KAGGLE` | commande `kaggle` |
| `VOIXLA_MICRO_ESSAI=1` | son de synthèse au lieu du micro |
| `VOIXLA_APP_ID` | instance distincte de l'application |

## Limites

- **Enregistrement** : seulement par un micro du PC (PulseAudio/PipeWire) ; pas de micro de téléphone.
- **Pistes en vidéo** : chaque piste écoutée ouvre son propre lecteur, et ils restent ouverts jusqu'à ce qu'on quitte
  le résultat. Si la vidéo source a été déplacée, l'original se lit sans image, d'après `travail/original.wav`.
- **Ma voix seule** : le limiteur du mix agit sur la musique et la voix ensemble. Quand la musique le fait travailler,
  la voix du mix est un peu plus comprimée que la voix seule, où il ne voit que la voix.
- **Paroles tirées des sous-titres** : ce sont ceux de la vidéo, souvent une traduction, et pas forcément le texte
  chanté. Sur un extrait, seuls les sous-titres copiés par `doubler` sont présents.
- **Renommer l'identifiant d'une voix** : les doublages déjà faits gardent l'ancien dossier `travail/<ancien>/`, et
  leur remix ne retrouve plus la voix. Changer seulement le nom affiché ne pose pas ce problème.
- **Fenêtre fermée** : les travaux en attente démarrent quand même, car l'exécuteur détaché les enchaîne. Ils ne
  s'affichent qu'à la réouverture.
