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
| **Doubler** | Choisir une ou plusieurs vidéos, par le sélecteur (il ouvre `Vidéos/` des données, sinon le dossier Téléchargements) ou par glisser-déposer. Pour chaque vidéo, un extrait facultatif se règle dans un petit lecteur (« Début ici », « Fin ici », écouter l'extrait). Réglages : voix pour tous, **Comparer plusieurs voix** (cases à cocher, deux à six voix, gardées dans `interface.json`, clé `voix_comparees` ; voir [Comparer des voix](#comparer-des-voix)) ou **une voix par chanteur** (duo mixte : Voix d'homme et Voix de femme ; duo de même genre : Chanteur 1 et Chanteur 2), gardée d'une session à l'autre dans `interface.json` (`voix_par_chanteur`, `duo_chanteurs`, `voix_chanteurs` ; une voix qui n'existe plus n'est pas présélectionnée), mode, octave, l'interrupteur **Transposer la chanson dans ma tessiture** (visible si `doubler` annonce `--transposer`, allumé par défaut, avec une info-bulle : la chanson hors de la tessiture de la voix est transposée en entier, musique comprise, de 5 demi-tons au plus ; éteint, il ajoute `--transposer=non` ; la case n'est pas gardée d'une session à l'autre), pas, niveau de la voix, reverb, ancien mixage v1, et, si `doubler` les connaît, la chaîne de conversion : « Par défaut » (voix principale et chœurs séparés, reverb retirée, YingMusic-SVC), « Chaîne d'avant » (Seed-VC, sans nettoyage, plus rapide) ou « Sur mesure » (moteur et nettoyage au choix), et l'interrupteur **Paroles** (transcription et traduction française ; éteint, il ajoute `--sans-paroles` ; allumé par défaut, d'après la clé `paroles` de la configuration de `doubler`), et l'**auto-tune** : interrupteur, intensité (1 à 100, 70 par défaut) et gamme (Auto, Chromatique ou une tonalité), d'après la clé `autotune` de `doubler` (éteint alors que la configuration l'allume, il ajoute `--sans-autotune`) ; grisé avec « installez x42-plugins » si `doubler` ne trouve pas le greffon. La durée de la session Kaggle est celle que `doubler` calcule lui-même (sa fonction `estimer_noyau_s`, appelée depuis le `doubler.py` livré avec l'interface, sur les rapports du dossier des sorties) : la même qu'il annoncera au lancement. Elle suit la chaîne, le moteur, les pas, les paroles, une voix par chanteur, le mode et les cartes GPU prévues (clé `gpu` et machine Kaggle de `doubler`) ; à deux cartes avec YingMusic-SVC, les paroles, faites sur la carte 1 pendant la conversion, comptent pour max(0,5 × P, P − Y). Sans `doubler.py` utilisable, l'interface garde son propre modèle, qui compte les paroles de la même façon. Le reste (extraction, envoi, rapatriement, mixage, vidéo) vient des doublages passés. À côté de « Lancer », la ligne du quota GPU : « Ce doublage prendra environ 18 min de GPU ; il restera 8 h 12 cette semaine », en orange s'il ne tient pas (voir [Quota GPU Kaggle](#quota-gpu-kaggle)), et une bannière quand il reste moins de 2 h. Puis le bouton « Lancer » envoie le travail dans la file. |
| **Travaux** | La file. Un seul `doubler` tourne à la fois. Les étapes s'affichent en direct, avec le temps écoulé et le temps restant estimé, l'état du noyau Kaggle et le journal complet dépliable. Avant le lancement, l'estimation est celle de la page Doubler ; une fois le noyau lancé, c'est celle de `doubler` (`estimation_s` de ses événements) : « durée prévue par doubler ≈ 17 min » (« au-delà de la durée prévue » si le noyau la dépasse), et le temps restant la suit. On peut annuler un travail. « Voir le résultat » ouvre le doublage fini. Un travail en attente du quota GPU porte le badge « Attend le quota », l'heure où il partira tout seul (remise à zéro), et « Lancer maintenant ». Une section « Sur Kaggle » en tête montre la file du compte (voir [File d'attente Kaggle](#file-dattente-kaggle)). Voir [Reprise et abandon d'une session Kaggle](#reprise-et-abandon-dune-session-kaggle). |
| **Résultats** | La **bibliothèque** des doublages, en **grille** de cartes ou en **liste** triable par colonne (titre, voix, mode, durée, date) ; les deux boutons de l'en-tête (ou le menu « Trier et filtrer ») basculent de l'une à l'autre. Une carte montre une image de la vidéo doublée en 16:9 (sans vidéo, la pochette intégrée au MP3 ; sinon une pochette générée : un dégradé tiré du titre et une note), le titre sur deux lignes au plus, les voix, la durée, la date et des badges : auto-tune, duo (une voix par chanteur), extrait, langue des paroles, parole, inachevé. Le bouton de lecture (au survol, toujours visible sur écran tactile ; Espace sur la carte qui a le focus) lit le doublage dans la barre « en cours de lecture », sans ouvrir le détail ; un second clic le met en pause. Un clic sur la carte ouvre le détail, celui qui joue étant retrouvé tel quel. Les **versions d'une même chanson** (même vidéo source, voix différentes) forment une seule carte « 2 versions » : une pastille par voix ouvre sa version (« +N » au-delà de trois) ; « Regrouper les versions d'une chanson » (menu « Trier et filtrer ») les déplie. Le menu « Trier et filtrer » trie par date, titre, voix ou durée (« Ordre inverse »), et filtre par voix, chant ou parole, entier ou extrait, avec auto-tune, une voix par chanteur ; chaque filtre actif a sa puce sous l'en-tête, qui l'efface. La recherche (Ctrl+F, ou en tapant) trouve un titre, une voix, une langue ou un sous-dossier, sans accents ni casse ; le compte de l'en-tête dit « 18 doublages · 12 chansons », ou « 5 sur 18 doublages » quand on filtre. La vue, le tri et le regroupement sont gardés dans `interface.json` (clé `bibliotheque`). Chaque carte ou ligne a un menu « ⋮ » : ouvrir une version, « Partager… », « Ouvrir le dossier » et « Mettre à la corbeille… » (toutes ses versions pour une chanson) ; le mode **Sélectionner** (bouton coche de l'en-tête, ou Ctrl+A ; Échap pour en sortir) coche plusieurs cartes ou lignes d'un clic et les met à la corbeille d'un coup (bouton, ou Suppr ; voir [Corbeille](#corbeille)). Voir [Bibliothèque des résultats](#bibliothèque-des-résultats). Le détail d'un doublage montre : la lecture de la vidéo ou du MP3 et le **choix de ce qu'on écoute, à la même position** : Doublage, Original, Beat seul, Ma voix seule, Voix d'origine seule (voir [Pistes séparées](#pistes-séparées) ; touche B : doublage ⇄ original) ; la bascule **Auto-tune** (touche T) passe au doublage et à la voix seule auto-tunés, à la même position ; les **paroles** synchronisées (ci-dessous) ; la frise d'octave, où un clic va à ce moment, et, avec une voix par chanteur, la frise **Voix de chaque passage** (une couleur par voix, bandeau partagé quand les deux chantent ensemble, **corrigeable** : voir [Corriger qui chante quoi](#corriger-qui-chante-quoi)) ; les **visualisations du son** (ci-dessous) ; les informations (moteur réellement utilisé et repli éventuel sur Seed-VC, nettoyage appliqué ou sa raison avec les niveaux de la voix principale, des chœurs et de la reverb retirée, décision d'octave et sa raison, la ligne **Transposition** (« Chanson transposée de −3 demi-tons pour ta voix », avec les médianes avant et après et la zone ; « Chanson non transposée » sinon ; un avertissement si la musique n'a pas pu être transposée, faute de Rubber Band), décision par chanteur avec une voix par chanteur, tonalité détectée et écart aux notes avant et après l'auto-tune) ; les pistes séparées, avec « Enregistrer le beat… » et « Enregistrer ma voix seule… » (et « Ma voix seule (auto-tune) », enregistrable aussi) ; le remix rapide, qui redonne les mêmes voix, avec l'interrupteur **Version auto-tune** (intensité et gamme comme sur la page Doubler, pré-remplies d'après `rapport.json`) : allumé, la version « (auto-tune) » est faite ou refaite (`--autotune N`, `--autotune-gamme`) ; éteint, `--sans-autotune` (une version existante va à la corbeille, et la clé `autotune` de la configuration n'en refait pas) ; grisé sans x42-plugins ; après le remix, la bascule Auto-tune (touche T) apparaît ou disparaît ; les durées par étape (nettoyage, YingMusic-SVC, estimation de doubler comprise ; à deux cartes, « GPU Kaggle (2 cartes T4) », avec le temps de GPU des deux cartes additionnés à côté des durées réelles de la session) ; l'ouverture du dossier, « Partager… » (voir [Partager un extrait](#partager-un-extrait)) et la mise à la corbeille. Si la vidéo source a changé de place, « Choisir… » l'indique à la main (`travail/source.json` n'est pas modifié) ; en attendant, l'original se lit sans image, d'après `travail/original.wav`. |
| **Karaoké** (Alt+7) | Chanter soi-même sur le beat seul d'un doublage, avec les paroles en karaoké : voir [Karaoké](#karaoké). |
| **Voix** | Les voix : nom, durée de la référence, hauteur de parole, alertes. On peut écouter la référence, choisir la voix par défaut, ajouter une voix depuis un fichier (`doubler --ajouter-voix`) ou au micro (texte à lire, compte à rebours, contrôle de qualité), la renommer ou la mettre à la corbeille. La page gère aussi les prises de chant pour l'affinage, avec le total cumulé face à l'objectif de 5 à 10 min. Le bouton **Enregistrer ma voix chantée…** ouvre la **séance d'enregistrement** (voir [Séance d'enregistrement](#séance-denregistrement)). La liste « Micro » de l'enregistrement propose aussi le **micro d'un téléphone** Android branché (voir [Enregistrer au téléphone](#enregistrer-au-téléphone)). |
| **Écoute à l'aveugle** (hors de la barre latérale ; Alt+5) | Pas d'entrée dans la barre latérale (le banc d'essai servait peu) : la page reste dans l'application et s'ouvre par « Comparer les voix » d'un résultat, par **Écoute à l'aveugle (banc d'essai)…** du menu principal ☰, ou par Alt+5 (aucune ligne de la barre n'est alors sélectionnée). Les versions A, B, C… de chaque extrait du banc d'essai (`banc/ecoute/`) : mix et voix seule. Ouverte depuis « Comparer les voix » d'un résultat, la même page écoute à l'aveugle les voix d'une comparaison (voir [Comparer des voix](#comparer-des-voix)). L'interrupteur **Voix seule** de la barre du bas (touche V) fait passer la version en cours de son mix à sa voix seule (`X_voix.mp3`) et retour, à la même position. Vous notez les versions de 1 à 5. La correspondance lettre → version n'apparaît qu'après votre vote, qui va dans `banc/ecoute/votes.json`. Vu-mètre, spectrogramme et forme d'onde de ce qui joue, si on les allume. |
| **État** | Quota GPU Kaggle : heures utilisées sur le total, jauge, heures restantes, remise à zéro, travaux en attente du quota, sessions comptées sur ce poste et ce qui échappe au compte ; bannière d'alerte. Kaggle : présence du jeton (son contenu n'est jamais lu), compte, statut des noyaux connus. `doubler` : options reconnues, verrou libre ou pris (verrou commun `~/.local/state/voix-la/verrou-kaggle` et `sorties/.kaggle/verrou`). Le poste : RAM, charge, `file-attente --etat`. Kaggle se rafraîchit à la demande, ou toutes les 60 s au plus quand la page est visible. |
| **Réglages** (menu ☰, Ctrl+,) | Dossier de données, compte et noms Kaggle, quota GPU (heures par semaine, jour et heure UTC de la remise à zéro, quand Kaggle ne répond pas ; seuil d'alerte ; doublage qui ne tient plus : Demander, Attendre la remise à zéro ou Lancer quand même ; secours, à venir), moteur du chant, paroles, auto-tune et cartes GPU (Auto, 1 ou 2) par défaut (écrits par `doubler --config`, par exemple `doubler --config paroles non`, `doubler --config autotune 50` ou `doubler --config gpu 1` ; le réglage des cartes est grisé si `doubler` ne connaît pas la clé `gpu`), voix et réglages de doublage par défaut, micro et durées d'enregistrement, autre dossier de prises de chant, visualisations et paroles. |

Raccourcis : Alt+1 à Alt+7 ouvrent les pages (Alt+5 : l'écoute à l'aveugle, qui n'a plus de ligne dans la barre latérale), F5 actualise, Ctrl+, ouvre les réglages, Ctrl+? les raccourcis. Dans un
résultat : Espace lecture / pause, flèches ±5 s, `,` et `.` une image en pause, Origine le début, 1 à 5 les pistes
(doublage, original, beat seul, voix convertie, voix d'origine), B doublage ⇄ original, T avec ou sans auto-tune, L la
boucle A-B, `[` et `]` la vitesse, M le son, F le plein écran (Échap pour sortir), V le mode cinéma, P le panneau
« Toutes les paroles », S une capture de l'image (voir [Lecteur d'un résultat](#lecteur-dun-résultat)). Dans l'écoute
à l'aveugle, V bascule mix ⇄ voix seule. Dans la bibliothèque des résultats : Ctrl+F cherche, Entrée ouvre la carte
qui a le focus, Espace la lit ou la met en pause sans ouvrir le détail (en mode sélection : la coche), Suppr la met à
la corbeille (en mode sélection : la sélection), Ctrl+A sélectionne tout, Échap sort du mode sélection.

## Lecteur d'un résultat

- **Commandes** sous l'image : temps écoulé, barre de progression, temps restant ; lecture / pause, ±5 s, son coupé et
  volume (gardé dans `interface.json`, clé `volume`), boucle A-B, vitesse, capture de l'image, plein écran. Fenêtre
  étroite : le volume, ±5 s et la capture disparaissent d'abord.
- **Vitesse** de 0,5× à 2× (menu ou `[` `]`), **la hauteur est gardée** : le lecteur de GTK passe le son par
  `scaletempo` (filtre audio de son playbin). Mesuré : un sinus à 440 Hz reste à 440 Hz à 0,5× et à 2×, pendant que la
  position avance à 0,502× et 2,011×. Elle vaut pour toutes les pistes, et pour le son seul fenêtre fermée.
- **Boucle A-B** (touche L ou bouton A-B : début, fin, puis effacée) : en temps du son doublé, elle vaut pour toutes
  les pistes. On compare ainsi le doublage et l'original au même passage. Surveillée pendant la lecture seulement
  (40 ms), que la page soit affichée ou non. Mesuré : B dépassé de 7 ms au plus, même après une bascule dans le même
  flux ou vers un autre fichier. Elle est surlignée sur la barre de progression.
- **Image par image** en pause (`,` et `.`) : un pas d'une image, d'après la cadence de la vidéo.
- **Capture** (S ou bouton) : l'image en cours, à sa taille d'origine, en PNG dans `Images/Voix-là/`
  (`<titre> - <piste> - 1m23,45s.png`), écrite dans un fil.
- **Reprise à la dernière position** : en rouvrant un doublage (détail ou bouton de lecture de la bibliothèque), la
  lecture reprend où elle s'était arrêtée, avec un bandeau « Reprise à 1:23 » et « Reprendre au début ». La position
  est retenue à la pause, toutes les 30 s pendant la lecture, en quittant ; pas au tout début (moins de 5 s) ni à la
  fin (moins de 10 s avant), où l'on reprend au début. Clé `positions` d'`interface.json`, 200 doublages au plus.
- **Plein écran** (bouton, F ou double-clic sur l'image ; Échap ou F pour sortir) : l'image seule sur fond noir, le
  titre en haut et les commandes en bas en surimpression, masqués avec le pointeur après 3 s sans mouvement (sauf
  pointeur sur les commandes ou menu ouvert), revenus au moindre mouvement ou à une touche. Les paroles sont sur
  l'image, en bas (texte blanc cerné de sombre, le mot en cours rempli en jaune), au-dessus des commandes quand elles
  sont là. Un MP3 montre sa pochette (ou la miniature de la vidéo, ou la pochette générée) et les paroles en grand. Le
  plein écran garde le lecteur de la page : pistes, vitesse, boucle, MPRIS et lecture continue tels quels. Toutes
  les touches du détail y marchent, et le choix de la piste est aussi dans un menu.
- **Mode cinéma** (V, ou menu « œil ») : frises, visualisations, paroles et informations masquées d'un coup, l'image
  en grand ; V encore pour tout retrouver tel quel.
- **Clavier** : Espace, les flèches et Origine sont pris avant la fenêtre (qui ferait d'Espace « activer le focus » et
  des flèches un déplacement du focus), sauf par ce qui s'en sert : un bouton ou une case qui a le focus (Espace), une
  barre, un texte, une liste. À l'ouverture du détail, le focus est sur l'image. Un clic sur une commande ne lui laisse
  pas le focus.
- **MPRIS** : `Rate` suit la vitesse (`MinimumRate` 0,5, `MaximumRate` 2) et se règle depuis le bureau ; la barre « en
  cours de lecture » montre la vitesse et la boucle (« voix Jules · Beat seul · 1,25× · boucle A-B »).
- **Sobriété** : les commandes se mettent à jour 4 fois par seconde au plus, pendant la lecture et si elles sont
  affichées ; en plein écran, la page cachée ne se redessine plus. Mesures plus bas.

## Lecture en arrière-plan

- **Un seul lecteur pour toute l'application.** Lancer une lecture ailleurs coupe la précédente (pause). Cela vaut
  pour un autre résultat, l'écoute à l'aveugle, la référence d'une voix, la réécoute d'une prise ou l'extrait à
  doubler. Changer de piste dans un même résultat (A/B, beat, voix) reste une bascule à la même position, sans
  coupure.
- **Quitter la page n'arrête pas la lecture.** Le résultat qui joue, ou qui est en pause, reste gardé tel quel :
  position, piste, lecture ou pause. Le suivi de la position et les visualisations s'arrêtent tant qu'on ne les voit
  pas, puis reprennent au retour. Un résultat quitté qui ne joue pas est fermé, et ses lecteurs avec lui. Les
  préécoutes (référence d'une voix, prise, extrait) s'arrêtent avec leur page ou leur dialogue.
- **Barre « en cours de lecture »**, en bas de la fenêtre, sous la barre latérale et le contenu. Elle apparaît dès
  qu'on quitte la page qui joue. Elle montre une image de la vidéo, le titre, la voix et la piste (la lettre seulement
  dans l'écoute à l'aveugle, tant qu'on n'a pas voté), la position et un fin trait d'avancement. On y trouve lecture /
  pause et stop, et un clic sur le titre ramène à la page qui joue. Elle reste visible fenêtre étroite, où la barre
  latérale est repliée.
- **Fermer la fenêtre pendant une lecture** ne l'arrête pas. La fenêtre se cache, l'application reste en vie
  (`Gio.Application.hold`) et la musique continue. Une vidéo passe sur son son seul, de même contenu, au même
  instant du son et sans blanc : le MP3 du doublage, `travail/original.wav` pour l'original, le fichier du beat ou d'une voix
  seule. Plus rien ne décode l'image : un essai sur une vidéo doublée 1080p de 3 min 50 mesure 1,5 % d'un cœur au lieu
  de 24 %. Relancer `voix-la` (instance unique) ou « Raise » rouvre la fenêtre telle qu'on l'a laissée, et la vidéo
  reprend là où en est le son. L'application quitte quand la lecture finit, sur un stop, ou après 15 min de pause
  fenêtre fermée. Sans lecture en cours, fermer la fenêtre quitte comme avant. Les travaux en cours continuent dans
  les deux cas, et une notification arrive à la fin de chacun. Une piste lue dans la vidéo doublée passe sur le même
  son seul (MP3 pour le doublage, fichier du beat ou de la voix) ; au retour, on reprend le flux de la vidéo resté
  ouvert, déjà sur la bonne piste. Rester sur la vidéo sans décoder l'image a été mesuré et écarté (image : 30 à 36 %
  d'un cœur ; son seul : 0,7 à 1,3 %).
- **MPRIS** : le lecteur `org.mpris.MediaPlayer2.voixla`, publié sur le bus de session pendant une lecture. GNOME le
  montre dans le menu du haut (titre, voix et piste, image de la vidéo), et les touches multimédia le pilotent :
  lecture, pause, stop, déplacement, Raise et Quit. Il n'y a pas de piste suivante ni précédente. Les essais
  (`VOIXLA_APP_ID`) publient `org.mpris.MediaPlayer2.voixla.essai<pid>`, sous le nom « Voix-là (essai) ». L'image est
  une miniature de la vidéo doublée, prise une fois au tiers de la vidéo (`~/.cache/voix-la/miniatures/`).

## Corbeille

« Mettre à la corbeille… » se trouve dans l'en-tête du détail d'un résultat, dans le menu « ⋮ » de chaque carte ou
ligne de la bibliothèque (pour une chanson à plusieurs versions : toutes ses versions), et dans la barre du mode
sélection (ou Suppr). Les trois font la même chose, avec une seule confirmation :

- **une seule voix dans le dossier** : tout le dossier va à la corbeille, avec son `travail/` ;
- **plusieurs voix** : seulement les fichiers de cette voix, c'est-à-dire la vidéo et le MP3 doublés, le beat et la
  voix seule enregistrés, les vidéos à partager de `partage/`, et `travail/<voix>/`. Si toutes les voix d'un dossier
  sont sélectionnées, le dossier entier part.

Si l'un d'eux joue, ou si son détail est ouvert, sa lecture s'arrête et ses lecteurs se ferment d'abord, copies vidéo
comprises. Les fichiers partent un par un, et l'envoi s'arrête au premier échec ; le toast dit combien sont partis.
Un dossier où un doublage en cours écrit encore est refusé. Les caches liés (copies vidéo, voix combinée, miniature)
restent : ils ne servent plus et leur purge les efface. Tout se récupère dans la corbeille.

## Bibliothèque des résultats

- **Modèle** : un `Gio.ListStore` d'entrées (une version seule, ou les versions d'une même chanson), filtré par un
  `Gtk.FilterListModel` (recherche et filtres), trié par un `Gtk.SortListModel` (le trieur des colonnes de la liste,
  que le menu de tri pilote aussi), puis un `Gtk.NoSelection`, ou un `Gtk.MultiSelection` en mode sélection. La grille
  (`Gtk.GridView`) et la liste (`Gtk.ColumnView`) sont virtualisées ; seule la vue affichée lit le modèle. La logique
  (versions, badges, regroupement, filtres, tri, compte) est dans `voixla/bibliotheque.py`, sans GTK.
- **Même chanson** : même vidéo source (`travail/source.json`) ; sans elle, le même titre (sans « (extrait …) ») dans
  le même dossier des sorties. La carte montre la version la plus récente ; avec un filtre, on ouvre (et on lit) la
  première version qui le passe. Deux versions de la même voix se distinguent par « extrait 1:00 » ou « entier »,
  puis par la date.
- **Langue des paroles** : `paroles_sorties` (langue chantée) ou `pistes_audio.langue` de `rapport.json`, sinon
  `travail/<voix>/paroles.json`.
- **Miniatures** : une image 16:9 (480 × 270) prise au tiers de la vidéo doublée, comme celle de la barre de lecture et
  dans le même cache (`~/.cache/voix-la/miniatures/`, 60 Mo, les moins récemment utilisées effacées d'abord). Sans
  vidéo, la pochette intégrée au MP3, entière, sur un fond flou tiré d'elle-même ; sans pochette, une pochette générée,
  toujours la même pour un titre (`python3 -m voixla.pistes miniature FICHIER --large` en ligne de commande). Elles
  se préparent en tâche de fond, sans jamais bloquer l'interface : un fil lit celles qui sont en cache, deux au plus
  en préparent (ffprobe et ffmpeg sous `nice -n 19` et `ionice -c3`, en attente tant que la mémoire disponible est
  sous 700 Mo). Celles des cartes à l'écran passent devant, de haut en bas ; une demande qu'aucune carte n'attend plus
  est abandonnée. Les dernières images lues restent en mémoire (160 au plus).
- **Lecture sans détail** : le bouton de lecture crée la page du détail sans la montrer ; elle lit le son seul (le MP3
  du doublage, comme fenêtre fermée) tant qu'on ne la regarde pas, puis reprend la vidéo au même instant quand on
  l'ouvre (clic sur la carte, ou barre « en cours de lecture »).
- **Chargement** : la liste se lit dans un fil, puis s'ajoute au modèle par paquets de 24, dans l'ordre affiché (le
  premier écran d'abord). Après un doublage fini ou F5, seules les entrées changées sont refaites : la sélection et la
  position du défilement restent.
- **Fenêtre étroite** (`Adw.Breakpoint`, 560 sp) : cartes plus petites (deux colonnes à 360 px), deux badges au plus ;
  dans la liste, les colonnes Voix, Mode, Durée et Date cèdent la place à une ligne sous le titre ; Actualiser, le
  dossier des sorties et les boutons de vue passent dans le menu « Trier et filtrer » (F5 reste).
- **Mesuré** sous Xvfb (rendu cairo, logiciel), avec 200 doublages inventés (117 cartes, vidéos de synthèse
  1280 × 720) : lecture du disque 0,27 s dans un fil ; remplissage en 5 paquets, 77 ms au plus sans rendre la main ;
  miniatures du premier écran (16 cartes) en 1,5 s à froid, 17 ms depuis le cache ; défilement, par image : grille
  12,6 ms (médiane), 15,8 ms au plus, liste 9,8 et 12 ms ; toutes les miniatures en 12 s (0,19 s de ffmpeg chacune,
  deux à la fois) ; F5 sans changement 0,09 s ; mémoire du processus 266 Mo à l'ouverture, 355 Mo une fois toutes les
  images chargées.

## Pistes séparées

Dans le détail d'un résultat, les bascules au-dessus du lecteur choisissent ce qu'on écoute. On change de piste sans
perdre la position, au même instant du son, même quand le son d'une vidéo entière part après l'image (voir
[Paroles](#paroles)), et la lecture continue (ou reste en pause).

- **Dans la vidéo doublée** (doubler ≥ 0.6.0) : la vidéo a ses propres pistes audio, calées à l'échantillon près.
  Beat seul, Ma voix seule et Voix d'origine seule changent alors de piste dans le même flux (`current-audio` de
  playbin) : même position, l'image continue, rien à préparer ni à copier. Mesuré : nouvelle piste entendue en 0,25 s,
  au même échantillon (la file audio de playsink passe de 1 s à 0,2 s pour ces vidéos).
  - Pistes repérées par leur titre, dans l'ordre de `pistes_audio.video` de `rapport.json` (`video_autotune` pour
    l'auto-tune), sinon par les titres des flux (`title`, `handler_name` en mp4), lus par ffprobe dans un fil.
  - **Original** reste la vidéo d'origine, avec son propre calage ; sans elle, c'est la piste Original de la vidéo
    doublée, sur son image. Avec « Auto-tune », le beat et la voix d'origine se lisent dans la vidéo auto-tune.
  - En pause, la piste choisie n'est posée qu'à la reprise : on entend encore la précédente 0,2 à 0,3 s (changer de
    piste en pause puis déplacer la lecture bloque playbin, GStreamer 1.24).
  - Le playbin est celui du `Gtk.MediaFile`, retrouvé par `gui/pistes_gst.py` : adresse du flux par la capsule
    `__gpointer__` de PyGObject, nom du type et tailles des structures vérifiés, type de l'objet pointé, usine
    `playbin`. Si un contrôle échoue, l'interface revient aux copies.
  - Préparation à l'avance : seulement la miniature. Ma voix seule n'est combinée qu'en MP3 ou quand on l'écoute.
  - Les anciens doublages à une piste gardent les copies et les fichiers du tableau ci-dessous.

| Piste | Fichier lu | Si le fichier manque |
|---|---|---|
| **Doublage** (par défaut) | la vidéo doublée, ou son MP3 | grisée |
| **Original** | la vidéo d'origine (`travail/source.json`, ou celle choisie à la main), au décalage de l'extrait ; en MP3, `travail/original.wav` | sans la vidéo d'origine : la piste Original de la vidéo doublée ; ancien doublage : `travail/original.wav`, sans image |
| **Beat seul** | la piste de la vidéo doublée ; ancien doublage : `travail/instrumental.wav` : la musique sans aucune voix (original − voix isolée) | grisée, avec la raison |
| **Ma voix seule** (« Voix converties seules » avec une voix par chanteur) | la piste de la vidéo doublée ; ancien doublage : la voix convertie telle qu'elle sonne dans le mix (ci-dessous), préparée en cache à l'avance ; avec l'auto-tune, « Ma voix seule (auto-tune) » de la même façon | ancien doublage sans voix sèche : `travail/<voix>/voix_convertie.flac` brute ; sans rien : grisée |
| **Voix d'origine seule** | la piste de la vidéo doublée ; ancien doublage : `travail/voix_source.wav` : la voix d'origine isolée | grisée, avec la raison |

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
- **Sur la vidéo qui avance** : en vidéo, le beat et les voix seules se lisent sur l'image de la vidéo doublée, qui
  avance avec le son, comme le doublage et l'original. Chaque piste a pour cela une copie préparée une fois, en cache :
  - l'image de la vidéo doublée y est copiée sans réencodage (`-c:v copy`) : mêmes paquets, même nombre d'images ;
  - le son de la piste y est encodé comme `doubler` le fait (AAC 256 kb/s, Opus 192 kb/s en webm) et coupé à la fin de
    la vidéo.

  Le son de la piste commence exactement là où commence le son du doublage. Ce départ est lu dans la vidéo doublée
  plutôt que recalculé : il contient le `decalage_son` de `travail/source.json` et le calage d'un extrait sur une
  image clé. On prend le temps du premier paquet du son doublé, qui porte l'amorce de l'encodeur (1024 échantillons
  d'AAC, 312 d'Opus), et le premier paquet de la copie reçoit le même temps. La copie garde les temps de la vidéo
  doublée (`-copyts`) : on y passe à la même position, et la touche B reste inchangée. Un essai le vérifie sur de
  petites vidéos de synthèse remises en son par les fonctions de `doubler` : mp4 entier et extrait, extrait mkv avec
  ou sans sous-titres, son d'origine en retard, webm. Les paquets d'image sont identiques, et le beat tombe au même
  échantillon que le mix. Même résultat sur de vrais doublages : une chanson en mp4 et un extrait de série en mkv.
  Un seul décodage vidéo à la fois : le flux d'une copie se ferme dès qu'on quitte sa piste. Seuls le doublage et
  l'original restent ouverts.
- **Préparées à l'avance**, pour que la première lecture d'une piste soit immédiate :
  - à la fin de chaque doublage : l'exécuteur détaché lit les événements « sortie » du travail et lance
    `python3 -m voixla.preparation` en tâche de fond, pour les dossiers produits. Cela marche fenêtre ouverte, fermée
    ou application quittée, et le travail suivant de la file n'attend pas ;
  - dès l'ouverture du détail d'un résultat, avant tout clic. La piste qu'on choisit passe devant les autres ;
  - au démarrage de l'interface, pour les 5 doublages les plus récents à qui il manque une copie, seulement quand le
    poste est calme (charge et mémoire).

  L'ordre est le beat, puis ma voix seule (combinée, puis copiée sur l'image), puis la voix d'origine, puis la
  miniature. Ces préparations sont légères : une copie prend environ 5 s pour une chanson de 4 min en 1080p, l'image
  étant copiée telle quelle. Elles ne passent donc pas par `file-attente`, partagée avec les autres sessions, où elles
  attendraient derrière des travaux lourds. Elles tournent tout de suite, une à la fois, sous `nice -n 19` et
  `ionice -c3`, qui leur fait céder le processeur et le disque à tout le reste. Elles attendent tant que la mémoire
  disponible est sous 700 Mo. Un verrou par fichier évite de préparer deux fois la même copie.
- **Pendant une préparation**, le son se lit seul sous l'image arrêtée, avec le bandeau « préparation de la vidéo…
  42 % ». L'avancement vient de `ffmpeg -progress`. Le lecteur passe ensuite tout seul sur la vidéo prête, à la même
  position. Si la préparation échoue, la raison s'affiche sous les bascules, avec un bouton « Réessayer ».
- **Cache des copies** : `~/.cache/voix-la/videos/`, environ 150 Mo par chanson pour les trois pistes. Sa taille est de
  2 Go par défaut, réglable dans Réglages > Doublage (« Taille du cache des pistes »). Les copies les moins récemment
  lues sont effacées d'abord, mais jamais celles du doublage ouvert ou en lecture. Elles sont nommées d'après le
  chemin, la taille et la date de la vidéo doublée et du son : un remix les refait.
- **Sans image** : si le doublage n'a pas de vidéo (MP3 seul) ou si la copie est impossible (vidéo doublée sans
  image, illisible, ou plus lourde que tout le cache), la piste se lit sous l'image arrêtée, avec le bandeau « son
  seul, image arrêtée » et la raison sous les bascules. L'écran ne disparaît pas, et la page ne saute pas. Les
  visualisations suivent la piste écoutée : le spectrogramme du beat, de la voix seule…
- **Enregistrer le beat…** et **Enregistrer ma voix seule…** (groupe « Pistes séparées ») ouvrent un sélecteur sur le
  dossier du doublage, avec le nom des sorties : `<titre> - voix <Nom> (beat).mp3` et
  `<titre> - voix <Nom> (voix seule).mp3`. On écrit en MP3 320 kb/s, ou en FLAC (ou WAV) si l'on change l'extension.
  Doublage à cinq pistes : ma voix seule est extraite de la vidéo (`-map 0:a:N`), sauf si elle est déjà combinée ; le
  beat vient de `instrumental.wav`.
  L'écriture est atomique, sous `nice`. Ces fichiers sont écartés de la liste des sorties de `doubler`, et partent à
  la corbeille avec les fichiers de leur voix.

## Paroles

Sous le lecteur, trois lignes : la ligne en cours en grand et centrée, la précédente et la suivante plus petites et
estompées. Quand `doubler` donne les mots, le mot en cours se **remplit** au fil de sa durée, façon karaoké, et les
mots chantés gardent la couleur d'accent. Les lignes glissent de l'une à l'autre (0,25 s). Entre deux lignes, la
suivante prend la place 3 s avant de commencer, sinon « ♪ ». Un clic sur une ligne va à son début. Le choix
« Originale », « Français » ou « Les deux » (la traduction en plus petit sous chaque ligne) n'apparaît que s'il y a deux
langues ; il est gardé dans `interface.json` (`paroles_langue`). L'interrupteur « Paroles » se trouve dans le menu
« œil » et dans les Réglages (`paroles`, allumé par défaut).

- **« Toutes les paroles »** (touche P, bouton de l'en-tête des paroles ou menu « œil ») : un panneau à droite du
  détail, par-dessus le contenu dans une fenêtre étroite, avec toutes les lignes, leur temps et leur traduction. Il
  suit la lecture en défilant seul (le suivi s'arrête 4 s si l'on fait défiler à la main) ; un clic ou Entrée sur une
  ligne y va. Clé `paroles_panneau`.
- **Sur l'image** : en plein écran (interrupteur « Paroles »), et hors du plein écran avec « Sous-titres sur la
  vidéo » (clé `paroles_video`). Texte blanc cerné de sombre, mot en cours en jaune.
- **Taille du texte** : Petite, Normale, Grande ou Très grande (menu « œil », Réglages ; clé `paroles_taille`). Sur
  l'image, elle suit aussi la largeur de l'image.
- Le calage ne change pas (ci-dessous).
- **`travail/<voix>/paroles.json`**, écrit par `doubler` (format 1) ; `mots` et `traductions` sont facultatifs :

  ```json
  {"format": 1, "langue": "es", "source": "whisper-large-v3",
   "segments": [{"debut": 64.5, "fin": 66.0, "texte": "…", "mots": [{"debut": 64.5, "fin": 64.8, "mot": "…"}]}],
   "traductions": {"fr": {"source": "<modèle>", "segments": [{"debut": 64.5, "fin": 66.0, "texte": "…"}]}}}
  ```

  Les segments ou les mots abîmés sont ignorés un par un. Le fichier n'est jamais modifié.
- **Sous-titres de la vidéo** : le lecteur ne laisse jamais GStreamer dessiner les pistes de sous-titres d'une vidéo
  (paroles de `doubler`, sous-titres d'une vidéo d'origine). Chaque flux s'ouvre sans le drapeau `text` de son playbin
  (`gui/pistes_gst.couper_texte`) : les seules paroles sur l'image sont celles de l'interface. Si le playbin est
  introuvable (autre version de GTK), un message le dit et GStreamer peut dessiner la première piste en plus.
- **Temps du lecteur, temps des paroles.** Le lecteur d'un résultat compte en temps du son doublé : celui du MP3, de
  `travail/original.wav`, du beat, des voix seules, de la frise et de `courbes.json`. Une piste qui a une image (la
  vidéo doublée, sa version auto-tune, les copies des pistes séparées, la vidéo d'origine) compte en temps de la vidéo
  doublée, où le son commence à `decalage_s` de `paroles.json` (exact depuis doubler 0.6.0, y compris pour un extrait
  dont l'image part d'une image clé antérieure ; une valeur négative, d'un ancien extrait mkv, est ignorée), sinon à
  `decalage_son` de `travail/source.json` (le retard du son d'origine d'une vidéo entière ; 0 pour un extrait) ; la
  vidéo d'origine garde `decalage_son` et le début de l'extrait : elle est lue décalée d'autant (`pistes.avance_piste`, la seule règle de passage d'un
  temps à l'autre). `paroles.json` est en temps du son + `decalage_s` (le temps du son vaut `debut − decalage_s`), et
  les sous-titres de repli en temps de la vidéo. Les paroles, la frise et la courbe suivent donc chaque piste. Une
  bascule (piste, Vidéo/MP3, son seul fenêtre fermée et retour) garde le même instant du son, et un clic sur une ligne
  ou sur la frise va à ce moment. Écart restant mesuré sous GStreamer : 26 ms au plus (amorce de l'AAC dans un mkv,
  délai de l'encodeur MP3). Une vidéo au son en retard s'ouvre au début du son, quelques centièmes de seconde après sa
  première image.
- **Repli : les sous-titres de la vidéo doublée.** C'est le cas des doublages sans `paroles.json`. Chaque flux de
  sous-titres en texte (mov_text, subrip, ass…) est extrait par `ffmpeg -map 0:s:N -f srt`, sous `nice`, dans
  `~/.cache/voix-la/sous-titres/` (20 Mo au plus). L'affichage indique leur langue (tag `language`) et s'ils sont
  « forcés ». Avec deux flux, dont un en français, le choix de langue les montre ensemble. Les sous-titres en image
  (PGS, DVD) ne sont pas lus. Rien n'est écrit à côté de la vidéo.

## Karaoké

Page « Karaoké » (Alt+7), ou « Chanter… » dans le menu « ⋮ » d'une carte de la bibliothèque et dans l'en-tête du
détail d'un doublage (quand le doublage a son `travail/instrumental.wav` ; sans lui, le bouton du détail est grisé et
l'infobulle dit pourquoi). Les deux appellent `fenetre.chanter(res)`, qui ouvre la page sur ce doublage.

- **Préparer** : le doublage, puis le passage (chanson entière ; extrait de la ligne X à la ligne Y, ou début et fin à la
  main sans paroles ; extrait en boucle, 2 à 10 tours). Conseil affiché : mettre un casque. Micro du PC au choix (pas
  celui du téléphone : scrcpy démarre la prise avec un retard variable, le chant ne tomberait pas sur le beat).
- **Chanter** : décompte 3-2-1, puis le beat et le micro partent ensemble, dans un même pipeline GStreamer ; les paroles
  défilent en karaoké (le composant de `gui/vue_paroles.py`, limité aux lignes du passage). Espace arrête et note,
  Échap annule ; la prise s'arrête seule à la fin du beat.
- **Note de justesse sur 100** (`voixla/justesse.py`) : hauteur de la prise par YIN (`analyse`, 16 kHz, 20 ms) comparée
  à `source_hz` de `courbes.json` (sinon à un YIN de `travail/voix_source.wav`). Écart en cents ramené dans l'octave
  (une octave plus bas ou plus haut n'est pas faux), médiane sur 100 ms ; crédit plein jusqu'à 35 cents, nul à
  100 cents. Chaque ligne cherche son meilleur calage dans ±160 ms (le retard est rapporté à part) ; une ligne chantée
  à moins de 60 % est pénalisée ; total pondéré par le temps chanté de l'original ; une boucle note chaque tour.
- **Résultat** : note, courbe « ta voix contre l'original » (passages faux en rouge, clic sur une ligne pour zoomer et
  l'écouter), lignes notées avec un conseil, réécoute (ta voix seule, ou avec le beat), recalage avec une autre latence,
  garder ou jeter la prise, mix en MP3.
- **Latence** : le pipeline mesure l'écart entre le premier échantillon enregistré et la sortie du beat ; reste celle du
  matériel (sortie, casque, micro), réglée à la main en ms ou mesurée par « Mesurer… » (quatre bips joués et
  enregistrés, corrélation). Gardée dans `interface.json` (clé `karaoke`).
- **Fichiers** : `<dossier du doublage>/karaoke/` (prises WAV 24 bits 48 kHz et leur JSON ; prises non gardées
  `.prise-…`, effacées après un jour ; MP3 `<titre> - karaoké AAAA-MM-JJ HHhMM.mp3`), ignoré par la bibliothèque. Cache :
  `~/.cache/voix-la/karaoke/` (500 Mo). En ligne de commande : `python3 -m voixla.karaoke analyser|mp3`.
- **Sobriété** : note et mix dans la file des analyses, sous `nice -n 10`, par `file-attente` au-delà d'une minute de son.
- **Doubler ma prise** : sur le résultat d'une prise (gardée ou non), « Doubler ma prise… » demande la voix cible, puis
  lance un travail `doubler` sur le **mix ta voix + beat** de la prise, enregistré en FLAC dans `karaoke/` sous le nom
  `<titre> - ma prise AAAA-MM-JJ HHhMM.flac` (`karaoke.preparer_entree_doubler` ; le mix en cache est refait s'il a
  été purgé). Le travail est `doubler <fichier> --voix <voix> --mode=chant` ; il passe par le quota GPU comme la page
  Doubler (sans dialogue : s'il ne tient pas, il attend la remise à zéro, sauf réglage « lancer »). Le résultat arrive
  dans la bibliothèque, mp3 seul, sous le nom de la prise ; le fichier reste dans `karaoke/` (que la bibliothèque ne
  lit pas) parce que `--remix` le relit. Grisé quand le mix n'est pas prêt, qu'aucune voix n'existe ou que `doubler`
  n'annonce pas « audio seul » (`moteur.capacites()` : `son_seul`). Pourquoi le mix et pas la voix seule : voir
  le CHANGELOG (séparation propre, beat rendu par la musique seule, mode chant sûr).
- **Limites** : la mesure de latence au vrai micro (casque Bluetooth compris) reste à essayer.
- **Essais** : `tests/faux_karaoke.py` (chanson inventée à mélodie connue), `test_justesse` (juste 100, un demi-ton
  faux 1, une octave plus bas 100, retard de 100 ms 100…), `test_karaoke`, et la page sous Xvfb
  (`tests/essai_karaoke_gui.py`).

## Partager un extrait

« Partager… » (en-tête du détail d'un doublage, ou menu « ⋮ » d'une carte ou d'une ligne de la bibliothèque) fait une
courte vidéo prête à partager : TikTok, Reels, Shorts, statut WhatsApp.

- **Extrait** de 15 à 60 s, en temps du doublage (celui du lecteur). Depuis le détail, la **boucle A-B** du lecteur
  (touche L) est prise d'office ; « Prendre la boucle » la reprend. Sinon, l'extrait **proposé** : le refrain (la
  fenêtre qui chante le plus de lignes répétées de `paroles.json`, deux transcriptions presque identiques comptant pour
  une), sinon le passage le plus chanté (d'après les paroles, sinon la voix convertie de `courbes.json`) ; il part 0,5 s
  avant une ligne et finit après une ligne. Début et Fin s'écrivent comme sur la page Doubler (`1:05`, `65`).
  « Écouter » pose la boucle A-B du lecteur sur l'extrait et le lit, avec la piste choisie.
- **Format** : vertical 9:16 (1080 × 1920), carré 1:1 (1080 × 1080) ou d'origine (1920 au plus sur le grand côté).
  **Cadrage** : l'image entière sur un fond flou tiré d'elle-même, paroles dessous ; ou recadrée au centre, paroles
  dessus, leur bas aux trois quarts de la hauteur (au-dessus des légendes et des boutons des applications). Un MP3 sans
  vidéo prend sa pochette, sinon le dégradé de sa pochette générée, paroles au milieu.
- **Paroles karaoké** incrustées par des sous-titres ASS à effet `\kf` (filtre `ass` de ffmpeg) : le mot se remplit au
  fil de sa durée, comme sur l'image du lecteur (blanc cerné de sombre, jaune), avec le même calage
  (`paroles.ecart_lecture`) et les mêmes lignes à l'écran (la suivante 3 s avant de commencer). **Traduction** française
  sous la ligne, en option. Police Noto Sans grasse, 6,2 % du petit côté.
- **Piste son** : Doublage (par défaut), Doublage (auto-tune), Beat seul, Ma voix seule : la piste de la vidéo doublée à
  cinq pistes (`-map 0:a:N`), sinon le MP3, `travail/instrumental.wav` ou ma voix seule combinée.
- **Mention** « Voix générée par IA », discrète, en haut à gauche (sous les onglets des applications) : allumée à chaque
  ouverture, texte modifiable ; aussi dans les métadonnées (`comment`).
- **Fondu** d'entrée et de sortie : image 0,5 s ; son 0,5 s et 1 s.
- **Encodage** H.264 (High 4.1, yuv420p, 30 i/s au plus, image clé toutes les 2 s) et AAC 192 kb/s, mp4 `faststart` ;
  qualité constante (CRF 21), débit vidéo plafonné à 5 Mb/s : 40 Mo au plus pour 60 s. Mesuré (doublage inventé,
  image 1280 × 720, poste chargé, x264 à 4 fils sous `nice`) : 60 s en 9:16 en 64 s de calcul, 15,7 Mo.
- **En tâche de fond** : `python3 -m voixla.export_social`, dans son propre groupe de processus, par `file-attente`
  au-delà de 5 s de calcul estimé (« voix-la export », 1 Go, 2 cœurs libres). L'attente dans la file, puis
  l'avancement s'affichent ; « Annuler » arrête tout le groupe (SIGTERM, SIGKILL 5 s plus tard), sans rien laisser. Un
  seul export à la fois ; fermer le dialogue ne l'arrête pas, un toast le dit à la fin (« Ouvrir le dossier »).
- **Aperçu** : une image de la vidéo où un mot se remplit (ou à l'instant choisi sous l'image), refaite une demi-seconde
  après chaque changement, dans un fil (une image, environ 1 s ; jamais par file-attente).
- **Sortie** : `<dossier du doublage>/partage/<titre> - voix <Nom> - partage 1m05-1m35 9x16.mp4` (`1x1`, `origine` ;
  « (beat) », « (auto-tune) », « (voix seule) » pour une autre piste ; « (2) » si le nom est pris). `partage/` n'est
  pas lu comme un doublage ; ses vidéos partent à la corbeille avec les fichiers de leur voix.
- **Préférences** : format, cadrage, fondu, traduction et texte de la mention (clé `partage` d'`interface.json`) ; la
  mention et la piste reviennent à leur défaut.

En ligne de commande :

```sh
cd interface
python3 -m voixla.export_social "<données>/sorties/Mon titre" --refrain            # le refrain, en 9:16
python3 -m voixla.export_social "…/Mon titre - voix Jules.mp4" --debut 1:05 --fin 1:35 --format carre --piste beat
python3 -m voixla.export_social DOSSIER --proposer                                  # la proposition, en JSON
python3 -m voixla.export_social DOSSIER --debut 65 --apercu auto image.png          # une image, sans encoder
python3 -m voixla.export_social DOSSIER --debut 65 --traduction --ass paroles.ass   # les sous-titres seuls
```

Autres options : `--voix ID`, `--duree S`, `--cadrage flou|recadre`, `--piste doublage|autotune|beat|ma_voix`,
`--sans-paroles`, `--mention TEXTE` ou `--sans-mention`, `--sans-fondu`, `--sortie FICHIER.mp4` ;
`VOIXLA_AVANCEMENT=1` : avancement en lignes JSON.

## Visualisations du son

Chaque panneau s'affiche ou se masque dans le menu « œil » d'un résultat (ou dans les Réglages) ; le choix est gardé
dans `interface.json`. Le mode cinéma (V) les masque tous d'un coup.

| Visualisation | Par défaut | Ce qu'elle montre |
|---|---|---|
| **Frise d'octave** | allumée | Sous le lecteur : la voix détectée de chaque passage et son décalage en demi-tons, clic pour y aller (`frise_octave`). |
| **Voix de chaque passage** | allumée | Avec une voix par chanteur : quelle voix chante chaque passage (`frise_voix`). |
| **Courbe de mélodie** | allumée | La voix d'origine (trait fin), la consigne, c'est-à-dire l'origine décalée du décalage de chaque passage (pointillés), et la voix convertie (trait plein, aux couleurs de la frise d'octave). Fenêtre de 12 s qui suit la lecture, échelle en notes (do, ré… ; la3 = 440 Hz) ou en Hz, clic pour aller à un moment. |
| **Spectrogramme** | éteint | Défile avec la lecture et montre la piste écoutée (doublage, son d'origine, beat seul, ma voix seule, voix d'origine). Échelle de fréquence logarithmique, couleurs lisibles en clair comme en sombre. |
| **Forme d'onde** | éteinte | Sous la frise d'octave : crêtes de toute la piste, partie déjà lue en couleur d'accent, curseur, clic pour y aller. |
| **Courbe de la version auto-tune** | allumée | Sur la courbe de mélodie, en ambre : la voix convertie après l'auto-tune (`autotune_hz` de `courbes.json`), quand le doublage en a une. |
| **Vu-mètre** | éteint | 24 bandes de 60 Hz à 8 kHz et niveau global, animés pendant la lecture. |

- **Données de la mélodie** : `travail/<voix>/courbes.json`, écrit par `doubler` (format 1 : `pas_s`, `source_hz`,
  `convertie_hz`, `decalage_dt`, et `chœurs_hz` facultatif). Pour un doublage plus ancien, l'interface calcule la
  hauteur elle-même (YIN avec numpy, son ramené à 16 kHz) à partir de `travail/voix_source.*` et de
  `travail/<voix>/voix_convertie.flac`. Le résultat est gardé dans `travail/<voix>/courbes-locales.json`, au même
  format, avec la signature des fichiers analysés : il est refait si la voix convertie change.
  Comme la frise, elle est en temps du son doublé : elle suit le son même quand celui d'une vidéo entière part après
  l'image.
- **Spectrogramme, forme d'onde, vu-mètre** : une seule lecture du son par piste (ffmpeg, puis numpy), mise en cache
  dans `~/.cache/voix-la/pistes/`, par fichier et par produit. On lit toujours une piste désignée (`-map 0:a:N`) : la
  première d'une vidéo (le doublage), ou celle d'une piste lue dans la vidéo (en cache à part). Un fichier refait (taille ou date) est réanalysé.
- **Sobriété** :
  - chaque calcul tourne dans un processus à part, sous `nice`, un seul à la fois ;
  - au-delà d'une minute de son, il passe par `file-attente`, la file des travaux lourds du poste ;
  - l'affichage ne recalcule rien : il redessine 25 fois par seconde au plus, seulement pendant la lecture et si la
    fenêtre est visible.

## Corriger qui chante quoi

Avec une voix par chanteur, `doubler` se trompe parfois sur qui chante un passage. La frise « Voix de chaque passage » du
détail permet de le corriger et d'entendre le résultat aussitôt :

- chaque passage est délimité d'un fin trait ; un clic ouvre son menu : « Passage 12 · 1:23–1:28 · automatique : A »,
  puis **Chanté par A**, **Chanté par B**, **Les deux**, **Automatique**, et « Écouter ce passage » (le clic va aussi à ce
  moment) ;
- un choix reste « en attente » (hachures sur la frise) jusqu'au bouton **Appliquer**, qui refait le doublage par un
  remix en tâche de fond, comme le remix rapide (`doubler --remix --affectation FICHIER`, sans Kaggle, quelques
  secondes) ; le lecteur est relâché puis repart à la même position, le rapport est relu, la frise redessinée ;
- les passages corrigés à la main sont marqués (repère sur la frise, infobulle « corrigé à la main », ligne « corrigé
  à la main : N passages » dans les informations) ; **Revenir à l'automatique** (visible dès qu'une correction est
  appliquée) rend le résultat du noyau ;
- tout est grisé, avec la raison, si le doublage n'a pas les deux versions de chaque passage (fait avant, ou avec
  `--versions-duo non` : « refaites-le pour pouvoir corriger qui chante quoi »), si un passage n'a pas sa version dans
  l'autre voix (`--versions-duo utiles`), ou si `doubler` ne connaît pas `--affectation`.

L'estimation de durée de la page Doubler compte les deux versions de chaque passage en duo (conversion doublée).

## Comparer des voix

`doubler --comparer A,B,C` double le même extrait dans plusieurs voix en une seule session Kaggle (envoi, séparation,
nettoyage et paroles payés une fois, la conversion refaite pour chaque voix sur les deux GPU). L'interface le propose et
fait écouter les résultats :

- **Doubler** : dans « Voix cible », **Comparer plusieurs voix** (si `doubler` annonce `--comparer`) remplace le choix
  de la voix par une liste de cases, une par voix (deux au moins, six au plus ; le choix est gardé dans
  `interface.json`, clé `voix_comparees`). La ligne dit combien de voix, et conseille un extrait de 20 à 40 s quand une
  vidéo est entière. L'estimation de durée et le quota GPU comptent les conversions en plus
  (`doubler.estimer_noyau_s(…, voix_comparees=N)`, sinon le repli de l'interface) ; la demande part avec
  `--comparer A,B,C` à la place de `--voix`. Dans la file, la carte du travail (page Travaux) dit « comparaison de N
  voix : » suivi de toutes les voix ; « Voir » ouvre le résultat de la première.
- **Résultats** : le détail d'un doublage dont le dossier a deux voix ou plus (`comparaison.json` de `doubler --comparer`,
  ou des doublages de voix différentes du même extrait) a un groupe **Comparer les voix** : les voix et un bouton
  **Comparer**. Il prépare un banc d'écoute à l'aveugle dans le cache (`~/.cache/voix-la/comparaisons/<empreinte du
  dossier>/`, `voixla/comparaison.py`) : des liens `A.mp3`, `B.mp3`… vers les mp3 des sorties (même durée, même instant),
  la voix convertie seule de chaque voix (`A_voix.mp3`, interrupteur **Voix seule**, touche V), l'original en mp3, et une
  correspondance lettre → voix tirée au hasard, gardée tant que les voix sont les mêmes. Puis il ouvre la page
  **Écoute à l'aveugle** sur ce banc (titre « Comparer des voix », bouton « Banc d'essai » pour revenir au banc
  d'essai).
- **Écoute** : une seule lecture pour toute la page ; passer d'une version à l'autre garde la position, pour comparer au
  même instant, et la barre de lecture ne dit que la lettre. On note chaque version de 1 à 5 (touches 1 à 5), on
  enregistre : le vote, dans `votes.json` du banc de la comparaison (jamais celui du banc d'essai), dévoile les voix.
  « Voter à nouveau » les cache de nouveau.
- Remix : le remix rapide d'une version ne touche que cette voix ; `doubler --comparer A,B,C --remix` les refait toutes.

## Séance d'enregistrement

Pour être reconnue dans ses doublages, une voix a besoin de sa version **chantée** : le modèle n'a souvent que 20 s de
parole. La page Voix a un bouton **Enregistrer ma voix chantée…** (à côté de « Chant pour l'affinage ») : il empile la
séance par-dessus la page, sans onglet de plus dans la barre latérale. On chante dans son téléphone, casque branché sur
le PC, sur des pistes jouées par l'interface, puis on importe ses fichiers. Le code est dans `voixla/seance.py` (sans
GTK, testé) et `gui/seance.py` (la vue).

- **Comment ça marche**, en haut, en 5 lignes : installer le téléphone ; lancer l'enregistrement sur le téléphone
  AVANT la piste ; une étape = un fichier ; importer à la fin ; environ 20 min en tout. Les textes sont dans
  `voixla/seance_textes.py`, adressés à la personne qui enregistre, sans prénom.
- **Rappel des consignes**, toujours visible : téléphone à 20 cm, meilleure qualité d'enregistrement, mode avion ;
  casque bien fermé et volume modéré (la musique ne doit pas passer dans le micro) ; taper dans ses mains sur le 4e
  bip ; pas d'envoi par WhatsApp (le son est recompressé).
- **Cinq étapes**, dont tout le contenu est fourni (rien à chercher ailleurs). Chacune affiche une **durée estimée**,
  une case **Fait** (gardée dans `voix/<nom>/seance.json`, avec la taille du texte et le choix grave/aiguë ; l'étape
  porte alors un ✓) et le **nom de fichier conseillé** à donner sur le téléphone : `1-parole`, `2-tessiture`, `3-notes`,
  `4-chanson`, `5-au-clair-de-la-lune`, `5-frere-jacques`.
  1. **Parole** (≈ 3 min, sans piste) : un texte français de cinq paragraphes (`seance_textes.TEXTE_PAROLE`) affiché
     en grand, sur toute la largeur, avec la **taille du texte réglable** (− / +, de 60 % à 180 %) et le défilement de
     la page ; puis « Parle librement (1 min) » avec trois sujets au choix.
  2. **Tessiture** (≈ 2 min) : une piste guide **réglée sur la voix**. Le centre C de la voix vient de `tessiture.json`
     (milieu de `confort`, sinon `p50`), sinon de 2 × `hauteur_parole_mediane_hz` de `voix.json` ; sans ces données,
     deux boutons proposent « Voix grave » (C = 190 Hz) ou « Voix aiguë » (C = 380 Hz). La piste : 4 bips à 880 Hz
     (1, 2, 3 et 4 s), puis 6 **sirènes guides** de 8 s (une glissade continue de C × 2^(−9/12) à C × 2^(+9/12), puis
     retour ; fondamentale et 2 harmoniques, enveloppe douce), trois fois sur « aaa » puis trois fois sur « ou », 3 s de
     silence entre chacune ; puis la **gamme** do ré mi fa sol la si do, montée puis descente (tonique : le demi-ton
     entier le plus proche de C × 2^(−5/12), 1,2 s par note), sur « la » puis sur « mi ». Le nom de la note en cours
     s'affiche en grand, synchronisé.
  3. **Notes à tenir** (6 notes de 110 à 261,63 Hz, 5 s de son et 3 s de silence, deux tours : « aaa » puis « ou »).
  4. **Chanson** d'un doublage (voir plus bas).
  5. **Chanson libre** (≈ 3 min) : deux chansons du **domaine public** avec accompagnement et paroles complètes, **Au
     clair de la lune** (deux couplets) et **Frère Jacques** (chantée deux fois) — ou n'importe quel doublage, comme à
     l'étape 4. Les notes et les paroles (une syllabe par note) sont dans `voixla/seance_chansons.py`. Chaque chanson
     est **transposée en demi-tons entiers** pour que la note médiane de sa mélodie tombe sur le centre C de la voix,
     à environ 88 battements par minute. Deux pistes, chacune derrière 4 bips : **avec la mélodie** (piano doux
     synthétisé et accords simples I, IV, V choisis d'après la mélodie, en blanches) pour s'entraîner, et
     **accompagnement seul** (les accords et un léger métronome) pour la vraie prise. Les **paroles** sont générées
     depuis les durées des notes, décalées des 5 s de bips, en **très grand** (taille « géante » du karaoké, une
     ligne à la fois, la suivante en petit) : une ligne de plus de 6 syllabes est affichée en deux moitiés, et la
     syllabe en cours se remplit (le karaoké remplit déjà mot à mot ; ici un mot est une syllabe).
     Tout est synthétisé avec numpy et ffmpeg, sans téléchargement.
- **Les pistes** se jouent avec le lecteur de l'application (`gui/lecteur.py` : lecture / pause, barre de position) et
  le temps restant ; une seule lecture à la fois, comme ailleurs. Elles se préparent hors du fil de l'interface, avec une
  barre de progression, par ffmpeg, en mp3, dans `~/.cache/voix-la/seance/<empreinte>/` (l'empreinte vient du chemin,
  de la taille et de la date de `travail/instrumental.wav` et `voix_source.wav`) ; ce qui existe n'est pas refait. Les
  notes sont des sinus. Une chanson : **4 bips à 880 Hz à 1, 2, 3 et 4 s, puis la chanson à 5 s** (on tape dans ses
  mains sur le 4e bip : cela permet de recaler la prise, même avec la latence d'un casque Bluetooth), un limiteur à
  0,89, et la tonalité d'origine ou de −1 à −7 demi-tons (`rubberband`, `pitchq=quality`, `channels=together`).
  **Répéter avec la voix** joue le guide (la musique et la voix d'origine à −6 dB), **Prise : musique seule** la vraie
  prise.
- **Le doublage** vient de n'importe quel doublage de la bibliothèque qui garde son `travail/` (`instrumental.wav` et
  `voix_source.wav`). **Chansons conseillées pour ta voix** : les doublages sont classés selon l'écart (en demi-tons)
  entre la tessiture du chanteur d'origine (`rapport.json` : `decision_hauteur.voix_principale`, sinon le premier
  dictionnaire qui porte `f0_p10_hz`, `f0_med_hz`, `f0_p90_hz` et `classe`) et la zone de chant de la voix : deux fois
  `hauteur_parole_mediane_hz` de `voix.json`, ou, si `voix/<nom>/tessiture.json` existe
  (`{"p05","p10","p50","p90","p95","confort":[bas,haut]}` en Hz), le centre de sa zone de confort. La tonalité conseillée
  baisse la chanson de cet écart (0 à −7), jamais ne la monte ; le classement tient compte de ce qui reste d'écart après
  la transposition. Les trois premières ont une étoile ; un doublage sans tessiture connue vient à la fin.
  **Langues** : seules les chansons dans une langue de la voix sont proposées (`voix/<nom>/voix.json`, clé `langues`,
  `["fr"]` par défaut ; par exemple `"langues": ["fr", "en"]`). La langue chantée d'un doublage vient de son
  `rapport.json` (`paroles_sorties.langue_chantee.langue`, `pistes_audio.langue.paroles`), sinon de
  `travail/<voix>/paroles.json`, du `[la:xx]` du `.lrc`, puis du suffixe des `.srt` (`.fr.srt`, `.en.srt`). Les autres
  sont derrière l'interrupteur **Montrer toutes les langues** (la langue s'affiche alors entre crochets).
- **Tout est dans l'interface** : aucune piste à préparer à la main, la séance fabrique les siennes dans le cache.
- **Paroles synchronisées**, en très grand pendant la lecture : le `.lrc` du doublage, avec la vue des paroles de l'application, décalées des 5 s de bips,
  avec la mention « transcription automatique, il peut y avoir des fautes ».
- **Importer mes enregistrements…** : sélecteur multiple (m4a, aac, mp3, wav, ogg, opus, 3gp, amr). L'**étape de
  chaque fichier est reconnue à son nom** (`1-parole`, `2-tessiture`, `3-notes`, `4-chanson`, `5-au-clair-de-la-lune`,
  `5-frere-jacques` ; le numéro du début l'emporte, sinon le nom suffit) : une ligne par fichier propose l'étape
  trouvée, à corriger avant « Importer ces fichiers » ; un nom inconnu prend l'étape de la liste « nom non reconnu ».
  Les fichiers sont copiés **tels quels** dans `voix/<nom>/chant/` (ou `parole/` pour l'étape 1), sous un nom horodaté qui garde l'étape
  (`20261109-153012-notes.m4a`). Ils ne sont lus que par ffmpeg (protocoles limités aux fichiers) : rien de ce qu'ils
  contiennent n'est exécuté. Chaque fichier est contrôlé : durée, saturation (crête à 0 dBFS), niveau, bruit de fond, et
  **musique du casque** dans la prise (ressemblance du rythme de la prise avec celui de la piste jouée, une fois les
  phrases lentes retirées ; alerte à partir de 0,6).
- **Durées** : le total de chant et de parole déjà enregistré pour la voix (tous les fichiers audio de `chant/` et
  `parole/`), avec l'objectif de 10 min de chant et 5 min de parole.

Pour les essais et les captures : `VOIXLA_SEANCE_ETAPE` (étape ouverte), `VOIXLA_SEANCE_CHANSON` (chanson de l'étape 5)
et `VOIXLA_SEANCE_POSITION` (instant, en secondes, où placer les paroles).

Essais : `tests/test_seance.py` (étapes, préparation des pistes sur des sons de synthèse, classement, paroles, import,
contrôles), `tests/test_seance_contenu.py` (le texte mot pour mot, les notes, les syllabes et les durées des chansons, la
transposition, les accords, les sirènes et la gamme, `seance.json`, la reconnaissance des noms) et `tests/test_seance_gui.py` (la fenêtre sous Xvfb : barre latérale sans l'écoute à l'aveugle, la séance
de bout en bout).

## Enregistrer au téléphone

Pour une voix (parole) comme pour une prise de chant, la liste « Micro » propose, après les micros du PC, les
téléphones Android prêts : « Téléphone Pixel 7 (USB) », « Téléphone Pixel 7 (sans fil, 192.168.0.104) ».

- **Brancher le téléphone** : débogage USB activé (options pour les développeurs), puis branché en USB, ou sans fil
  après `adb connect <adresse IP>:5555`. Sans téléphone prêt, la ligne « Micro du téléphone » dit pourquoi (aucun
  téléphone, débogage non autorisé sur son écran, hors ligne, scrcpy ou adb introuvable, scrcpy trop ancien) et
  propose **Réessayer**. Le champ « Téléphone sans fil » fait d'abord `adb connect` sur l'adresse indiquée, préremplie
  avec `ADB_SERIAL` ou le téléphone de la dernière prise.
- **La prise** : `scrcpy -s <série> --no-video --no-playback --no-window --audio-source=mic-voice-recognition
  --audio-codec=flac --require-audio --record=<fichier caché>.flac --time-limit=<durée>`, dans un fil à part. Le son
  arrive sans perte, sans image ni son sur le PC. Il devient un WAV mono 48 kHz, 24 bits en chant et 16 bits en parole
  (comme au micro du PC), par `ffmpeg`, puis le FLAC est effacé (gardé si la conversion échoue). Le compte à rebours,
  l'arrêt, le contrôle de qualité, Garder, Supprimer et Recommencer sont les mêmes qu'au micro du PC.
- **Pas de niveau en direct** : scrcpy n'en donne pas sans jouer le son sur le PC. L'écran de la prise montre le
  temps écoulé, et le contrôle de qualité montre le niveau après la prise.
- **Arrêt** : scrcpy s'arrête seul à la durée prévue. « Arrêter » lui envoie SIGINT, comme Ctrl+C, pour qu'il termine
  le fichier ; s'il ne s'arrête pas dans les 10 s, il est tué. Un fichier vide donne « le téléphone n'a rien
  enregistré (téléphone connecté ?) », avec la cause donnée par scrcpy.
- **scrcpy et adb** : scrcpy du `PATH`, sinon `~/.local/bin/scrcpy` ; adb est celui livré à côté de scrcpy (sinon celui
  du `PATH`), passé à scrcpy par la variable `ADB` : un seul serveur adb pour les deux. Seules les options que le
  scrcpy installé connaît sont passées (lues dans `scrcpy --help`) : sans `mic-voice-recognition`, la source est
  `mic`. Options vérifiées avec scrcpy 5.0.1.
- **Mémoire** : la source de la dernière prise est gardée dans `interface.json` (`source_enregistrement` :
  `telephone:<série>`, vide pour le micro du PC des Réglages). Un téléphone revenu sous une autre série (port sans fil
  changé) cède la place au premier téléphone prêt ; `ADB_SERIAL` passe en premier.

## Reprise et abandon d'une session Kaggle

Tant que `~/.local/state/voix-la/session-en-cours.json` existe, `doubler` refuse tout nouveau doublage (le nouveau noyau
effacerait la sortie de la session). La page Travaux montre alors :

- **la bannière de reprise** (aucun travail actif, session pas suivie par un `doubler` vivant) avec deux boutons :
  « Reprendre » (travail `doubler --reprendre`) et « Abandonner la session » ;
- **un doublage refusé** : repéré par son échec et le message de `doubler` dans son journal (« une session Kaggle
  précédente n'est pas finie »), il l'explique en clair, avec « Reprendre » et « Abandonner la session » tant que la
  session existe ; `doubler.py` n'est pas modifié ;
- **une attente anormale** : quand les événements `kaggle` du travail disent `queued` depuis plus de 2 fois
  `estimation_s`, avec un plancher de 10 min et un plafond de 30 min (30 min sans estimation), la carte du travail
  dit que Kaggle n'a pas encore donné de GPU, qu'une autre session du compte les occupe peut-être, et propose
  « Abandonner la session » (`travaux.attente_anormale`) ;
- **un travail de reprise** en cours ou annulé : même bouton.

« Abandonner la session » demande confirmation (Adw.AlertDialog). Le dialogue explique que la version du notebook est
peut-être encore en attente sur Kaggle et consommera du quota si elle démarre ; l'outil `kaggle` ne sait pas
l'annuler (seulement `delete`, qui supprime tout le notebook : l'interface ne l'appelle jamais), d'où le bouton
« Ouvrir la page du notebook sur Kaggle » (`Gtk.UriLauncher`, `https://www.kaggle.com/code/<noyau>`, le noyau venant
du champ `noyau` du fichier de session, exemple `exemple/doublage-voix-gpu`). À la confirmation, `systeme.abandonner_session`
annule d'abord les travaux de reprise actifs par la marque « annuler » habituelle, attend (20 s au plus) que
`doubler` lâche le verrou, puis RENOMME le fichier en `session-abandonnee-AAAAMMJJ-HHMM.json` dans le même dossier
(jamais effacé, jamais écrasé : suffixe `-2` si besoin). Si un `doubler` vivant tient encore le verrou commun
`verrou-kaggle` (ou l'ancien verrou du dossier de sorties de la session), rien n'est renommé. Essais :
`tests/test_abandon_session.py` ; capture : `outils/capturer.sh /tmp/a.png clair "travaux:abandon"` (dialogue), avec
`VOIXLA_ETAT_DOUBLER` vers un dossier d'essai contenant un `session-en-cours.json` inventé.

## Quota GPU Kaggle

Kaggle donne environ 30 h de GPU par semaine et par compte, remises à zéro chaque samedi à 00:00 UTC (fenêtre fixe).
Une session sur deux cartes T4 compte une seule fois : c'est sa durée qui compte. Le code est dans `voixla/quota.py`,
sans GTK.

- **Lecture** : `kaggle quota --format json` (CLI 2.2.4 ou plus récente) donne l'utilisé, le total et l'heure de remise
  à zéro. Lecture seule, rien n'est consommé ; l'interface n'ouvre pas le jeton, c'est la CLI qui le lit. Relue au plus
  toutes les 5 min (toutes les minutes page État ouverte, tout de suite à la fin d'un doublage ou par F5), et gardée
  dans `~/.local/state/voix-la/quota-kaggle.json`. Le compteur de Kaggle avance pendant une session : ce qui a tourné
  depuis la lecture s'y ajoute d'après le compte local.
- **Sans réponse de Kaggle**, le compte local de la semaine, avec le quota et la remise à zéro des Réglages (30 h,
  samedi 00:00 UTC). Une session compte du démarrage du noyau à sa fin. Sources : `rapport.json` des doublages
  (`noyau_depuis_envoi_s` − `attente_demarrage_noyau_s`, fin d'après le journal du noyau rapatrié), travaux de la file
  (réussis, en échec, annulés : le noyau continue seul, durée estimée), `session-en-cours.json` et
  `session-precedente.json` de doubler (doublages en ligne de commande compris), outils préparés (11 min estimées),
  `banc/resultats/<S>/suivi.json`. Les vidéos d'un lot et une session vue par plusieurs sources ne comptent qu'une fois
  (même version du noyau, ou mêmes heures : doubler n'a qu'une session à la fois).
- **Ce qui échappe au compte local** (Kaggle, lui, compte tout) : les sessions d'un autre poste ou du site de Kaggle ;
  un doublage en ligne de commande vers un autre dossier (`--sortie`), sauf la dernière session de doubler ; une
  session sans rapport lancée en ligne de commande et suivie d'une autre ; un doublage mis à la corbeille, sauf s'il est
  passé par la file ; la durée exacte des outils préparés et des essais du banc sans suivi.
- **Un doublage tient-il ?** Sa session estimée (celle de `doubler`), plus 20 % de marge, doit tenir dans le reste,
  moins ce que la file prendra encore (le reste du travail en cours, les travaux en attente). Sinon, selon le réglage
  « Doublage qui ne tient plus dans le quota » :
  - **Demander** (par défaut) : un dialogue propose « Attendre la remise à zéro » (le choix par défaut), « Lancer quand
    même » ou « Annuler ». Kaggle ne vérifie le quota qu'au démarrage d'une session ; une session commencée avec du
    quota peut le dépasser, une fois par semaine ;
  - **Attendre la remise à zéro** : sans dialogue ;
  - **Lancer quand même** : sans dialogue.
- **Attente** : la demande porte `attendre_jusqu_a` (remise à zéro + 2 min). L'exécuteur détaché ne la lance qu'à cette
  heure-là, ou plus tôt sur la marque `lancer` (bouton « Lancer maintenant »). Les travaux prêts passent devant. Tant
  qu'un travail attend, l'exécuteur garde la file et la relit toutes les 3 s, fenêtre fermée comprise.
- **Alerte** sous 2 h restantes (réglable) : bannière des pages Doubler et État, et un message une fois par semaine.
- **Secours** (`voixla/secours.py`, préférence `secours`) : point d'accroche pour doubler ailleurs quand le quota ne
  suffit plus, ancien PC par SSH ou RunPod. Rien n'est branché : un moyen prêt ajoutera « Doubler sur … » au dialogue
  du quota, et sa demande transformée passera par la même file.

## File d'attente Kaggle

La section « Sur Kaggle » de la page Travaux (et une ligne « File du compte » dans la page État) montre ce qui attend
ou tourne sur le compte Kaggle, y compris les notebooks d'un autre projet : quand Kaggle ne donne plus de GPU, toutes
les sessions du compte restent « queued » et c'est le seul endroit où on le voit. Le code est dans
`voixla/kaggle_file.py`, sans GTK.

- **Ce que Kaggle permet de voir** : la liste des notebooks du compte (`kaggle kernels list --mine --sort-by dateRun -v`,
  les 12 derniers lancés) et l'état du dernier lancement de chacun (`kaggle kernels status <réf>` : en attente d'un
  GPU, en cours, arrêt demandé, terminé, échec, arrêté), seulement pour ceux lancés depuis moins de 24 h.
- **Ce que Kaggle ne donne pas** : la place dans sa file, l'heure de démarrage prévue, la machine d'un notebook d'un
  autre projet, ni de quoi annuler une version (`kaggle kernels delete` supprime tout le notebook : l'interface ne
  l'appelle jamais). Le bouton de chaque ligne ouvre la page du notebook sur Kaggle, où l'on arrête la session à la
  main. Une phrase d'aide le dit sous la liste et rappelle que, si rien ne démarre, la cause peut aussi être un incident
  chez Kaggle (la page du notebook affiche alors une bannière d'incident) ; quand plusieurs sessions attendent en même
  temps, elle ajoute que Kaggle peut les faire passer une par une, ou n'avoir plus de GPU libre. Le message d'une
  attente anormale d'un travail (`travaux.texte_attente_anormale`) dit la même chose.
- **Affichage** : une ligne par notebook (titre, état en clair, « lancé il y a 1 h 32 », badge « Voix-là » sur celui
  de doubler, reconnu par le champ `noyau` de `session-en-cours.json` et `session-precedente.json` et par le noyau de
  la configuration) ; les terminés récents en repli ; « Rien en attente ni en cours » si le compte est au repos ; un
  bouton « Actualiser » et l'heure de la dernière lecture. L'alerte d'attente anormale et « Abandonner la session »
  des cartes restent propres au travail de la file : la section, elle, voit tout le compte.
- **Coût** : un tour = 1 appel `list` + au plus 5 appels `status` (3 en parallèle), donc 6 appels par minute au plus
  tant que la page est ouverte ; le cache est partagé entre les pages (un tour toutes les 60 s, et seulement page
  visible ou travail Voix-là en cours). « Actualiser » peut forcer un tour, pas plus d'un toutes les 20 s. Rien ne
  tourne dans le fil de l'interface ; chaque appel a un délai maxi (30 s pour la liste, 20 s par état).
- **Erreurs** : sans commande `kaggle`, sans réseau ou sur une réponse illisible, la section affiche « Kaggle
  indisponible : <raison> » ; un état qui ne se lit pas apparaît « État illisible » sans cacher les autres.
- **Essais** : `tests/test_kaggle_file.py`, avec la fausse commande `tests/faux_kaggle.py` (`FAUX_KAGGLE_NOYAUX`,
  `FAUX_KAGGLE_LENT`, `FAUX_KAGGLE_LISTE_BRUTE`) ; capture avec des notebooks inventés :
  `VOIXLA_KAGGLE=tests/faux_kaggle.py FAUX_KAGGLE_NOYAUX=<fichier.json> outils/capturer.sh <png> clair travaux:kaggle`.

## Où sont les choses

- **Données** (`voix/`, `sorties/`, `Vidéos/`) : `VOIXLA_DONNEES`, sinon la clé `donnees` de
  `~/.config/voix-la/config.json`, sinon `~/Music/Voix-là`. C'est le même ordre que `doubler`, et jamais à côté du
  code.
- **Préférences de l'interface** : `~/.config/voix-la/interface.json` (réglages par défaut, choix d'une voix par
  chanteur, visualisations et frises, paroles (langue, taille, panneau, sur l'image), volume et dernière position de
  chaque doublage, choix d'affichage de la bibliothèque (vue, tri et regroupement : `bibliotheque`), réglages de
  « Partager… » (`partage`), vidéos sources choisies à la main). L'interface ne touche pas au `config.json` de
  `doubler`, sauf par `doubler --config`.
- **Caches** (`~/.cache/voix-la/`, `VOIXLA_CACHE` pour les essais), chacun purgé à part, les fichiers les moins
  récemment utilisés d'abord : `pistes/` (analyses du son, 500 Mo), `voix/` (ma voix seule, 300 Mo), `videos/`
  (copies vidéo du beat et des voix seules des anciens doublages à une piste, 2 Go par défaut, préférence
  `cache_pistes_mo`), `sous-titres/` (20 Mo), `miniatures/` (images de MPRIS, de la barre de lecture et de la
  bibliothèque, 60 Mo), `partage/` (images d'aperçu et fichiers temporaires de l'export, effacés après usage).
  `proteges.json` liste les fichiers que l'interface lit en ce moment : la purge n'y touche pas. `verrous/` contient
  les verrous des préparations.
- **Journal du préparateur en tâche de fond** : `~/.local/state/voix-la/preparation.log`.
- **Beat et voix seule enregistrés** : à côté des sorties, `<titre> - voix <Nom> (beat).mp3` et
  `<titre> - voix <Nom> (voix seule).mp3` (ou ailleurs, par le sélecteur).
- **Vidéos à partager** : `<dossier du doublage>/partage/`.
- **Captures d'image** : `Images/Voix-là/` (`VOIXLA_IMAGES` pour les essais).
- **Voix par défaut** : celle choisie dans l'interface (page Voix ou Réglages, gardée dans `interface.json`), sinon
  celle de `doubler` (clé `voix` de `config.json`, annoncée par `doubler --voix-liste --json` et
  `doubler --config --json`), sinon la seule voix. Avec plusieurs voix et aucune par défaut, rien n'est présélectionné :
  il faut en choisir une avant de lancer.
- **File des travaux** : `~/.local/state/voix-la/travaux/<id>/`. On y trouve `demande.json`, `etat.json`,
  `journal.txt` et `evenements.jsonl`.
- **Dernière lecture du quota GPU** : `~/.local/state/voix-la/quota-kaggle.json` (utilisé, total, remise à zéro, heure
  de lecture).
- **Enregistrements** :
  - parole brute : `voix/.enregistrements/`, puis `voix/<nom>/enregistrement-….wav` une fois la voix créée ;
  - prises de chant : `voix/<nom>/chant/chant-AAAAMMJJ-HHMMSS.wav`, en 48 kHz et 24 bits. Un autre dossier de prises
    `chant-*.wav` (faites hors de l'interface) peut être compté en lecture seule pour la voix par défaut (Réglages,
    préférence `prises_externes`) ;
  - au téléphone, le FLAC de scrcpy passe par un fichier caché à côté du WAV (`.chant-…-telephone.flac`), effacé
    après la conversion.
- **Votes de l'écoute à l'aveugle** : `banc/ecoute/votes.json` ; pour une comparaison de voix, `votes.json` et
  `correspondance.json` du banc de la comparaison, dans `~/.cache/voix-la/comparaisons/<empreinte>/`.

```json
{"format": 1, "votes": [{"extrait": "refrain", "date": "…", "notes": {"A": 4, "B": 2}, "classement": ["A", "B"],
 "commentaire": "", "correspondance": {"A": "V1", "B": "V0"}}]}
```

## Fonctionnement

- **L'estimation de durée vient de `doubler`.** `moteur.estimation_noyau_s` charge le `doubler.py` livré avec
  l'interface (dépôt ou `/usr/lib/voix-la`) comme module, sans le lancer ni écrire de `.pyc`, et appelle
  `estimer_noyau_s`. Un `doubler.py` absent, illisible ou trop ancien laisse l'interface sur son propre modèle
  (`rapports.noyau_repli`).
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
  Il lance aussi, en tâche de fond, la préparation des pistes séparées des doublages produits. Un travail en attente
  du quota GPU garde l'exécuteur en vie jusqu'à son départ.
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
  `doubler`, `kaggle` (limite de 120 s), `ffprobe`, `ffmpeg`, la liste des résultats (lue dans un fil, puis ajoutée au modèle par paquets) et ses miniatures.

## Essais

```sh
cd interface && nice python3 -m unittest discover -s tests -t .      # tests de la logique (sans écran)
VOIXLA_DOUBLER="python3 tests/faux_doubler.py" python3 -m voixla    # file des travaux avec un faux doubler
outils/capturer.sh /tmp/capture.png sombre "resultats:detail" 1180x800   # capture sous Xvfb (une à la fois)
outils/capturer.sh /tmp/barre.png clair "resultats:detail-chanson:lire+puis-liste" 1180x800 7   # barre de lecture
```

Les captures tournent sous Xvfb, sur un bus de session privé (`dbus-run-session` avec `outils/bus-essai.conf`, sans
aucun service activable). Le lecteur MPRIS d'essai, les notifications et le nom d'application d'essai n'atteignent
donc jamais le bureau. Leur son est coupé. Scénarios utiles :

- dans un résultat (`resultats:detail-<mot>:…`) : `lire`, `pause`, `piste-<clé>`, `puis-liste` ou `puis-<page>`
  (quitter le résultat pendant la lecture), `retour` (y revenir), `fermer` (fermer la fenêtre), `plein-ecran`,
  `masquer` (commandes du plein écran masquées), `cinema`, `panneau`, `sous-titres`, `taille-<nom>`, `oeil` (menu
  « œil » ouvert), `boucle-<a>-<b>`, `vitesse-<v>`, `frises`, `clavier` (vraies touches par xdotool, l'état relevé
  après chacune, une ligne JSON par touche), `partager`, `partager-carre`, `partager-traduction` (le dialogue
  « Partager… » ; exemple :
  `outils/capturer.sh /tmp/p.png clair "resultats:detail-soir:boucle-18.5-50.4+partager" 1280x900 10`).
  `VOIXLA_CAPTURE_ECRAN=1` capture l'écran Xvfb plutôt que la fenêtre : les menus et popovers, surfaces à part, y
  sont ;
- dans la bibliothèque (`resultats:<actions>`, plusieurs avec `+`) : `grille`, `liste`, `deplier`, `selection`,
  `corbeille` et `corbeille-selection` (la confirmation seulement, rien n'est envoyé), `chercher-<mot>`,
  `filtre-<autotune|duo|chant|parole|extrait|entier|voix-<id>>`, `tri-<critère>`, `inverse`, `lire[-<mot>]` (lecture
  muette sans ouvrir le détail), `ouvrir[-<mot>]` (comme un clic sur la carte), `survol` (boutons de lecture visibles),
  `pause-<s>` (la suite s secondes plus tard). Exemple :
  `outils/capturer.sh /tmp/versions.png clair "resultats:grille+lire-lumiere" 860x620 9` ;
- l'enregistrement (`voix:enregistrer[-chant][-telephone][-enregistrement|-controle]`) : `telephone` choisit le
  premier téléphone, avec `VOIXLA_TELEPHONE_ESSAI=1` (`VOIXLA_TELEPHONE_ESSAI=aucun` montre « Aucun téléphone
  connecté » et Réessayer) ;
- le quota : `doubler:exemple-entier+bas` (ligne du quota à côté de « Lancer »), `doubler:quota-dialogue` (dialogue
  quand le doublage ne tient pas), `etat:sessions` (sessions comptées dépliées) ; avec
  `VOIXLA_KAGGLE=tests/faux_kaggle.py` et `FAUX_KAGGLE_QUOTA`.

Les essais MPRIS lancent leur propre `dbus-daemon`, avec la même configuration.

Variables pour les essais :

| Variable | Rôle |
|---|---|
| `VOIXLA_DONNEES` | dossier de données |
| `VOIXLA_CONFIG` | dossier de `config.json` et `interface.json` |
| `VOIXLA_ETAT` | dossier de la file des travaux |
| `VOIXLA_ETAT_DOUBLER` | état de doubler lu par l'interface (verrou commun, `session-*.json`, `outils.json`), à la place de `~/.local/state/voix-la` |
| `VOIXLA_ECOUTE` | banc d'écoute |
| `VOIXLA_CACHE` | caches : analyses du son, ma voix seule, copies vidéo des pistes, sous-titres |
| `VOIXLA_DOUBLER` | commande `doubler` |
| `VOIXLA_KAGGLE` | commande `kaggle` (`tests/faux_kaggle.py` : `quota` et `kernels status` sans réseau ; `FAUX_KAGGLE_QUOTA="27.5,30"`, `FAUX_KAGGLE_ANCIEN=1`, `FAUX_KAGGLE_PANNE=1`) |
| `VOIXLA_MICRO_ESSAI=1` | son de synthèse au lieu du micro |
| `VOIXLA_TELEPHONE_ESSAI=1` | un téléphone imité, « Pixel 7 (essai) » : son de synthèse en temps réel par `ffmpeg`, sans adb ni scrcpy ; `aucun` : aucun téléphone |
| `VOIXLA_SCRCPY`, `VOIXLA_ADB` | chemins de `scrcpy` et d'`adb` (sinon le `PATH`, `~/.local/bin/scrcpy` et l'adb livré à côté de scrcpy) |
| `ADB_SERIAL` | téléphone proposé en premier, et adresse sans fil préremplie (comme le script d'enregistrement) |
| `VOIXLA_APP_ID` | instance distincte de l'application (et lecteur MPRIS `…voixla.essai<pid>`) |
| `VOIXLA_SANS_PREPARATION=1` | ni préparation des pistes à la fin d'un doublage, ni au démarrage |
| `VOIXLA_IMAGES` | dossier des captures d'image du lecteur |
| `VOIXLA_CAPTURE_ECRAN=1` | capture de l'écran Xvfb (menus compris) au lieu de la fenêtre |

Les essais ne lisent jamais Kaggle ni l'état réel de doubler : `EssaiIsole` pose `VOIXLA_KAGGLE` sur une commande
absente et `VOIXLA_ETAT_DOUBLER` sur un dossier temporaire.

Le faux doubler sait corriger un duo (`--remix --passage`, `--affectation`, fichiers `versions/` et `rapport.versions`) :
`FAUX_DOUBLER_SANS_VERSIONS=1` imite un doublage sans les deux versions, `FAUX_DOUBLER_SANS_AFFECTATION=1` un doubler qui
ne connaît pas `--affectation` ; `python3 -m tests.faux_duo DONNEES [--sans-versions]` crée un duo inventé à corriger.
Scénarios de capture : `resultats:detail-<mot>:corriger[-<t>[-<choix>]]` (menu d'un passage, ou choix en attente),
`appliquer`, `revenir-auto`.

Le faux doubler imite les deux GPU (`--gpu`, clés `gpu` et `kaggle.machine`, `temps_local.gpus`) ;
`FAUX_DOUBLER_SANS_GPU=1` imite un doubler d'avant les deux GPU.
`FAUX_DOUBLER_ESTIMATION=<s>` fixe la durée de session qu'il annonce (`estimation_s`).

`test_telephone` passe par un faux adb et un faux scrcpy (`tests/faux_telephone.py`) : liste d'appareils,
`adb connect`, aide d'un scrcpy récent ou ancien, prise écrite à la fin (sur SIGINT ou à `--time-limit`), prise vide,
échec, scrcpy qui ignore SIGINT. Aucun téléphone ni réseau.

`test_pistes_dans_la_video` lit en GStreamer dans un processus à part (`fakeaudiosink`, aucun son), et le lecteur
sous Xvfb, sur le bus privé.

`test_lecteur_complet` lit sous Xvfb, sans son, deux petites vidéos de synthèse (sinus à 440, 660 et 550 Hz) : la
hauteur gardée à 0,5× et 2×, la boucle A-B à travers une bascule dans le même flux et vers un autre fichier, l'image par
image, le volume ; puis l'application entière au clavier (xdotool), touche par touche, dans le détail et en plein écran.

`tests/fausse_bibliotheque.py` crée une bibliothèque aux titres et voix inventés (`python3 -m tests.fausse_bibliotheque
DONNEES` : les 14 doublages des captures ; `--n 200` : 200 doublages ; `--sans-videos` : médias vides).
`test_bibliotheque` vérifie le modèle, les miniatures 16:9 et la page elle-même dans la vraie fenêtre, sous Xvfb
(`tests/essai_bibliotheque_gui.py`). `python3 -m tests.mesure_bibliotheque DOSSIER [N]`, sous Xvfb, mesure
l'ouverture, les miniatures et le défilement avec N doublages.

`python3 -m tests.faux_partage DONNEES` : un doublage inventé à cinq pistes (sons purs de fréquences connues), paroles
avec refrain et traduction. `test_export_social` vérifie l'export (de bout en bout avec x264 « veryfast ») et, sous
Xvfb, le dialogue (`tests/essai_partage_gui.py`).

`test_sous_titres_gst` fait doubler deux vidéos à image unie (un mp4 dont la vidéo d'origine a ses sous-titres, et un
mkv) avec leurs pistes de paroles. Sous Xvfb, un `Gtk.MediaFile` brut dessine le texte (témoin) et un flux du lecteur
n'en dessine aucun ; le lecteur de la page Résultats n'a aucun pixel de texte à l'écran après une bascule de piste
audio, des déplacements, le passage par la vidéo d'origine, la fenêtre fermée puis rouverte, la fermeture et la
réouverture. `VOIXLA_ESSAI_CAPTURES=DOSSIER` garde les captures.

## Limites

- **Chanson transposée** : `doubler` transpose la musique seule de la vidéo (`travail/instrumental_transpose_<dt>.wav`) et
  les cinq pistes de la vidéo sont justes. Mais le Karaoké, le partage, les pistes séparées reconstruites par l'interface et
  « Enregistrer le beat… » lisent encore `travail/instrumental.wav`, la musique d'origine, non transposée : sur une
  chanson transposée, ce beat n'est pas dans la tonalité de la voix doublée. Le remix rapide suit la décision déjà
  prise : il ne peut pas la changer sans reconvertir. L'estimation de durée de la session Kaggle ne compte pas la
  transposition de la musique, faite sur le PC après le noyau (environ 0,15 s par seconde de chanson, un seul cœur) : elle
  change à peine.
- **Enregistrement** : par un micro du PC (PulseAudio/PipeWire) ou par le micro d'un téléphone Android (scrcpy, Android
  11 ou plus récent pour le son). Au téléphone, pas de niveau en direct, et la prise ne commence qu'une fois le
  téléphone joint par scrcpy, juste après le compte à rebours.
- **Pistes en vidéo** : le doublage et l'original gardent chacun leur lecteur ouvert jusqu'à ce qu'on quitte le
  résultat, ou jusqu'à la fin de sa lecture s'il joue en arrière-plan ; le beat et les voix seules ferment le leur
  quand on les quitte. Si la vidéo source a été déplacée,
  l'original se lit sans image, d'après `travail/original.wav`.
- **Copies vidéo des pistes** : chacune pèse à peu près le poids de la vidéo doublée. Une vidéo doublée plus lourde
  que tout le cache (2 Go par défaut, un film entier par exemple) n'est pas copiée : ses pistes se lisent sous l'image
  arrêtée. Agrandir le cache dans les Réglages lève cette limite. Une piste choisie avant la fin de sa préparation à
  l'avance attend encore sa copie quelques secondes, sur l'image arrêtée.
- **Ma voix seule** : le limiteur du mix agit sur la musique et la voix ensemble. Quand la musique le fait travailler,
  la voix du mix est un peu plus comprimée que la voix seule, où il ne voit que la voix.
- **Paroles tirées des sous-titres** : ce sont ceux de la vidéo, souvent une traduction, et pas forcément le texte
  chanté. Sur un extrait, seuls les sous-titres copiés par `doubler` sont présents.
- **Renommer l'identifiant d'une voix** : les doublages déjà faits gardent l'ancien dossier `travail/<ancien>/`, et
  leur remix ne retrouve plus la voix. Changer seulement le nom affiché ne pose pas ce problème.
- **Fenêtre fermée** : les travaux en attente démarrent quand même, car l'exécuteur détaché les enchaîne. Ils ne
  s'affichent qu'à la réouverture.
- **Lecture fenêtre fermée** : l'application reste en mémoire, comme fenêtre ouverte (environ 400 Mo). Sans MP3, une
  vidéo doublée continue de se décoder. Une pause de plus de 15 min fait quitter.
- **MPRIS** : pas de file, donc ni piste suivante ni précédente. L'écoute à l'aveugle n'a pas d'image.
- **Quota GPU** : après un redémarrage du PC, un travail en attente du quota ne repart qu'une fois l'interface rouverte
  (l'exécuteur est relancé à l'ouverture). Sans `kaggle quota`, le compte local ne voit que ce poste.
