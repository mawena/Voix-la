# Transposition de la chanson, mixage v3 et session GPU « réalisme »

Prototypes du banc (rapport de recherche sur le mixage, §§ 6 et 8, et audit A2, A3, A4). Rien ici ne touche `doubler.py`,
le noyau de production ni l'interface. Tout se fait sur le PC, sans GPU, sauf la session Kaggle qui reste à lancer à la main.

Décision de principe : **par défaut, une chanson hors de la tessiture de la voix cible est transposée en entier** (musique
comprise, comme un changement de tonalité au karaoké), au lieu de décaler seulement la voix de ±12 demi-tons.

| Fichier | Rôle |
|---|---|
| `transposition.py` | musique seule transposée (Rubber Band R3), mesures de qualité, règle du décalage de la voix |
| `mixage_v3.py` | mixage v3 et variantes d'écoute à l'aveugle |
| `noyau_realisme.py` | session GPU du banc (étape « realisme ») et son lanceur local |
| `tests/test_transpo.py`, `tests/test_mix_v3.py`, `tests/test_noyau_real.py` | essais sur sons synthétiques, sans modèle ni GPU |

Environnement : `~/voixla-realisme-venv` (`numpy`, `scipy`, `soundfile`, `pyloudnorm`) ; `librubberband2` (3.3.0) et `ffmpeg`
du système. Essais : `~/voixla-realisme-venv/bin/python -m unittest discover -s banc/realisme/tests -p "test_*.py"`
(à passer par `file-attente` si le poste est chargé).

## 1. Transposition de la musique seule

### Outil retenu : Rubber Band R3 par `ctypes`

- Le filtre `rubberband` de ffmpeg n'offre **aucune option de moteur** (tempo, pitch, transients, detector, phase, window,
  smoothing, formant, pitchq, channels) : il ne sait faire que R2. Écarté.
- Le binaire `rubberband` (paquet `rubberband-cli`) n'est pas installé ; l'installer demanderait `sudo`, ce n'est pas fait.
- `librubberband.so.2` (3.3.0) est là : son API C (`rubberband_new`, `study`, `process`, `retrieve`) s'appelle par `ctypes`,
  sans en-têtes ni compilation. `OptionEngineFiner` donne R3 ; la bibliothèque confirme `engine_version == 3`
  (`transposition.version_moteur`). Hors ligne, rapport de temps 1,0, formants non préservés (instruments), un seul cœur
  (`ThreadingNever`), sortie coupée ou complétée à la longueur d'entrée : durée exacte à l'échantillon près.
- R2 (`OptionEngineFaster`, même bibliothèque) sert de comparaison. Aucune liaison Python (pylibrb) n'a été nécessaire.

```
python3 banc/realisme/transposition.py transposer musique.wav musique_p2.wav 2          # R3, +2 demi-tons
python3 banc/realisme/transposition.py mesurer musique.wav --debut 60 --duree 30 --json mesures.json
```

### Mesures : deux musiques seules réelles, extraits de 30 s, ±1, ±2, ±3, ±5 demi-tons

Musique 1 : ballade avec batterie. Musique 2 : morceau à rythmique marquée. Valeurs « musique 1 / musique 2 ».
- **Écart spectral** : écart moyen absolu (dB) entre les spectrogrammes en tiers d'octave de l'original et de l'aller-retour
  (+n puis −n), sur les trames actives. 0 = identique.
- **Attaques** : corrélation de l'enveloppe d'attaque (hausses d'énergie par tiers d'octave) entre l'original et le décalé
  (1 = attaques aux mêmes instants), et force du centile 99 de cette enveloppe (< 1 = attaques adoucies).
- **Aigus** : variation de l'énergie au-dessus de 10 kHz après aller-retour. **Niveau** : variation du RMS, en dB.

