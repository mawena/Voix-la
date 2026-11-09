# banc/realisme : le juge d'identité et les outils de réalisme

Outils CPU pour mesurer si un doublage « sonne comme » la voix cible, sans écouter à la main. Tout est local, rien
n'est envoyé sur le réseau (hors téléchargement des poids publics au premier lancement). Les résultats chiffrés ne
sont jamais dans le code : ils vont dans `banc/resultats/realisme/` (dossier ignoré par git).

## Contenu

| Fichier | Rôle |
|---|---|
| `juge.py` | Similarité de locuteur par deux encodeurs indépendants : cosinus par clip et par encodeur, rang parmi des leurres, verdict contre un seuil d'égale erreur calibré. |
| `fantome.py` | Voix d'origine restée dans la musique seule : similarité de locuteur sur la bande vocale et énergie de 1 à 4 kHz aux instants des syllabes. Rend un score et les moments suspects. |
| `tessiture.py` | Tessiture sur des enregistrements a cappella (pyin), centiles et zone de confort, et transposition entière d'une chanson vers cette zone. |
| `test_proches.py` | Fabrique la page « Est-ce lui ? » (HTML local, sans réseau, utilisable sur téléphone) à partir d'un manifeste. |
| `depouiller.py` | Lit les réponses exportées par la page et rend le taux de « oui » par condition. |
| `page/modele.html` | Modèle de la page. |
| `tests/` | Tests sans modèle ni réseau : `python -m pytest banc/realisme/tests`. |

## Installation

```
python3 -m venv ~/voixla-realisme-venv
~/voixla-realisme-venv/bin/pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
~/voixla-realisme-venv/bin/pip install transformers speechbrain librosa soundfile scipy numpy pyarrow pytest
```

`ffmpeg` est nécessaire pour `test_proches.py` (extraits mp3).

Versions utilisées pour les mesures : Python 3.12, torch 2.15 (CPU), torchaudio 2.11, transformers 5.22,
speechbrain 1.1, huggingface_hub 2.4, librosa 1.0, numpy 2.5, scipy 1.18, soundfile 0.14, pyarrow 26.

## Encodeurs et licences

CAMPPlus est exclu : le moteur de conversion l'utilise, le juge serait biaisé en sa faveur.

| Nom | Modèle | Licence |
|---|---|---|
| `wavlm-sv` | `microsoft/wavlm-base-plus-sv` (transformers) | La fiche du modèle n'indique aucune licence : à usage de recherche et de banc seulement, ne rien redistribuer. |
| `ecapa` | `speechbrain/spkrec-ecapa-voxceleb` (ECAPA-TDNN) | Apache-2.0. |

Leurres : voix d'hommes chantées du corpus VocalSet (CC BY 4.0, extraites par `juge.py leurres-vocalset`), plus des
voix isolées d'autres chansons. Les poids sont mis en cache dans `~/.cache/huggingface` et `~/.cache/voixla-realisme`.

## Utilisation

Calibrer (seuil d'égale erreur, un par encodeur ; écrit un JSON et un `.npz` à côté) :

```
python banc/realisme/juge.py calibrer --chant D_chant --parole D_parole --leurres D_leurres --sortie calib.json
```

Sans dossier de chant, la calibration est marquée « provisoire » : les positifs sont alors des extraits de parole,
et le seuil est à refaire dès que le chant a cappella existe.

Juger des extraits contre des références, ou passer le juge sur des doublages existants :

```
python banc/realisme/juge.py juger  --refs D_refs --clips F_OU_D... --calib calib.json
python banc/realisme/juge.py valider --sorties D_sorties --refs D_refs --calib calib.json --md resultat.md
```

`valider` attend, par chanson, `travail/<voix>/voix_convertie.flac` et `travail/voix_source.wav` ; il rend la
ressemblance à la cible et la ressemblance au chanteur d'origine.

Fantôme (musique seule = `instrumental.wav`, voix d'origine = `voix_source.wav`) :

```
python banc/realisme/fantome.py analyser --musique M.wav --voix V.wav --controles autres_voix.wav...
python banc/realisme/fantome.py lot --sorties D_sorties --md fantome.md
```

Un z élevé n'est pas une preuve : c'est un classement des moments à écouter.

Tessiture (sortie JSON : `p05`, `p10`, `p50`, `p90`, `p95`, `confort` = [bas, haut], en Hz, plus le détail en notes) :

```
python banc/realisme/tessiture.py mesurer --audio D_acappella --sortie tessiture.json
python banc/realisme/tessiture.py transposer --tessiture tessiture.json --voix-chanson voix.wav
```

Page de test des proches, puis dépouillement :

```
python banc/realisme/test_proches.py --manifeste m.json --sortie D
python banc/realisme/depouiller.py --cles D/cles.json --reponses reponses-*.json
```

Donner à chacun le dossier `D/test/` (la clé `D/cles.json` reste chez soi). Chaque extrait est à 8 à 10 s de voix
seule, mélangé au hasard ; réponse oui / non / je ne sais pas, puis confiance de 1 à 5 ; la page exporte un JSON.

## Ressources

Les modèles tournent sur CPU (`JUGE_THREADS`, 4 par défaut, 2 à 3 Go de RAM). Sur le poste partagé, passer par
`file-attente`.

## Licence

MIT, comme le reste du projet.
