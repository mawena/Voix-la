# Journal des modifications

Toutes les modifications notables de ce projet sont consignées ici.

Le format suit [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/), et le projet respecte le
[versionnage sémantique](https://semver.org/lang/fr/).

## [Non publié]

### Ajouté

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

[Non publié]: https://github.com/mawena/Voix-la/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/mawena/Voix-la/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/mawena/Voix-la/releases/tag/v0.1.0
