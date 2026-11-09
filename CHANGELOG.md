# Journal des modifications

Toutes les modifications notables de ce projet sont consignées ici.

Le format suit [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/), et le projet respecte le
[versionnage sémantique](https://semver.org/lang/fr/).

## [Non publié]

### Ajouté

- Corriger à la main qui chante quoi dans un duo (une voix par chanteur). Le noyau convertit chaque passage dans les
  deux voix cibles, voix principale, chœurs et secours compris (`--versions-duo toutes|utiles|non`, clé `versions_duo`,
  `toutes` par défaut), et rapatrie les deux versions dans `travail/<a+b>/versions/` ; le doublage automatique reste
  identique au bit près. `doubler --remix --passage N=VOIX` (`N-M`, `N,M`, `N/piste=VOIX` ; `deux`, `auto`) et
  `--affectation FICHIER|auto` recomposent la voix convertie sur le PC, sans Kaggle, en quelques secondes ;
  l'affectation est gardée dans `travail/<a+b>/affectation.json`, un remix sans option la garde, `--affectation auto`
  rend le résultat du noyau au bit près. `rapport.json`, `courbes.json` (`voix_par_trame`) et la vidéo à cinq pistes la
  suivent. Mesuré sur un extrait de 55 s : +107 % de conversion (environ 3 min de GPU), session de 8,2 min.
- Interface : la frise « Voix de chaque passage » se corrige. Un clic sur un passage ouvre un menu (Chanté par A, par B,
  Les deux, Automatique, Aller à ce passage) ; « Appliquer » refait le doublage par un remix en tâche de fond ;
  « Revenir à l'automatique » ; les passages corrigés à la main sont marqués. Grisé, avec la raison, si le doublage n'a
  pas les deux versions ou si `doubler` ne connaît pas `--affectation`.
- Clés de configuration `envoi_flac` (16 ou 24) et `versions_duo`.

- Interface : page « Karaoké » (Alt+7 ; « Chanter… » dans le menu « ⋮ » de la bibliothèque). On chante sur le beat
  seul d'un doublage, les paroles défilent en karaoké après un décompte ; chanson entière, extrait ou extrait en
  boucle. Prise au micro du PC, beat et micro dans un même pipeline GStreamer ; latence du matériel réglable en ms ou
  mesurée par quatre bips. Note de justesse sur 100, par ligne et au total (écart ramené dans l'octave, médiane sur
  100 ms, calage cherché dans ±160 ms), courbe « ta voix contre l'original », lignes fausses. Réécoute, garder ou jeter
  la prise, mix en MP3 dans `<dossier du doublage>/karaoke/`. Calculs sous `nice`, par `file-attente` au-delà d'une
  minute de son.
- `python3 -m voixla.karaoke analyser|mp3` : la note et le mix en ligne de commande.

### Modifié

- Remise sur la vidéo : chaque son est encodé à part, en parallèle, puis multiplexé sans réencoder (4 min, cinq pistes :
  23 s au lieu de 62 s ; son décodé identique au bit près, calage inchangé ; en mkv, toute la vidéo peut glisser d'un
  bloc de 23 ms au plus).
- Le son part à Kaggle en FLAC 16 bits (6 Mo au lieu de 20 Mo en WAV sur un extrait de 55 s ; erreur à 88 dB sous le
  signal) ; `envoi_flac 24` revient à l'ancien envoi.
- Les `.srt` des paroles vont dans un sous-dossier `paroles/` de la sortie, pour que VLC et mpv ne les chargent plus
  d'office ; le `.lrc` reste à côté du mp3. `--remix` range ceux d'un ancien doublage ; la corbeille de l'interface les
  emporte avec la voix.
- L'estimation de durée compte les deux versions de chaque passage en duo.

### Corrigé

- mp4, m4v et mov : la piste de paroles n'est plus activée d'office (mpv l'affichait) : le drapeau « enabled » posé par
  ffmpeg sur la première piste de sous-titres est effacé quand aucune piste d'origine n'est « par défaut ».

## [0.10.0] - 2026-11-07

### Ajouté

- Interface : « Partager… » (en-tête du détail d'un doublage, menu « ⋮ » de la bibliothèque) fait une vidéo courte
  prête à partager (TikTok, Reels, Shorts, statut WhatsApp). Extrait de 15 à 60 s repris de la boucle A-B du lecteur,
  ou proposé (le refrain, sinon le passage le plus chanté) ; vertical 9:16, carré 1:1 ou format d'origine, image
  entière sur fond flou ou recadrée ; paroles karaoké incrustées (sous-titres ASS à effet `\kf`, même calage que le
  lecteur), traduction sous la ligne en option ; piste son au choix (doublage, auto-tune, beat seul, ma voix seule) ;
  mention « Voix générée par IA » allumée par défaut ; fondus du son et de l'image ; H.264 et AAC en mp4 faststart,
  40 Mo au plus pour 60 s. Aperçu d'une image ; export en tâche de fond par `file-attente`, avec avancement et
  annulation, dans `<dossier du doublage>/partage/` ; « Ouvrir le dossier ». Les vidéos à partager partent à la
  corbeille avec les fichiers de leur voix.
- `python3 -m voixla.export_social` : le même export en ligne de commande (`--refrain`, `--proposer`, `--apercu`,
  `--ass`).

### Corrigé

- Interface : des sous-titres semblaient incrustés sur la vidéo, et s'affichaient en double avec les paroles de
  l'interface (plein écran, « Sous-titres sur la vidéo »). Rien n'était incrusté dans le fichier : GStreamer dessinait
  de lui-même la première piste de sous-titres de la vidéo (les paroles ajoutées par `doubler`, en mp4 comme en mkv, ou
  les sous-titres d'une vidéo d'origine), le drapeau `text` de playbin étant actif par défaut. Le lecteur ouvre
  désormais chaque flux sans ce drapeau : les seules paroles sur l'image sont celles de l'interface, même après une
  bascule de piste, un déplacement, la vidéo d'origine ou une réouverture.
- Estimation de la durée d'une session à une carte (`--gpu 1`) : 18,5 min annoncées pour 22,8 sur une chanson de
  2 min 49 doublée le 06/11 (−19 %), contre −8 % à deux cartes sur la même chanson. La conversion en manquait 3 min :
  le débit médian des sessions passées (0,067 s par pas et par seconde de son) pour une chanson aux chœurs partout,
  convertis comme une seconde piste entière (0,099 mesurés), et des sessions à 50 pas comptées comme si la conversion
  était proportionnelle aux pas (50 pas en prennent 1,3 fois 30). Le reste : nettoyage 30 s, paroles plus denses
  40 s. Désormais, la conversion se mesure sur les sessions au même nombre de pas, et sur la même chanson si elle a
  déjà été doublée (même durée de son à 0,05 s près, plus d'une minute). À une carte, le temps de GPU noté par une
  session à deux cartes (cartes additionnées, sans la lecture ni l'écriture des pistes) compte 1,09 fois pour le
  nettoyage et 1,04 fois pour YingMusic-SVC, et ces deux étapes coûtent 3 et 13 s de plus que leur temps de GPU et
  leur chargement, oubliées jusqu'ici. Même chanson, même historique : 21,5 min annoncées à une carte (−6 %), 15,2 à
  deux (0 %, au lieu de −8 %). Rejouée sur les treize sessions du 04 au 06/11 (chacune d'après les autres) : écart
  médian +7 % à une carte comme avant, extrêmes −12 à +28 % (avant −18 à +20 %) ; à deux cartes −17 % au lieu de
  −21 %, la session au noyau actuel passant de −7 à +1 %.

## [0.9.0] - 2026-11-06

### Ajouté

- Interface : suivi du quota GPU Kaggle. La page État montre les heures utilisées et restantes de la semaine (jauge),
  la remise à zéro (samedi 00:00 UTC), les sessions comptées sur le poste et ce qui échappe au compte. Les chiffres
  viennent de `kaggle quota` quand il répond, sinon du compte local des sessions (rapports, file des travaux, sessions
  de `doubler` même lancées en ligne de commande, outils préparés, banc), chaque session comptée une fois. La page
  Doubler annonce « ce doublage prendra environ X min de GPU ; il restera Y », avec une alerte sous 2 h. Un doublage
  qui ne tient plus peut attendre la remise à zéro dans la file : il part tout seul à l'heure dite, même fenêtre fermée
  (« Lancer maintenant » dans la page Travaux), ou être lancé quand même. Réglages : quota et remise à zéro de repli,
  seuil d'alerte, comportement par défaut (Demander). Point d'accroche d'un secours (ancien PC, RunPod), rien de
  branché.
- Banc d'essai vitesse : extraits tirés des vidéos, variantes de précision et de recouvrement, de pas, de référence et
  d'égalisation, une suite d'étapes par carte, verrou Kaggle de doubler, `banc.py rapport`.

### Modifié

- Noyau : Roformer de séparation, karaoké et dereverb en demi-précision (autocast fp16). Sur une chanson de 2 min 49,
  le noyau passe de 852 à 656 s : nettoyage 351 → 137 s de GPU, séparation 116 → 60 s. Réglages
  `separation_precision`, `nettoyage_precision` (`autocast`, `fp16`, `fp16+compile`, `fp32`) et
  `separation_recouvrement`. Un son non fini en demi-précision est refait en fp32.

## [0.8.0] - 2026-11-06

### Ajouté

- Interface : la page Résultats devient une bibliothèque, en grille de cartes ou en liste triable par colonne (choix
  gardé). Chaque carte a une image de la vidéo doublée (16:9, préparée en tâche de fond sous `nice -n 19` et
  `ionice -c3`, deux à la fois, celles de l'écran d'abord, dans le cache des miniatures ; la pochette d'un MP3 ; sinon
  une pochette générée d'après le titre), le titre, les voix, la durée, la date et des badges (auto-tune, duo, extrait,
  langue des paroles, parole). Un bouton lit le doublage sans ouvrir le détail (son seul tant qu'on ne le regarde pas,
  puis la vidéo au même instant). Les versions d'une même chanson forment une carte « 2 versions », avec une pastille
  par voix. Tri (date, titre, voix, durée, mode), filtres (voix, chant ou parole, entier ou extrait, auto-tune, duo)
  avec des puces, recherche sans accents ; menu « ⋮ » et mode sélection dans les deux vues ; Ctrl+F, Espace, Suppr,
  Ctrl+A ; état vide avec « Doubler une vidéo » ; fenêtre étroite de 360 px. Avec 200 doublages : 77 ms au plus sans
  rendre la main au chargement, défilement sous 16 ms par image.
- `python3 -m voixla.pistes miniature FICHIER --large` : la miniature 16:9, ou la pochette intégrée d'un MP3.
- Interface : auto-tune dans le remix rapide des Résultats. Interrupteur « Version auto-tune », allumé si le
  doublage en a une, avec l'intensité (celle de `rapport.json`, sinon le défaut de la configuration, sinon 70) et la
  gamme (celle du rapport si elle avait été imposée, sinon Auto) de la page Doubler. Allumé : `--remix --autotune N`
  (et `--autotune-gamme`) ; éteint : `--remix --sans-autotune` (une version existante va à la corbeille ; sans version,
  la clé `autotune` de la configuration n'en refait pas). Grisé sans x42-plugins ou avec un doubler sans `--autotune`.
  Après le remix, la bascule Auto-tune (touche T) apparaît ou disparaît.
- Interface : enregistrement au micro d'un téléphone Android, pour une voix comme pour une prise de chant. La liste
  « Micro » propose les téléphones prêts (`adb devices -l`, `ADB_SERIAL` en premier, nom du modèle) ; scrcpy enregistre
  le micro en FLAC sans perte (`--no-video --no-playback --no-window --audio-source=mic-voice-recognition
  --audio-codec=flac --time-limit`, seulement les options que le scrcpy installé connaît), converti en WAV mono 48 kHz,
  24 bits en chant et 16 bits en parole. « Arrêter » envoie SIGINT, comme Ctrl+C. Pas de niveau en direct : le temps
  écoulé, puis le contrôle de qualité habituel. Sans téléphone, la raison (aucun, non autorisé, hors ligne, scrcpy ou
  adb absent) et « Réessayer », avec `adb connect` sur l'adresse sans fil indiquée. La source de la dernière prise est
  gardée (`source_enregistrement`). `VOIXLA_TELEPHONE_ESSAI=1` imite un téléphone pour les essais.
- Interface : plein écran du lecteur d'un résultat (bouton, F ou double-clic ; Échap ou F pour sortir). L'image seule
  sur fond noir, titre et commandes en surimpression masqués avec le pointeur après 3 s sans mouvement, paroles sur
  l'image, lisibles sur toute image ; pour un MP3, la pochette et les paroles en grand. Le lecteur reste celui de la
  page : lecture continue, MPRIS, pistes dans le même flux.
- Interface : lecteur complet. Temps écoulé et restant, volume et muet (M), vitesse de 0,5× à 2× en gardant la hauteur
  (scaletempo ; `[` et `]`), boucle A-B qui survit aux changements de piste (L), ±5 s (flèches), une image en pause
  (`,` et `.`), capture de l'image en PNG (S), touches 1 à 5 pour les pistes. Reprise à la dernière position en
  rouvrant un doublage, aussi depuis la bibliothèque. MPRIS : `Rate` réglable.
- Interface : chaque panneau du détail s'affiche ou se masque (menu « œil » et Réglages) : frise d'octave et « Voix de
  chaque passage » en plus des visualisations et des paroles ; mode cinéma (V) qui les masque tous d'un coup.
- Interface : paroles façon karaoké. Ligne en cours en grand et centrée, précédente et suivante estompées, mot en cours
  rempli au fil de sa durée, transitions douces, traduction sous chaque ligne ; panneau « Toutes les paroles » qui suit
  la lecture (P) ; sous-titres sur la vidéo hors du plein écran ; taille du texte réglable. Calage inchangé.

### Modifié

- Interface : le cache des miniatures passe de 20 à 60 Mo (images 16:9 de la bibliothèque en plus de celles de MPRIS).

### Corrigé

- Interface : fermer le dialogue d'enregistrement pendant le compte à rebours ne lance plus la prise derrière lui.
- Interface : dans le détail d'un résultat, Espace (lecture / pause) pouvait être pris par le bouton qui avait le
  focus, ou avalé par la fenêtre ; il va maintenant au lecteur, et le focus va à l'image à l'ouverture.
- Interface : le détail d'un résultat débordait à droite dans une fenêtre étroite (légende de la mélodie).

### Interne

- Interface : le défaut de la clé `bibliotheque` d'`interface.json` est déclaré dans `chemins.py`
  (`bibliotheque.declarer_preferences` retiré).

## [0.7.0] - 2026-11-06

### Performances

- Noyau : sur deux cartes, les paroles (Whisper large-v3, puis NLLB) tournent sur la carte 1 pendant que la carte 0
  commence la conversion YingMusic-SVC, puis la carte 1 rejoint la conversion : 365 → 324 s de la fin du nettoyage à
  la fin du noyau sur une chanson de 3 min 48, son et paroles identiques au bit près. `--gpu 1` et les machines à une
  carte gardent la chaîne d'avant.
- Noyau : mémoire du GPU au plus fort de YingMusic-SVC et des paroles dans le journal.

### Ajouté

- Interface : pour un doublage dont la vidéo a ses cinq pistes audio (doubler ≥ 0.6.0), Beat seul, Ma voix seule et
  Voix d'origine seule changent de piste audio dans la vidéo doublée elle-même (`current-audio` de playbin) : même
  position, l'image continue, plus aucune copie vidéo (environ 150 Mo de cache en moins par chanson de 4 min, 200 Mo
  avec l'auto-tune). Nouvelle piste entendue en 0,25 s, au même échantillon. Pistes repérées par leur titre d'après
  `pistes_audio` de `rapport.json`, sinon d'après les titres des flux. Sans la vidéo d'origine, Original lit la piste
  Original de la vidéo doublée, sur son image. Avec l'auto-tune, beat et voix d'origine se lisent dans la vidéo
  auto-tune. Les anciens doublages à une piste gardent les copies.
- Interface : « Enregistrer ma voix seule… » extrait la piste de la vidéo (`-map 0:a:N`) au lieu de recombiner la voix.

### Modifié

- Interface : l'estimation de la session Kaggle est celle de `doubler` lui-même (`estimer_noyau_s`, chargée depuis le
  `doubler.py` livré avec l'interface, sans le lancer) : paroles comptées comme lui, une voix par chanteur comprise. À
  deux cartes, l'interface annonçait environ 45 % de trop ; elle annonce maintenant la même durée que `doubler`. Sans
  `doubler.py` utilisable, son propre modèle compte les paroles de la même façon.
- Interface, page Travaux : une fois le noyau lancé, la durée prévue par `doubler` (`estimation_s`) s'affiche (« durée
  prévue par doubler ≈ 17 min », ou « au-delà de la durée prévue ») et le temps restant la suit.
- Estimation de la durée d'une session : à deux cartes avec YingMusic-SVC, les paroles comptent pour
  max(0,5 × P, P − Y) (P : leur durée sur la carte 1, attente du chargement de la carte 0 comprise ; Y : la conversion
  à deux cartes) au lieu de P en entier ; à une carte, en parole ou avec Seed-VC, P comme avant. Part de la conversion
  à deux cartes tirée des sessions passées sans la moitié de `paroles_pendant_conversion_s`. Le reste de la session
  (chargement de la séparation, fin du noyau), compté 60 s, se mesure sur les sessions passées (80 s sans
  historique). Sans historique, paroles : 25 s fixes (90) et 0,45 s par seconde de son (0,25).
- L'estimation lit aussi les doublages du dossier de sorties par défaut : un dossier neuf (`--sortie`) n'est plus
  estimé sur les seuls défauts.
- Interface : la préparation à l'avance de ces doublages se limite à la miniature ; ma voix seule n'est combinée qu'en
  MP3 ou quand on l'écoute (son seul fenêtre fermée).
- Interface : les analyses du son lisent toujours une piste désignée (`-map 0:a:N`), la première d'une vidéo doublée
  (le doublage), au lieu du choix automatique de ffmpeg.
- Étiquette de langue des cinq pistes audio de la vidéo doublée (et de sa version auto-tune) : la langue principale
  des paroles reconnues, et non plus celle du son d'origine. Une chanson française téléchargée avec un son étiqueté
  `eng` a désormais des pistes `fre`. Chanson bilingue : la langue chantée le plus longtemps (durée totale de ses
  lignes, comme la langue de `paroles.json`). Sans paroles (`--sans-paroles`, rien de reconnu, langue inconnue) :
  l'étiquette du son d'origine, comme avant. `--remix` réétiquette les anciens doublages qui ont un `paroles.json`.
  `rapport.json` : `pistes_audio.langue`.

### Corrigé

- Noyau : les paroles manquaient de mémoire GPU sur certains morceaux (`CUDA OutOfMemoryError` en horodatage par mot,
  morceau repassé deux fois en vain puis transcrit sans les mots : 4 min au lieu d'une sur une chanson de 2 min 49). Le
  pipeline de transformers cherche en faisceau (5) et gardait toute l'attention de l'encodeur (2,7 Gio) et, à chaque
  jeton, l'attention croisée des 32 couches du décodeur : une boucle de Whisper (448 jetons) demandait 16 Gio, plus
  qu'une T4, sur la carte 1 comme sur la carte 0. Whisper ne garde plus que ses têtes d'alignement (7,6 Gio mesurés
  pour une telle boucle), mêmes paroles au bit près ; allocateur à segments extensibles ; mémoire libre vérifiée avant
  le chargement ; un morceau qui en manque encore est coupé en deux plutôt que repassé tel quel. Sur la chanson en
  cause : transcription 253 → 101 s, douze morceaux sur douze en mot à mot, son identique au bit près. Sans morceau
  pour YingMusic-SVC, les paroles passent sur la carte 1, libre, et non plus sur la carte 0, où le noyau garde Seed-VC.
  `rapport.json` : `paroles.memoire_max_mo`, `memoire_passes_mo`, `coupes_memoire`.
- `doubler --sortie` vers un dossier neuf : les outils préparés (`--preparer-outils`) n'étaient pas montés (session
  environ 4 min plus longue). `outils.json` est cherché dans `<sortie>/.kaggle/`, dans `~/.local/state/voix-la/` (où
  `--preparer-outils` le copie, avec le nom du noyau d'outils), puis dans `<données>/sorties/.kaggle/`. Sans outils,
  `doubler` le dit.
- Interface : extrait dont la copie de l'image part d'une image clé antérieure (« image sans son au début ») : le son
  doublé y commence à `decalage_s` de `paroles.json`, et non à `decalage_son` de `source.json` (0 pour un extrait).
  Paroles, frise, courbe et bascules (vidéo ⇄ MP3, son seul, touche B) étaient en avance d'autant (0,48 s dans
  l'essai). La vidéo d'origine garde son propre calage.

## [0.6.0] - 2026-11-05

### Ajouté

- Cinq pistes audio dans la vidéo doublée, calées entre elles et sur l'image à l'échantillon près : Doublage (la
  première, seule par défaut), Original, Beat seul, Voix convertie seule (« Voix converties seules » avec une voix par
  chanteur ; voix sèche et reverb aux gains du mix, même limiteur, sans la musique) et Voix d'origine seule. Titres
  dans `title` et, en mp4, `handler_name` ; même codec que le doublage. La version auto-tune a les mêmes pistes, avec
  la voix auto-tunée. `--remix` refait la vidéo ainsi, sans Kaggle. Environ 25 à 30 Mo de plus pour une chanson de
  4 min. Clé `pistes_audio` et option `--pistes-audio toutes|doublage` pour revenir au doublage seul ;
  `rapport.json` : `pistes_audio`. Si ffmpeg refuse les pistes en plus, la vidéo est faite sans elles.
- Deux GPU : la machine Kaggle `NvidiaTeslaT4` (« GPU T4 x2 ») a deux cartes T4. Le nettoyage (dereverb de la voix
  principale et des chœurs en même temps), la conversion YingMusic-SVC (morceaux répartis) et la séparation d'un lot
  s'y partagent le travail. Le son est identique à celui d'une carte, au bit près. Chanson de 3 min 48 : session GPU de
  26,6 à 19,4 min.
- Clé `gpu` et option `--gpu auto|1|2` ; clé `kaggle.machine` (repli sur `NvidiaTeslaT4` si Kaggle refuse la machine
  demandée).
- Repli automatique sur une carte : machine à une seule carte, ou carte arrêtée en route.
- Interface : réglage « Cartes GPU » (Auto, 1 ou 2), écrit par `doubler --config gpu`, grisé si le `doubler` installé
  ne connaît pas la clé `gpu`.
- Interface : dans le détail d'un résultat à deux cartes, « GPU Kaggle (2 cartes T4) », et le temps de GPU additionné
  des deux cartes pour le nettoyage et YingMusic-SVC (`nettoyage_gpu_s`, `yingmusic_gpu_s`) à côté des durées réelles
  de la session, avec une note qui l'explique.
- Interface : le choix « Une voix pour tous / Une voix par chanteur », le duo (mixte ou même genre) et les deux voix
  choisies sont gardés d'une session à l'autre (`interface.json` : `voix_par_chanteur`, `duo_chanteurs`,
  `voix_chanteurs`). Une voix qui n'existe plus n'est pas présélectionnée.

### Modifié

- Estimation de durée à deux cartes. `rapport.json` : `temps_local.gpus`. `session.json` du noyau : `gpus`,
  `nettoyage_gpu_s`, `yingmusic_gpu_s`. À deux cartes, `temps.nettoyage_s` et `conversion_s` additionnent le temps de
  GPU des deux cartes.
- Interface : estimation de durée à deux cartes. Les doublages nettoyés à deux cartes ont leur propre pente, tirée de
  la durée réelle de la session et non de `temps.total_video_s`, qui additionne les deux cartes et surestimait
  d'environ 30 %. Sans historique : 4,1 s par seconde de son. La page Doubler estime selon les cartes prévues (clé
  `gpu`, machine Kaggle). Les rapports à une carte comptent comme avant.

### Corrigé

- Paroles d'un extrait mkv ou webm : `paroles.json` et les `.srt` écrits à côté des sorties étaient en avance sur la
  vidéo doublée, de 0,13 s (le décalage donné à ffmpeg, alors que le conteneur recale tout sur la première image), et
  d'autant plus quand la copie de l'image partait d'une image clé antérieure (0,96 s mesuré sur un webm VP9). Ils sont
  maintenant au temps de la vidéo doublée, comme ses pistes de paroles, à 2 ms près ; `decalage_s` donne l'instant où y
  commence le son doublé (0 pour un extrait), le `.lrc` reste au temps du mp3. Les pistes de la vidéo, les cinq pistes
  audio et les autres cas ne changent pas. `doubler --remix` recale les paroles d'un ancien doublage.
- Interface : paroles calées sur la piste lue. Pour un extrait mkv ou webm, elles venaient 0,155 s trop tôt sur la
  vidéo et 0,13 s sur le MP3 et les sons seuls ; pour une vidéo entière au son d'origine en retard, 0,15 à 0,18 s trop
  tard sur le MP3 et les sons seuls. L'écart restant est de 25 ms au plus.
- Interface : les notes du panneau des durées (attente d'une place GPU, session partagée) ne s'effacent plus l'une
  l'autre.
- Interface : le choix des voix « Chanteur 1 / 2 » ne glisse plus sur une autre voix quand la liste des voix change.
- Interface : pour une vidéo entière dont le son d'origine est en retard (`decalage_son` de `travail/source.json`), la
  frise d'octave, la courbe de mélodie et les autres visualisations suivent le son en vidéo ; elles avaient 0,18 à
  0,20 s d'avance. Le passage de la vidéo à une piste en son seul (beat ou voix seule sans copie vidéo, bascule
  Vidéo → MP3, son seul fenêtre fermée), puis le retour à la vidéo, gardent le même instant du son ; ils sautaient de
  0,15 à 0,20 s. Un extrait et une vidéo au son à l'heure ne changent pas. Écart restant : 26 ms au plus.

## [0.5.0] - 2026-11-04

### Ajouté

- Auto-tune (`--autotune [N]`, de 0 à 100, 70 sans valeur ; clé de configuration `autotune`, 0 ou absente :
  désactivé). En plus de la version normale, inchangée au bit près : `<titre> - voix <Nom> (auto-tune)` en vidéo (mêmes
  pistes de paroles et sous-titres d'origine), en mp3 et en `.lrc`. Fait sur le PC, sans GPU, par x42-autotune (paquet
  `x42-plugins`, recommandé ; filtre `lv2` de ffmpeg) sur la voix sèche, avant la reverb, au même niveau que la version
  normale. Bas : correction lente et partielle, vibrato gardé ; haut : correction totale, effet robotique (deux passes
  au-delà de 80). Gamme détectée sur la musique seule, avec son accord, repli chromatique si elle est peu sûre ;
  `--autotune-gamme` l'impose (`la-mineur`, `do#-majeur`, `Am`…). Hauteur avant et après dans `rapport.json`
  (`autotune`), `autotune_hz` dans `courbes.json`, champ `autotune` de l'événement `sortie`. Sans le greffon, doubler le
  dit et fait la version normale seule ; en parole, seulement sur demande explicite. `--sans-autotune` l'emporte ; avec
  `--remix`, il met la version auto-tune à la corbeille, et un `--remix` sans option la garde à jour.
- **Une voix par chanteur** : `--voix homme:jules,femme:marie` (duo mixte) ou `--voix chanteur1:jules,chanteur2:marie`
  (duo de même genre, chanteurs pris dans l'ordre d'apparition) donne à chaque chanteur sa propre voix cible.
  - Chaque piste de chaque passage (voix principale, chœurs, piste de secours) prend la voix du chanteur qu'on y
    reconnaît, par sa hauteur et son empreinte : quand les deux chantent ensemble, voix principale et chœurs peuvent
    chanter chacun dans sa voix. Une piste incertaine, ou trop faible (un reste du karaoké), prend la voix du passage.
  - Duo mixte annoncé : les chanteurs peuvent se répondre phrase à phrase, et l'empreinte (nettement d'homme, nettement
    de femme) l'emporte sur la hauteur (un chanteur qui monte à 345 Hz).
  - Octave décidée pour chaque couple chanteur → voix cible, d'après le centre de chant de chaque voix.
  - Une seule voix trouvée : tout prend la voix de sa classe, sinon la première, avec un message ; en parole, la
    première voix seule.
  - Sorties « <titre> - voix Jules et Marie.<ext> », `.mp3`, dossier `travail/jules+marie/` (mêmes noms quel que soit
    l'ordre donné) ; `--remix`, `--reprendre`, les paroles, la piste de secours et l'auto-tune suivent.
  - Rapport : voix cibles, chanteurs et décalage de chaque couple, voix de chaque piste sur chaque passage et sa raison,
    passages par voix ; `courbes.json` gagne `voix_cibles` et `voix_par_trame` (format 1 inchangé) ; l'événement
    `sortie` gagne `voix` (la liste des voix).
  - Surcoût mesuré : environ 3 s par session (une référence de plus par moteur, reconnaissance des pistes).
  - `--voix NOM` ne change pas : avec une seule voix, le résultat est le même qu'avant, au bit près.
- Interface, page Doubler : « Une voix pour tous / Une voix par chanteur », duo mixte (Voix d'homme, Voix de femme) ou
  de même genre (Chanteur 1, Chanteur 2).
- Interface, page Doubler et Réglages : auto-tune (interrupteur, intensité de 1 à 100, 70 par défaut, gamme Auto,
  Chromatique ou une tonalité) ; grisé, avec « installez x42-plugins », si `doubler` ne trouve pas le greffon ; défaut
  de chaque doublage écrit par `doubler --config autotune N`.
- Interface, page Résultats : bascule « Auto-tune » (touche T), à la même position, pour le doublage et la voix seule ;
  « Ma voix seule (auto-tune) » dans les pistes séparées, préparée à l'avance comme les autres ; tonalité détectée et
  écart aux notes avant et après ; courbe de la version auto-tune sur la mélodie (menu « œil » ou Réglages).
- Interface, page Résultats : « Voix de chaque passage » sous la frise d'octave (une couleur par voix, bandeau partagé
  quand les deux chantent ensemble) ; « Voix converties seules » au lieu de « Ma voix seule » pour une voix par
  chanteur ; décision par chanteur dans les informations ; remix avec les mêmes voix.
- Interface : la corbeille emporte aussi la version « (auto-tune) » d'une voix (vidéo, mp3, `.lrc`, voix seule
  enregistrée).

### Modifié

- Choix d'octave : un duo mixte annoncé par `--voix homme:…,femme:…` est cherché sans l'épreuve des sections (les
  chanteurs peuvent se répondre phrase à phrase). Sans voix par chanteur, la règle ne change pas.

## [0.4.0] - 2026-11-04

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

[Non publié]: https://github.com/mawena/Voix-la/compare/v0.10.0...HEAD
[0.10.0]: https://github.com/mawena/Voix-la/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/mawena/Voix-la/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/mawena/Voix-la/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/mawena/Voix-la/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/mawena/Voix-la/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/mawena/Voix-la/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/mawena/Voix-la/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/mawena/Voix-la/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/mawena/Voix-la/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/mawena/Voix-la/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/mawena/Voix-la/releases/tag/v0.1.0
