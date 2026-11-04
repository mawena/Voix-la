# Journal des modifications

Toutes les modifications notables de ce projet sont consignées ici.

Le format suit [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/), et le projet respecte le
[versionnage sémantique](https://semver.org/lang/fr/).

## [Non publié]

### Ajouté

- Interface : la musique continue en arrière-plan.
  - Un seul lecteur pour toute l'application : lancer une lecture ailleurs (autre résultat, écoute à l'aveugle,
    référence d'une voix, réécoute d'une prise, extrait à doubler) met la précédente en pause ; changer de piste dans
    un résultat reste une bascule à la même position.
  - Quitter la page n'arrête plus la lecture : on retrouve le résultat tel quel (position, piste, lecture ou pause).
    Suivi et visualisations s'arrêtent quand on ne les voit pas.
  - Barre « en cours de lecture » en bas de la fenêtre : image de la vidéo, titre, voix et piste, position,
    lecture/pause, stop, retour au résultat.
  - Fermer la fenêtre pendant une lecture : l'application reste en vie et la musique continue, sur le seul son de la
    piste (1,5 % d'un cœur au lieu de 24 % pour une vidéo 1080p) ; relancer voix-la rouvre la fenêtre telle quelle ;
    l'application quitte à la fin de la lecture, sur un stop, ou après 15 min de pause.
  - MPRIS (org.mpris.MediaPlayer2.voixla) : la lecture dans le menu du haut de GNOME, et les touches multimédia.
- Interface, page Résultats : « Mettre à la corbeille… » sur chaque ligne (menu « ⋮ ») et mode « Sélectionner » pour
  plusieurs doublages, avec une seule confirmation ; même règle que le détail (une voix : le dossier ; plusieurs : les
  fichiers de cette voix) ; la lecture d'un doublage mis à la corbeille s'arrête d'abord.
- Interface : beat seul, ma voix seule et voix d'origine seule se préparent à l'avance : à la fin de chaque doublage,
  même fenêtre fermée ; dès l'ouverture d'un résultat ; au démarrage pour les 5 derniers doublages, quand le poste
  est calme. Avancement affiché, « Réessayer » en cas d'échec.
- Interface, Réglages : « Taille du cache des pistes » (2 Go par défaut) ; les copies du doublage ouvert ou en lecture
  ne sont jamais purgées.

### Corrigé

- Interface : « Ma voix seule » et les autres pistes n'attendent plus derrière les travaux lourds des autres
  sessions. Leurs préparations, légères, ne passent plus par file-attente : elles tournent tout de suite, en priorité
  basse (nice 19, ionice idle), avec un garde-fou mémoire.
- Interface : un résultat quitté sans lecture ferme ses lecteurs (ils restaient ouverts jusqu'à la fermeture de la
  fenêtre).

### Modifié

- Interface, essais : les captures tournent sur un bus de session privé, sans aucun service (outils/bus-essai.conf).

## [0.3.1] - 2026-11-04

### Corrigé

- Interface, page Résultats : « Beat seul », « Ma voix seule » et « Voix d'origine seule » se lisent maintenant sur
  l'image de la vidéo doublée, qui avance avec le son, au lieu de l'image arrêtée. Chaque piste a sa copie vidéo,
  préparée une fois en cache (`~/.cache/voix-la/videos/`, 2 Go au plus) : l'image copiée sans réencodage, le son de la
  piste calé au même échantillon que le mix. Pendant la préparation (7 à 14 s pour une chanson de 4 min, sous nice),
  le son se lit sous l'image arrêtée, puis le lecteur passe sur la vidéo à la même position. Sans vidéo doublée, ou si
  la copie est impossible, l'écoute reste sans image, avec la raison.
- Interface, lecteur : une bascule faite avant que la piste précédente soit prête ne repart plus du début.

### Modifié

- Interface, page Voix : bouton « Ajouter une voix » bien visible à côté du titre (depuis un fichier audio ou vidéo,
  ou au micro), et glisser-déposer d'un fichier sur la page ; le seul « + » de la barre de titre passait inaperçu.

## [0.3.0] - 2026-11-04

### Ajouté

- **Paroles à chaque doublage** : Whisper large-v3 transcrit la voix principale (sinon la voix isolée), phrase
  chantée par phrase chantée, avec la langue de chaque phrase et l'horodatage de chaque mot ; garde-fous contre les
  mots inventés (silences rendus muets, seuils de Whisper, mots hors du chant, boucles, génériques de sous-titres).
  Traduction française ligne à ligne par NLLB-200 (distillé 600M) quand les paroles ne sont pas en français.
  - `travail/<voix>/paroles.json` (format 1), calé sur la vidéo de sortie (`decalage_s`).
  - Pistes de sous-titres « Paroles (<langue>) » et « Paroles (français, traduction) » ajoutées à la vidéo (mov_text
    en mp4, SubRip en mkv, WebVTT en webm), après celles d'origine, qui sont gardées.
  - `<titre> - voix <Nom>.<langue>.srt`, `.fr.srt` et `.lrc` (paroles synchronisées pour les lecteurs de musique).
  - `--sans-paroles` et la clé de configuration `paroles` (`doubler --config paroles oui|non`) ; `--remix` refait
    la vidéo avec les paroles déjà là, sans GPU.
  - L'événement `sortie` donne `paroles`, `srt` et `lrc`.
- **Passages perdus au nettoyage** : un passage que voix principale et chœurs secs ont perdu (souvent un « oooh » de
  fond très réverbéré, jeté par le dereverb) est repris de la voix complète par une troisième piste, « secours »,
  mêmes passages et mêmes décalages, porte ouverte seulement là. Zones dans `rapport.json` (`secours`). Rien de
  perdu : rien ne change.
- `kaggle/secours_local.py` : rejoue cette détection sur le PC, sans GPU, sur un doublage déjà fait.
- `--preparer-outils` prépare aussi Whisper large-v3 et NLLB-200 (environ 5,6 Go de plus).
- Essais `tests/` : détection des passages perdus, garde-fous des paroles, `.srt` et `.lrc`, calage des extraits,
  cas abîmés, mise en vidéo (ffmpeg).
- **Règle d'octave v2.3** : ancres de genre bâties sur un corpus public de parole lue (`octave/ancres.py`, aucune voix
  chantée ni voix cible) ; frontière « hauteur seule » fixe à 281 Hz ; empreinte de femme prise en compte dès 255 Hz.
  Un chanteur à voix haute ou éraillée n'est plus pris pour une femme.