| décalage | moteur | écart spectral (dB) | corrélation des attaques | force des attaques | aigus > 10 kHz (dB) | niveau (dB) | calcul (s / 30 s) |
|---|---|---|---|---|---|---|---|
| ±1 | R3 | 1,04–1,06 / 1,65–1,71 | 0,97 / 0,97 | 0,94–0,97 / 0,92–0,93 | −0,8 / −0,9 | −0,1 / −0,5 | 2,2–3,1 |
| ±1 | R2 | 2,11–2,19 / 2,04–2,28 | 0,91–0,93 / 0,94–0,95 | 0,86–0,95 / 0,81–0,93 | −1,3 / −1,3 | −0,5 / −0,4 | 1,3–1,8 |
| ±2 | R3 | 1,02–1,10 / 1,61–1,72 | 0,97 / 0,98 | 0,93–1,03 / 0,93–0,99 | −0,9 à −1,1 / −1,1 à −1,2 | −0,1 / −0,5 | 2,7–3,2 |
| ±2 | R2 | 2,65–2,88 / 2,92–3,30 | 0,88–0,89 / 0,89–0,91 | 0,81–0,91 / 0,75–0,94 | −1,4 à −1,5 / −1,5 à −1,6 | −0,9 / −0,9 à −1,0 | 1,4–1,5 |
| ±3 | R3 | 1,03–1,11 / 1,53–1,72 | 0,97 / 0,97–0,98 | 0,93–0,99 / 0,91–0,94 | −1,2 à −1,3 / −1,3 à −1,4 | −0,1 / −0,5 | 2,6–3,2 |
| ±3 | R2 | 2,98–3,31 / 3,50–3,93 | 0,85 / 0,85–0,86 | 0,82–0,85 / 0,70–0,93 | −1,4 à −1,5 / −1,6 à −1,7 | −1,1 / −1,5 | 1,4–1,5 |
| ±5 | R3 | 1,21–1,35 / 1,75–2,02 | 0,96–0,97 / 0,98 | 0,89–0,93 / 0,84–0,86 | −1,8 à −2,1 / −1,9 à −2,3 | −0,1 / −0,5 à −0,6 | 2,5–4,3 |
| ±5 | R2 | 3,50–3,97 / 4,08–4,61 | 0,82–0,83 / 0,79 | 0,76–0,79 / 0,63–0,86 | −1,6 à −2,1 / −1,8 à −2,4 | −1,4 / −2,6 | 1,6–1,9 |

À retenir :
- **R3 divise l'écart spectral par 2 à 3 par rapport à R2** (moyenne 1,4 dB contre 3,1 dB) et garde les attaques
  (corrélation 0,96–0,98 contre 0,79–0,95). L'écart de R3 reste presque plat de ±1 à ±5 ; celui de R2 monte avec l'intervalle.
- R2 perd du niveau (jusqu'à −2,6 dB à ±5, signe de transitoires et de graves affaiblis) ; R3 reste à −0,1 / −0,5 dB.
- Les aigus au-dessus de 10 kHz baissent d'environ 1 à 2 dB pour les deux moteurs : c'est la limite de l'aller-retour.
- Durée : 0 échantillon d'écart dans tous les cas. Accord : à ±6 cents (limite de la mesure, un demi-tons vaut 100 cents).
- Coût : R3 environ 0,1 s de calcul par seconde de musique sur un cœur, R2 environ 0,05 : une chanson de 4 min passe en
  25 s. Le calcul ne gêne donc pas : **R3 pour tout**.
- Les attaques d'une batterie se perdent un peu plus (force 0,84–0,86 à ±5 sur la musique rythmée) : au-delà de ±5 dt,
  prévoir de transposer sans la batterie. La règle plafonne la transposition de la musique à ±5 (`DEMITONS_MUSIQUE_MAX`).

## 2. Règle du décalage de la voix quand la chanson est transposée

Entrées : centiles de F0 de la voix d'origine (`p10`, `p50`, `p90`, en Hz, RMVPE) et JSON de tessiture de la voix cible
(sortie de `tessiture.py`) :

```json
{"p05": 98.0, "p10": 110.0, "p50": 165.0, "p90": 247.0, "p95": 277.0, "confort": [123.0, 220.0]}
```

Règle (`transposition.regle_decalage`) :

1. On cherche le décalage **entier** `d` de la voix (demi-tons, de −24 à +24) qui met les centiles `p10` et `p90` de la voix
   convertie dans la zone `confort` et sa médiane près du centre (géométrique) de la zone.
2. La musique n'est transposée que de **T = d ramené dans [−6, +6]** (`d` modulo 12) : la voix reste dans la tonalité de la
   musique et `d − T` est un nombre entier d'octaves. **Décalage de la voix = transposition de la musique + octave.**
3. Coût = 3 × débordement de la zone (demi-tons) + 0,5 × écart de la médiane au centre + 0,15 × |T| + 0,5 × |octaves| ;
   |T| est borné à 5 (la musique ne se transpose pas au-delà). À coût égal, le plus petit décalage.
4. Sans transposition (`transposer_musique=False`), seules les octaves restent : c'est le comportement actuel, sans la borne
   de ±12. L'essai montre que la transposition ne fait jamais moins bien.

Sorties : `decalage_voix_dt`, `transposition_musique_dt`, `octave_voix`, F0 de la voix convertie, `dans_confort`, débordement,
et trois alternatives. Exemples avec une tessiture inventée (à remplacer par la mesure de la voix cible) :

| Voix d'origine (p10 / p50 / p90) | Voix | Musique | Octave | Voix convertie (p10 / p90) |
|---|---|---|---|---|
| 245 / 295 / 411 Hz (ténor aigu) | −11 | +1 | −1 | 130 / 218 Hz, dans la zone |
| 235 / 296 / 307 Hz (voix de femme) | −10 | +2 | −1 | 132 / 172 Hz, dans la zone |
| 186 / 219 / 299 Hz (voix médium) | −5 | −5 | 0 | 139 / 224 Hz, 0,3 dt au-dessus (musique bornée à ±5) |

```
python3 banc/realisme/transposition.py regle tessiture.json --p10 245 --p50 295 --p90 411
python3 banc/realisme/transposition.py regle tessiture.json --p10 245 --p50 295 --p90 411 --sans-transposition
```

### Repli : un décalage par phrase

`decalage_par_phrase(phrases, tessiture, decalage_global, ecart_max=3, silence_min_s=0.25, pas_max=2, lissage=0.6)` :
- chaque phrase (`debut_s`, `fin_s`, `p10`, `p50`, `p90`) reçoit un décalage entier à `ecart_max` demi-tons au plus du décalage
  global ;
- le décalage **ne change qu'à un silence** d'au moins `silence_min_s` entre deux phrases, d'au plus `pas_max` demi-tons, et
  chaque demi-ton de changement coûte `lissage` (continuité) ; programmation dynamique (Viterbi) ;
- `phrases_depuis_f0(temps, f0)` découpe une courbe de F0 en phrases.
Limite : si la musique n'est pas transposée de la même façon à chaque phrase, la voix sort de la tonalité de la musique
(décalage non multiple de 12 par rapport à elle). C'est pourquoi la règle globale reste la voie par défaut.

## 3. Session GPU « réalisme » (`noyau_realisme.py`)

Étape « realisme » ajoutée au noyau du banc, **sans modifier `banc.py` ni `noyau_banc.py`** : le lanceur local construit le
noyau (noyau de doubler, `noyau_banc.py`, `transposition.py` embarqué, puis ce code, avec `principal` redéfini) et le passe à
`banc.lancer`. **Rien n'a été lancé** : seuls la construction, la compilation et la simulation ont été faites.

Par extrait et par variante (voir l'en-tête de `noyau_realisme.py`) :
- **(a) décalage arbitraire en demi-tons** : `decalage_dt` (entier, `defaut` = celui de l'extrait, ou `regle`) ; `musique_dt`
  (`auto` = la voix moins son octave entière) est écrit dans la fiche. Après rapatriement,
  `noyau_realisme.py musiques <session>` transpose la musique seule de chaque extrait de la même façon (R3, sur le PC) :
  `<variante>_musique.wav`. La musique ne part pas sur Kaggle : le noyau ne convertit que la voix.
- **(b) références multiples par registre** : `registres: [{nom, reference, centre_hz?}]`. Le centre de chaque référence est la
  médiane de son F0 RMVPE (ou `centre_hz`). Pour chaque morceau, la référence au centre le plus proche de
  F0 médian de la source + décalage est choisie (`choisir_reference`) ; l'usage est écrit dans la fiche. Les références sont
  les `<cible>-reference-<lettre>.wav` du jeu (`voix.references_en_plus` de `cas.json`). Une référence absente saute la variante
  (fiche `sautee`), la session continue.
- **(c) résidu de style source** : `style_r` = échelle appliquée au résidu de style calculé sur la voix SOURCE (1 = actuel,
  0,5 = atténué, 0 = coupé : `style_r=None`). Le style de la RÉFÉRENCE n'est jamais touché.
- **(d) balayage de cfg** : `balayage: {"cfg": [0.5, 0.7, 1.0, 1.5]}` (produit cartésien possible avec `style_r`). Une variante
  de signature identique à une précédente n'est pas reconvertie (alias : 0,7 = réglage de `Ra`).

Sorties : mêmes fichiers que l'étape « convertir » du banc (`<variante>.flac`, `<variante>.json` de type `version`), donc
`banc.py rapatrier`, `tableau` et `mixer` les lisent. Pas d'étape « juger » dans la session : celle du banc compare le F0 au
décalage de l'**extrait**, faux pour des variantes qui ont chacune le leur. Juger les `.flac` rapatriés avec le juge local.

Modèle de session : `banc/sessions/realisme.json` (ignoré par git, comme `s1.json` ; régénérable par
`noyau_realisme.py session-modele`). Variantes : `R0` (témoin), `Ra` (règle + musique transposée), `Rb` (registres B grave,
A médian, C aigu), `Rs` (style 0,5 et 0), `Rc` (cfg 0,5, 0,7, 1, 1,5). Dont 8 conversions par extrait ; 2 extraits dans le
`cas.json` actuel, environ 460 s à convertir, **environ 34 min de GPU** estimées (conversion à 3,04 de temps réel plus 10 min de
chargement ; limite de la session 90 min). `Rb` ne partira que quand les références B et C existeront.
La tessiture de la session est INVENTÉE : la remplacer par celle de `tessiture.py` (`reglages.tessiture`).

```
python3 banc/realisme/noyau_realisme.py simuler realisme     # plan, GPU estimé, noyau compilé ; ne lance rien
python3 banc/banc.py preparer --extraits-seulement           # si les extraits ne sont pas déjà dans banc/travail/
python3 banc/banc.py envoyer                                  # jeu de données (références, extraits)
python3 banc/realisme/noyau_realisme.py lancer realisme      # banc.py lancer, avec ce noyau (verrou Kaggle compris)
python3 banc/banc.py rapatrier realisme
python3 banc/realisme/noyau_realisme.py musiques realisme    # musique transposée de chaque variante
```

Variante C du mixage : prendre la fiche de `Ra` (décalage de la voix, `musique_dt`) et son `.flac` ; voir § 4.

## 4. Mixage v3 (`mixage_v3.py`)

Pistes : musique seule (`instrumental.wav`), voix convertie (`voix_convertie.flac`), voix d'origine isolée (`voix_source.wav`),
original (`original.wav`), et en option la piste de reverb retirée (`reverb_source.wav`) et les chœurs convertis à part.

Chaîne de la voix (rapport § 8) :
1. passe-haut 90 Hz ;
2. **EQ douce** : différence des spectres moyens (voix d'origine moins voix convertie, tiers d'octave, 150 Hz à 10 kHz), lissée
   sur une octave (la forme à grande échelle seulement, pas les creux fins du chanteur d'origine), **±3 dB au plus, et ±2 dB au
   plus entre 250 Hz et 4 kHz** (`eq_plafond_db` : 3, 1,5 ou 0) ; FIR à phase linéaire ;
3. compression douce 2:1, seuil −20 dB, attaque 15 ms, relâchement 150 ms ;
4. **dé-esseur à bande** 5–9 kHz : seuil = centile 90 du rapport sibilantes/voyelles de la voix d'origine, réduction de la seule
   bande, au plus 6 dB ;
5. **niveau** : le niveau actif de la voix suit, par segments de 3 s lissés sur 1 s, le rapport voix/musique de l'original
   + `voix_db` (1 dB par défaut). Voulu = niveau de la musique (éventuellement transposée) + rapport de l'original + `voix_db`.

Reverb neuve sur la voix seule (jamais sur la musique) :
- **RT60 mesuré** par décroissances libres de l'enveloppe (200 Hz à 4 kHz) : fin d'un son, 30 ms de saut, droite sur 15 dB,
  RT60 = −60 / pente, médiane des ajustements corrects (R² > 0,85) ; **plafonné à 2 s**. Sur la piste de reverb retirée si elle
  existe, sinon sur la voix d'origine isolée.
- **pré-délai** : intercorrélation des hausses d'enveloppe de la voix sèche et de la piste de reverb. **Sans cette piste, il n'est
  pas mesurable** : valeur par défaut de 30 ms, signalée (`predelai_mesure: false`).
- rapport humide/sec : mesuré avec la piste de reverb (borné à −25 / −13 dB), sinon −17 dB ; jamais plus fort que −13 dB.
- RIR stéréo décorrélée, queue exponentielle, aigus deux fois plus vite, passe-haut 400 Hz et passe-bas 7 kHz ; +2 dB en fin
  de phrase.

Chœurs (s'il y a une piste à part) : −6 dB sous la voix principale, stéréo large (deux filtres passe-tout à phase aléatoire),
reverb +3 dB et plus sombre. Voix principale mono au centre.

Mastering : loudness intégré (BS.1770, `pyloudnorm`) **égal à celui de l'original de l'extrait**, limiteur −1 dBTP (détection ×4,
anticipation 5 ms), contrôlé après limiteur. Même loudness pour toutes les variantes (à ±0,05 LU, vérifié sur les mp3 avec
`ffmpeg ebur128`). Voix seule (`<lettre>_voix.mp3`) : voix + reverb + chœurs, au niveau du mix.

Option de **musique transposée** : `Chanson.mix_v3(musique=<musique transposée>, voix=<voix reconvertie>)` ; le rapport de niveau
suit alors la musique transposée. `produire --tessiture <json>` prépare `pret_C/<extrait>/` (règle, musique transposée R3 de la
fenêtre de l'extrait, instructions).

### Variantes (lettres du rapport) et ce qui est produit ici

| Variante | Contenu | Produite |
|---|---|---|
| A | mix v2 actuel (témoin) : `seche_voix_convertie.wav` + `reverb_voix_convertie.wav` aux gains du rapport | oui |
| B | v3 (voix +1 dB, EQ ±3 / ±2 dB, reverb mesurée) | oui |
| C | v3 + musique transposée | **non : voix à reconvertir au décalage de la règle (GPU)** ; `pret_C/` prêt |
| D | v3, RIR par déconvolution (`rir_par_deconvolution`) | non : exige la piste de reverb retirée, absente de `travail/` |
| E | v3 + 25 % de la reverb d'origine | non : idem |
| F | v3 sans reverb | oui |
| G | voix +4 / +1 / 0 dB | oui (G4, B, G0) |
| H | correction de hauteur légère | non (hors de ce prototype) |
| I | EQ plafonnée à 3 / 1,5 / 0 dB | oui (B, I15, I0) |

Sept versions par extrait sont donc écoutables : A, B, F, G4, G0, I15, I0.

```
python3 banc/realisme/mixage_v3.py produire <extrait> <dossier travail/> <sous-dossier de la voix> \
    --sortie banc/resultats/realisme/mixage --duree 60 [--debut 14.2] [--tessiture tessiture.json] \
    [--humide reverb_source.wav] [--choeurs choeurs.flac] [--musique-transposee m.wav --voix-transposee v.flac]
```

Sorties dans `banc/resultats/realisme/mixage/` (ignoré par git) :
- `ecoute/<extrait>/<lettre>.mp3`, `<lettre>_voix.mp3`, `original.mp3` : le format de `banc/ecoute/` (l'Écoute à l'aveugle
  de l'interface les lit ; la pointer sur ce dossier avec `VOIXLA_ECOUTE`) ; lettres tirées au hasard, indépendamment pour
  chaque extrait ; fenêtre de 60 s où la voix est la plus présente ;
- `correspondance.json` (et `correspondances/<extrait>.json`) : lettre → variante, **hors du dossier `ecoute/`** : l'interface
  ne la révèle donc pas avant le vote ;
- `mesures/<extrait>.json` : loudness et crête vraie de chaque variante, réglages mesurés (RT60, pré-délai, EQ, dé-esseur,
  niveau), règle de décalage ; `pret_C/<extrait>/` : musique transposée, `regle.json`, `LISEZMOI.txt`.

Trois doublages existants ont été remixés : une chanteuse (duo), un chanteur aigu, un chanteur médium (extraits `chanteuse`,
`aigu`, `autre`). Mesures notables : RT60 de la voix d'origine de 0,25 à 0,49 s ; réduction maximale du limiteur de 1,6 à 7,6 dB
sur des crêtes isolées (les originaux sont très forts, de −13,3 à −9,1 LUFS) ; EQ d'au plus 3,0 dB, et 2,0 dB entre 250 Hz et 4 kHz.

## 5. Ce qui reste

- **GPU** : la session « réalisme » (§ 3), puis la variante C du mixage (voix reconvertie au décalage de la règle, mixée
  avec la musique transposée).
- **Enregistrement de la voix cible** : références chantées par registre (B grave, C aigu) pour la variante `Rb`, et sa tessiture
  mesurée pour remplacer la tessiture inventée.
- **Données manquantes dans `travail/`** : la piste de reverb retirée (donne D, E, le pré-délai mesuré et le rapport
  humide/sec) et les chœurs convertis à part (donne le placement stéréo et le −6 dB). Sans eux, ces deux étapes du mixage sont
  prêtes et testées mais ne s'appliquent pas aux doublages actuels.
- Pas de `sudo` demandé : `rubberband-cli` reste facultatif (Rubber Band est appelé par la bibliothèque).