- Interface, page Résultats : choix de ce qu'on écoute à la même position : Doublage, Original, Beat seul, Ma voix
  seule, Voix d'origine seule (touche B : doublage ⇄ original). « Ma voix seule » sonne comme dans le mix : voix sèche
  et réverbération aux gains du rapport, même limiteur, préparée en cache sous nice. Sans image, une piste se lit sous
  l'image arrêtée de la vidéo.
- Interface : « Enregistrer le beat… » et « Enregistrer ma voix seule… », en MP3 ou FLAC, nommés
  `<titre> - voix <Nom> (beat).mp3` et `<titre> - voix <Nom> (voix seule).mp3`.
- Interface : paroles synchronisées sous le lecteur (ligne en cours avec le mot en cours, ligne suivante ; langue
  d'origine, français ou les deux), depuis `travail/<voix>/paroles.json`, sinon les sous-titres de la vidéo doublée.
- Interface : interrupteur « Paroles » dans la page Doubler (`--sans-paroles`) et réglage par défaut
  (`doubler --config paroles oui|non`).
- Interface, écoute à l'aveugle : interrupteur « voix seule » (touche V).

### Modifié

- Interface : sans la vidéo d'origine, l'original se lit d'après `travail/original.wav`, sans image, au lieu de
  désactiver la bascule A/B.
- Interface : le spectrogramme montre la piste écoutée.
- L'estimation de la durée de session compte les paroles.
- En mkv, les pistes de sous-titres ajoutées ne s'affichent pas d'office.

## [0.2.0] - 2026-11-04

### Ajouté

- **Paquet Debian** `voix-la_<version>_all.deb` (Ubuntu 24.04 ou plus récent), construit par `make deb` :
  - commandes `/usr/bin/doubler` et `/usr/bin/voix-la`, code dans `/usr/lib/voix-la/` ;
  - entrée « Voix-là » du menu des applications, icône, pages de manuel `doubler(1)` et `voix-la(1)` ;
  - `scripts/build-deb.sh` vérifie les versions, lance les tests, range le paquet et `SHA256SUMS` dans `dist/`,
    puis passe `lintian`.
- Lanceurs `bin/doubler` et `bin/voix-la` : le code du dépôt, sinon celui du paquet.
- `doubler --version`.
- `VOIXLA_KAGGLE` choisit la commande `kaggle` de `doubler`, comme pour l'interface.
- `doubler --config` affiche la commande `kaggle`, le script du noyau et le fichier des ancres de genre utilisés.

### Modifié

- Les ancres de genre sont cherchées d'abord dans `~/.config/voix-la/ancres_genre.json`, puis dans
  `kaggle/ancres_genre.json` à côté de `doubler.py`.
- L'interface trouve son code, `doubler.py` et le noyau une fois installée dans `/usr/lib/voix-la`.

### Corrigé

- Interface : un doublage fini pouvait rester affiché « interrompu », avec une barre rouge. L'interface voyait
  le processus déjà terminé avant que l'exécuteur n'ait écrit « fini », et ne revenait plus sur cette conclusion.

## [0.1.0] - 2026-11-04

Première version publique.

### Ajouté

- **`doubler`** : remplace la voix d'une vidéo (chanson, duo, rap ou vidéo parlée) par une voix clonée, et rend la
  même vidéo et son audio.
  - Calcul sur le GPU gratuit de Kaggle, dans un jeu de données et un noyau privés.
  - Mix en local, avec ffmpeg et numpy.
  - Plusieurs vidéos dans une seule session, chacune avec son propre extrait (`vidéo@60+40`).
  - Reprise après une coupure réseau (`--reprendre`), avec un suivi qui tolère jusqu'à 3 h de coupure.
  - Verrou commun à la machine, pour ne jamais lancer deux sessions à la fois.
- **Chaîne de chant** : séparation MelBand-Roformer, puis nettoyage de la voix.
  - Le nettoyage sépare la voix principale des chœurs (karaoké) et retire la réverbération.
  - Conversion par YingMusic-SVC (par défaut) ou Seed-VC 44 kHz (`--moteur`), avec repli automatique sur Seed-VC.
  - `--sans-nettoyage` convertit la voix isolée complète.
- **Parole** : conversion Seed-VC 22 kHz.
- **Mode** : choix automatique entre chant et parole. Le rap n'est retenu que sur un fond musical tonal.
- **Règle d'octave** : un seul décalage par chanteur, décidé d'abord par la hauteur.
  - Une empreinte de voix facultative départage la zone ambiguë.
  - Les duos sont retenus à quatre conditions, avec des changements placés aux pauses.
- **Mix v2** :
  - suivi des nuances de la voix d'origine ;
  - dé-esseur ;
  - réverbération reprise de la voix d'origine ;
  - limiteur sur la crête vraie à −1 dBTP.

  `--mix-v1` garde l'ancien mix. `--remix` refait le mix sans repasser par le GPU.
- **Transport FLAC** 24 bits, dans les deux sens, avec un gain connu.
- **Sorties de chaque doublage** :
  - le rapport détaillé `rapport.json` ;
  - les courbes de mélodie `courbes.json` ;
  - des événements JSONL (`--evenements`) pour suivre le travail depuis un autre programme.
- **Configuration** dans `~/.config/voix-la/config.json` (`--config`) : compte Kaggle, dossier de données, moteur et voix par défaut.
- **Interface graphique** GTK4 et libadwaita (`voix-la`) :
  - voix, doublages, travaux, résultats et état de la machine ;
  - écoute à l'aveugle ;
  - bascule original ⇄ doublé à la même position ;
  - courbe de mélodie ; en option, spectrogramme, forme d'onde et vu-mètre ;
  - lanceur, entrée de menu et icône (`interface/installer.sh`).
- **Banc d'essai** (`banc/`) : compare plusieurs chaînes de conversion sur les mêmes extraits, avec des mesures
  (justesse, intelligibilité, ressemblance du timbre, aigus) et une écoute à l'aveugle.
- **Outils de mise au point** de la règle d'octave (`octave/`) : rejouer la décision sur des rapports existants, sans GPU.

### Corrigé

- La musique seule gardait la voix d'origine. Avec ffmpeg 6.1.1, `amix` ignore le signe des poids, si bien que la
  voix d'origine restait 6 dB au-dessus. La soustraction se fait maintenant en numpy, avec trois garde-fous.
- Les extraits de fichiers mkv commençaient par une image figée.
- Le limiteur décalait le son de 5 ms.
- Interface :
  - le décodage vidéo matériel Intel (VA-API) faisait planter la lecture. Le décodage est maintenant logiciel par défaut ;
    `VOIXLA_DECODAGE_MATERIEL=1` remet le décodage matériel ;
  - les essais automatiques laissaient le son de l'application coupé.

[Non publié]: https://github.com/mawena/Voix-la/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/mawena/Voix-la/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/mawena/Voix-la/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/mawena/Voix-la/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/mawena/Voix-la/releases/tag/v0.1.0
