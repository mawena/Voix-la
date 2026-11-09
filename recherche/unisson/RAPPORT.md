# Recherche : deux chanteurs qui chantent ensemble

Nouveauté 7 de la feuille de route. Recherche du 06/11/2026, sans intégration dans `doubler`.

## Le problème

Quand deux chanteurs chantent **ensemble** (unisson, octave, harmonie serrée), le Roformer karaoké du nettoyage les
laisse tous les deux dans la voix principale. Cette piste est alors convertie d'un bloc, dans une seule voix : le
chanteur prend la voix de la chanteuse (ou l'inverse) sur ces passages. Sur un duo homme-femme réel de 3 min 47,
le dernier doublage n'a vu que 5 s « à deux », alors que les refrains de la fin sont chantés ensemble.

Question : peut-on séparer les deux chanteurs dans ces passages, pour que chacun garde sa propre voix convertie ?

## État de l'art (vérifié le 06/11/2026)

Les licences ont été lues sur la source primaire (fiche Hugging Face, dépôt GitHub, Zenodo). « Non indiquée » veut
dire qu'aucune licence n'est écrite : usage privé seulement, sans redistribuer les poids.

### Essayés au banc

| Candidat | Tâche, données | Fréq. | Licence code / poids | Source |
|---|---|---|---|---|
| BS-Roformer « Chorus Male-Female » (Sucial), `model_chorus_bs_roformer_ep_267_sdr_24.1275.ckpt` | voix d'homme et de femme chantées ensemble ; 750 chansons d'Opencpop et M4Singer (chant chinois) ; l'auteur prévient qu'il ne sépare pas deux voix qui alternent | 44,1 kHz | MIT (audio-separator) / **CC BY-NC-SA 4.0** | [HF Sucial/Chorus_Male_Female_BS_Roformer](https://huggingface.co/Sucial/Chorus_Male_Female_BS_Roformer), 527 Mo |
| BS-Roformer « Male-Female » (aufr33), `bs_roformer_male_female_by_aufr33_sdr_7.2889.ckpt` | voix d'homme / voix de femme, entrée « voix seule » ; données non publiées ; MVSep le mesure à 8,2 dB sur son jeu | 44,1 kHz | MIT / **non indiquée** (dépôt d'origine introuvable) | miroirs : release « model-configs » d'audio-separator, [HF Politrees/UVR_resources](https://huggingface.co/Politrees/UVR_resources) |
| Mel-Roformer karaoké (becruily), celui de `doubler` | voix principale / reste | 44,1 kHz | non indiquée ; l'auteur : « use the model freely as long as it's not commercial use » | [HF becruily/mel-band-roformer-karaoke](https://huggingface.co/becruily/mel-band-roformer-karaoke) |
| BS-Roformer karaoké (frazer et becruily), `bs_roformer_karaoke_frazer_becruily.ckpt` | voix principale / reste ; données non publiées | 44,1 kHz | MIT / **non indiquée** (dépôt sans fiche ni licence ; une demande de licence commerciale y est restée sans réponse) | [HF becruily/bs-roformer-karaoke](https://huggingface.co/becruily/bs-roformer-karaoke), 204 Mo ; dans la liste d'audio-separator 0.48.0 |
| MedleyVox (Jeon et al., ICASSP 2023), réentraîné par Cyru5, dossier `vocals 238` | plusieurs voix chantées : Conv-TasNet sur STFT, deux sorties, entraîné avec 30 % d'unissons (chant + LibriSpeech) | 24 kHz | dépôt officiel sans licence ni poids ; poids Cyru5 **CC BY 4.0** ; modèle rebâti ici avec asteroid (MIT), comme la [WebUI](https://github.com/SUC-DriverOld/MedleyVox-Inference-WebUI) (MIT) | [HF Cyru5/MedleyVox](https://huggingface.co/Cyru5/MedleyVox), 233 Mo par modèle |
| SepFormer WSJ0-2mix et WHAMR! 16 kHz (SpeechBrain) | deux locuteurs, parole anglaise | 8 / 16 kHz | Apache-2.0 / Apache-2.0 ; **données WSJ0 et WHAMR! sous licence LDC** | [HF speechbrain/sepformer-wsj02mix](https://huggingface.co/speechbrain/sepformer-wsj02mix), [sepformer-whamr16k](https://huggingface.co/speechbrain/sepformer-whamr16k) |
| MossFormer2 SS 16K (ClearerVoice-Studio) | deux locuteurs, parole | 16 kHz | Apache-2.0 / Apache-2.0 | [HF alibabasglab/MossFormer2_SS_16K](https://huggingface.co/alibabasglab/MossFormer2_SS_16K), [ClearerVoice-Studio](https://github.com/modelscope/ClearerVoice-Studio) |
| Masque harmonique guidé par deux hauteurs (code d'ici) | chaque case temps-fréquence partagée entre les deux voix selon la proximité de leurs harmoniques ; hauteurs « oracle » (des sources) ou tirées des sorties d'un Roformer homme/femme (RMVPE) | 44,1 kHz | MIT (Voix-là) | `noyau_unisson.py` |

### Écartés, avec la raison

| Candidat | Raison | Source |
|---|---|---|
| Conv-TasNet Libri2Mix 16 kHz (Asteroid) | parole seule, comme SepFormer ; poids CC BY-SA (la fiche hésite entre 3.0 et 4.0) | [HF JorisCos/ConvTasNet_Libri2Mix_sepclean_16k](https://huggingface.co/JorisCos/ConvTasNet_Libri2Mix_sepclean_16k) |
| Extraction de locuteur cible : WeSep | poids pré-entraînés officiels « coming soon » | [wenet-e2e/wesep](https://github.com/wenet-e2e/wesep) |
| USEF-TSE | code CC BY-NC 4.0, poids sans licence, parole seule | [ZBang/USEF-TSE](https://github.com/ZBang/USEF-TSE) |
| SpEx+ | parole seule ; contenu du dossier de poids non vérifié | [gemengtju/SpEx_Plus](https://github.com/gemengtju/SpEx_Plus) |
| AV-MossFormer2 TSE | audio-visuel : il faut voir les lèvres | ClearerVoice-Studio |
| Séparation guidée par une référence du chanteur (IWAENC 2026) | licence non indiquée, poids sur Google Drive | [jocelynxu01/singer-separation-paper](https://github.com/jocelynxu01/singer-separation-paper) |
| Duos par diffusion | MIT, 24 kHz, poids sur Google Drive, lent | [iamycy/duet-svs-diffusion](https://github.com/iamycy/duet-svs-diffusion) |
| Chœur SATB (umss) | demande les hauteurs en entrée ; « All rights reserved » | [schufo/umss](https://github.com/schufo/umss) |
| Plusieurs hauteurs : Basic Pitch | Apache-2.0, mais Python 3.12 non pris en charge et « works best on one instrument at a time » ; le masque oracle borne déjà la piste | [spotify/basic-pitch](https://github.com/spotify/basic-pitch) |
| Plusieurs hauteurs de voix (Cuesta) | MIT, mais Keras / TensorFlow 1.15 | [helenacuesta/multif0-estimation-polyvocals](https://github.com/helenacuesta/multif0-estimation-polyvocals) |
| MVSep (homme/femme, SATB, MedleyVox) | service en ligne, aucun poids public | [mvsep.com](https://mvsep.com/en/algorithms) |

## Le banc

`recherche/unisson/unisson.py` (PC) et `recherche/unisson/noyau_unisson.py` (Kaggle, ajouté aux fonctions de
`kaggle/doubler_kaggle.py` comme le banc d'essai). Sessions GPU sous le verrou Kaggle de `doubler`, quota vérifié
avant chaque session (refus s'il resterait moins de 4 h).

```sh
export UNISSON_DONNEES=~/travail/unisson          # dossier de travail (défaut : banc/travail/unisson, ignoré par git)
python3 recherche/unisson/unisson.py preparer --video "Duo.mp4" --duree 110   # prises libres + fin du vrai duo
python3 recherche/unisson/unisson.py envoyer      # jeu privé <compte>/voixla-recherche-unisson
python3 recherche/unisson/unisson.py lancer s1 --limite 28                    # noyau privé <compte>/voixla-unisson-gpu
python3 recherche/unisson/unisson.py preparer --reel-extrait s1               # même plan, sans le vrai duo
python3 recherche/unisson/unisson.py envoyer && python3 recherche/unisson/unisson.py lancer s2 --sans-reel --limite 18
python3 recherche/unisson/unisson.py analyser s1 s2 --sortie pc               # PC, CPU : karaoké frazer, hauteurs
python3 recherche/unisson/unisson.py tableau s2
python3 recherche/unisson/unisson.py ecoute s1 s2 pc --ecoute banc/ecoute
```

Python du PC : numpy et soundfile (l'environnement d'audio-separator convient), ffmpeg et ffprobe ; pour `analyser`,
audio-separator 0.48.0, PyTorch et librosa (CPU).

Sessions du 06/11/2026 sur deux T4 : **s1** (tout : duos synthétiques, vrai duo, extrait doublé ; 9,6 min de GPU) et
**s2** (les mêmes duos avec les deux voix converties, modèles de parole ; 11,4 min). Une session **s3** (`lancer s3
--complement` : MossFormer2 refait, karaoké frazer sur le vrai duo, conversions de plus de l'extrait) est prête dans
le code mais n'a pas été lancée : le budget de la recherche (deux sessions) était atteint. Le karaoké frazer a été
passé sur le PC à la place (`analyser`), sans les conversions.

### Duos synthétiques à vérité connue

- **Voix** : [VocalSet 1.1](https://zenodo.org/records/1442513) (Wilkins, Seetharaman, Wahl, Pardo, ISMIR 2018),
  **CC BY 4.0**. Tous les chanteurs y chantent les mêmes airs (« Caro mio ben », « Dona nobis pacem », « Row, row,
  row your boat »). `preparer` ne télécharge que les 36 prises utiles (requêtes HTTP Range dans l'archive de 2 Go).
  Aucune voix de l'utilisateur.
- **Paires** : trois duos homme + femme (m1+f1, m3+f3, m6+f5), deux de même genre (f2+f8, m2+m9). Fenêtres de 10 s,
  les plus chantées de chaque prise.
- **Conditions**, les deux voix au même niveau :

  | Condition | Voix 1 | Voix 2 |
  |---|---|---|
  | alternance | A chante 5 s | puis B 5 s |
  | superposition | A, « Caro » | B, un autre air (« Dona ») |
  | même air | A, « Caro » | B, « Caro », non calé (tempo et tonalité de B) |
  | unisson | A, « Caro » | A convertie par YingMusic-SVC vers le timbre de B, même hauteur |
  | octave (duos mixtes) | B (femme), « Caro » | B convertie vers le timbre de A (homme), −12 demi-tons |
  | tierce | A, « Caro » | A convertie vers le timbre de B, +4 demi-tons |
  | unisson*, octave*, tierce* (session 2) | la voix 1 passée elle aussi par YingMusic-SVC, vers son propre timbre | comme ci-dessus |

  La conversion donne deux voix parfaitement calées, comme deux chanteurs qui chantent ensemble ; la voix 2 de
  l'unisson, de l'octave et de la tierce est donc une voix du pipeline (référence : une autre prise de B, 20 pas de
  diffusion). Avec une voix 1 réelle (prise dans une salle) et une voix 2 convertie (sèche, vocodeur), un séparateur
  peut s'appuyer sur la seule acoustique : à l'unisson, le Roformer Sucial passe de +4,0 dB (voix 1 réelle) à −1,5 dB
  (deux voix converties). Les conditions marquées * sont donc celles qui comptent.
- **Mesures** : SI-SDR de chaque voix à la sortie que lui donne la meilleure permutation (44,1 kHz, et 16 kHz pour
  les modèles de parole), amélioration sur le mélange (SI-SDRi) ; **identification** : chaque sortie est comparée par
  l'empreinte CAMPPlus (celle de `doubler`) aux deux chanteurs, sur une prise à part (« Row ») ; elle est juste si
  l'empreinte donne la même attribution que la meilleure permutation. Pour les Roformer homme/femme, l'**étiquette**
  est juste si la piste « male » est celle du chanteur.

### Vrai duo, pour l'écoute

Les 110 dernières secondes d'un duo homme-femme du commerce (refrains chantés ensemble), lues par ffmpeg seulement,
envoyées dans le jeu privé. Sur Kaggle : voix isolée (Roformer de `doubler`), karaoké, dereverb, puis chaque
Roformer homme/femme sur la voix isolée et sur la voix principale sèche ; temps de chaque passe ; secondes chantées à
deux selon chaque modèle (deux pistes actives, à moins de 15 dB l'une de l'autre) ; extrait d'écoute de 45 s là où
les deux chantent le plus ensemble. Puis cet extrait doublé, voix cibles libres (deux autres chanteurs de VocalSet) :
la voix principale d'un bloc (chaîne actuelle), ou chaque chanteur séparé dans sa voix.

## Résultats

### Duos synthétiques (session 2)

SI-SDR moyen des deux voix (dB, 44,1 kHz ; à 16 kHz, ce que lit la conversion, les écarts sont de 0,1 à 0,3 dB, sauf
mention). Entre parenthèses : part des mélanges où l'empreinte CAMPPlus attribue chaque sortie au bon chanteur.
* : les deux voix converties (voir plus haut).

**Duos mixtes** (trois paires) :

| Candidat | alternance | superposition | même air | unisson* | octave* | tierce* |
|---|---|---|---|---|---|---|
| mélange, aucune séparation | 0,0 | 0,0 | 0,0 | +0,9 | +0,1 | 0,0 |
| Mel-Roformer karaoké (becruily), celui de `doubler` | −5,2 (67 %) | +1,7 | +3,4 | −3,1 (67 %) | +2,6 (67 %) | −1,4 |
| BS-Roformer karaoké (frazer-becruily) | +18,3 | +16,8 | +20,4 | −1,5 (67 %) | **+10,7** | +11,0 |
| BS-Roformer homme/femme (aufr33) | +18,4 | +25,9 | +26,5 | −2,7 (33 %) | −1,2 | −3,7 (33 %) |
| BS-Roformer chœur homme/femme (Sucial) | +21,4 | **+28,4** | **+28,4** | −1,5 (67 %) | +7,0 | +9,0 (67 %) |
| SepFormer WSJ0-2mix (8 kHz) | **+21,8** | +18,9 | +17,7 | −0,9 (33 %) | −0,1 (67 %) | +4,7 (67 %) |
| SepFormer WHAMR! (16 kHz) | −0,1 (67 %) | −0,3 (67 %) | +0,6 | −3,8 (67 %) | −4,6 (33 %) | −1,2 (67 %) |
| MedleyVox (Cyru5, 24 kHz) | +0,3 (33 %) | +19,0 | +19,1 | +0,8 (67 %) | +8,0 | **+12,3** |
| masque harmonique, hauteurs oracle | +35,7 | +10,1 | +11,2 | +1,2 | +4,0 | +12,6 |
| masque harmonique, hauteurs du Roformer Sucial | +9,9 | +10,2 | +11,2 | −0,2 (0 %) | +3,0 | +8,1 |

Identification à 100 % là où rien n'est indiqué.

**Duos de même genre** (deux paires) :

| Candidat | alternance | superposition | même air | unisson* | tierce* |
|---|---|---|---|---|---|
| mélange, aucune séparation | 0,0 | 0,0 | 0,0 | +4,0 | 0,0 |
| Mel-Roformer karaoké (becruily) | −5,1 | +11,3 | +12,0 (50 %) | −4,1 (50 %) | −2,1 (50 %) |
| BS-Roformer karaoké (frazer-becruily) | +25,5 (50 %) | +18,6 | +14,3 | −1,6 | +2,4 (+5,1 à 16 kHz) |
| BS-Roformer homme/femme (aufr33) | −5,5 (50 %) | +5,6 (50 %) | +7,9 | −3,5 | −5,1 (50 %) |
| BS-Roformer chœur homme/femme (Sucial) | +13,0 | +17,2 | +15,9 | −3,0 | +4,1 (50 %) |
| SepFormer WSJ0-2mix (8 kHz) | +11,2 | +10,7 | +7,9 | −1,8 (50 %) | +5,3 |
| SepFormer WHAMR! (16 kHz) | +0,2 (50 %) | −7,5 | −6,2 | −5,2 (50 %) | −3,0 |
| MedleyVox (Cyru5, 24 kHz) | +12,8 (50 %) | **+19,2** | **+17,8** | +3,8 (50 %) | **+10,8** |
| masque harmonique, hauteurs oracle | +39,8 | +10,5 | +8,9 | +4,2 | +12,6 |

À l'unisson de même genre, le mélange vaut déjà +4,0 dB : les deux voix, converties d'une même prise vers deux
timbres proches, se ressemblent. Aucun candidat ne fait mieux que le mélange.

**Paire par paire** (tierce*, SI-SDR des deux voix) : MedleyVox reste entre +9,6 et +15,6 dB sur les cinq paires,
et entre +7,1 et +9,0 dB en octave* sur les trois paires mixtes. Les Roformer font plus haut sur certaines paires,
mais tombent à zéro sur d'autres : en tierce*, frazer donne −0,2 / +2,5 dB sur m3+f3 et laisse une sortie vide sur
f2+f8 (−33,5 dB) ; Sucial donne +0,3 / −2,3 dB sur m3+f3. Ils rangent alors les deux voix dans une même sortie.

**D'une génération à l'autre** : les duos de la session 1 sont les mêmes, mais YingMusic-SVC en fp16 ne rend pas
exactement les mêmes voix. Sur les conditions communes, les écarts entre sessions vont jusqu'à 5 dB (frazer en
tierce, voix 1 réelle : +9,6 en session 1, +15,0 en session 2) ; la plupart restent sous 1 dB. Sur trois paires,
un écart de moins de 3 dB entre deux candidats ne départage rien.

**Ce qu'on en tire** :

- **Unisson** : rien ne sépare deux voix qui chantent la même note au même moment. Même le masque guidé par les
  hauteurs exactes des sources ne gagne que +0,3 dB sur le mélange : les harmoniques des deux voix tombent dans les
  mêmes cases, et seul le timbre les distingue. L'empreinte n'y retrouve pas mieux les chanteurs qu'au hasard.
- **Octave et tierce** : séparables. Duos mixtes : frazer, MedleyVox et Sucial à +7 à +12 dB. Même genre : seul
  MedleyVox tient (+10,8 dB en tierce*). L'empreinte attribue alors chaque sortie au bon chanteur (100 % pour
  MedleyVox et frazer).
- **Le karaoké de `doubler`** (Mel-Roformer becruily) ne sépare pas deux voix qui chantent ensemble (−1,4 dB en
  tierce*, −5,2 dB en alternance) : c'est bien lui qui les laisse ensemble dans la voix principale.
- **Les modèles de parole** ne conviennent pas. SepFormer WSJ0-2mix sépare bien deux voix qui chantent des airs
  différents, mais à 8 kHz seulement ; il reste faible en tierce et nul en octave. SepFormer WHAMR! ne sépare rien.
  MossFormer2 n'a pas été mesuré : ClearVoice choisissait une carte invisible (« invalid device ordinal »). Le
  correctif est dans le code, mais il n'y a pas eu de session de plus.
- **MedleyVox ne laisse pas de sortie vide** quand une seule voix chante (alternance : +0,3 dB). Il ne doit servir
  que là où les deux chantent, et ne peut pas les détecter lui-même.
- **Les Roformer homme/femme** (Sucial, et aufr33 en retrait) séparent bien deux voix d'homme et de femme qui
  chantent des airs différents, ou qui alternent (+21 dB). L'étiquette « male » va au chanteur dans 85 % (Sucial) et
  78 % (aufr33) des mélanges mixtes.

### Vrai duo (session 1, et PC)

Les 110 dernières secondes d'un duo homme-femme du commerce, puis un extrait de 45 s, là où les deux chantent le plus
ensemble.

Temps sur une T4 (rtf = temps / durée du son) :

| Passe | rtf | Chargement |
|---|---|---|
| voix isolée (Mel-Roformer de `doubler`) | 0,21 | 10 s |
| karaoké (becruily) | 0,13 | 15 s |
| dereverb | 0,13 | 8 s |
| BS-Roformer Sucial (voix principale sèche) | 0,17 | 3 à 25 s |
| BS-Roformer aufr33 (voix principale sèche) | 0,18 | 3 à 39 s |
| BS-Roformer karaoké frazer (duos synthétiques) | 0,20 | 7 s |
| MedleyVox (duos synthétiques) | 0,013 | 12 s |

Secondes chantées à deux sur les 110 s (deux pistes actives, à moins de 15 dB l'une de l'autre) :

| Séparation | sur la voix principale sèche | sur la voix isolée |
|---|---|---|
| Sucial | 23,8 s | 50,3 s |
| aufr33 | 15,6 s | 27,1 s |

Sur la chanson entière, le dernier doublage de ce duo n'avait trouvé que 5 s chantées à deux. Sur la voix isolée,
chœurs compris, le chiffre compte aussi les chœurs : c'est la voix principale qui compte ici.

**Nature des passages à deux** sur l'extrait de 45 s (analyse sur le PC, pYIN ; unisson : écart de hauteur de moins
d'un demi-ton entre les deux pistes). Les chiffres sont de petits nombres de secondes, sur un seul extrait :

| Séparation de la voix principale sèche | À deux (s) | dont voisé | unisson | octave | autre intervalle | hauteurs médianes (Hz) |
|---|---|---|---|---|---|---|
| Sucial | 17,6 | 14,9 | 3,3 | 0,0 | 11,6 | 313 / 410 |
| aufr33 | 12,0 | 11,0 | 4,5 | 0,0 | 6,5 | 313 / 410 |
| BS-Roformer karaoké frazer | 14,7 | 12,8 | 0,6 | 0,0 | 12,2 | 315 / 413 |
| MedleyVox | 17,3 | 15,3 | 3,4 | 0,4 | 11,4 | 339 / 353 |
| SepFormer WSJ0-2mix | 25,9 | 19,4 | 10,6 | 0,5 | 8,3 | 357 / 311 |

- Sur ce duo, les passages à deux sont surtout des **harmonies à un autre intervalle** (environ 11 s sur 15 voisées),
  pas des unissons exacts (moins de 4 s pour les trois modèles les plus fiables). L'unisson exact est donc le cas
  minoritaire, et le moins séparable : la double conversion le couvre.
- Les trois Roformer s'accordent sur des hauteurs médianes de 313 et 410 Hz (homme, femme) ; MedleyVox rend deux
  pistes aux hauteurs voisines (339 / 353 Hz) : il mélange encore les deux voix sur l'extrait, ce que les mesures
  synthétiques ne montrent pas. SepFormer rend 10,6 s d'« unisson » : il copie la même voix sur les deux sorties.
- Temps sur CPU (PC, 4 fils) du karaoké frazer : 427 à 510 s pour 45 s de son (rtf 9 à 11), donc à garder sur GPU.

**Écoute** : `banc/ecoute/unisson/` (voix principale non séparée, pistes Sucial homme/femme, MedleyVox, karaoké
frazer, chaque piste seule sur la musique) et `banc/ecoute/unisson-double/` (voix principale d'un bloc, ou chaque
chanteur séparé par Sucial dans sa voix). La double conversion des passages à deux n'a pas pu être rendue (session
GPU non disponible dans le budget).

**Réserve sur la recommandation** : sur le vrai duo, MedleyVox ne se montre pas aussi net que sur les duos
synthétiques ; l'écoute tranche entre lui et Sucial/frazer pour les harmonies.

## Recommandation

**C'est faisable en partie : oui pour l'harmonie et l'octave, non pour l'unisson exact.**

- **Unisson** : ne pas chercher à séparer, rien n'y parvient. Mais à l'unisson, chanter « ensemble » ne demande pas
  de séparer : il suffit de **convertir la voix principale du passage dans les deux voix** et de les additionner,
  chacune à −3 dB. Paroles, hauteur et rythme sont les mêmes ; seuls les timbres changent, comme dans l'original.
- **Harmonie et octave** : séparer la voix principale sèche du passage avec **MedleyVox** (poids Cyru5
  « vocals 238 », CC BY 4.0), puis convertir chaque piste dans la voix de son chanteur. Des trois bons candidats,
  c'est le plus régulier (au moins +7 dB sur toutes les paires en octave* et en tierce*), le seul qui tienne en duo
  de même genre (+10,8 dB en tierce*), le moins cher (rtf 0,013 sur T4) et celui dont la licence est la plus
  ouverte. Ses 24 kHz ne gênent pas : la conversion ne lit sa source qu'à 16 kHz (YingMusic-SVC : contenu et hauteur
  à 16 kHz ; la mel à 44,1 kHz ne sert qu'à la longueur). frazer et Sucial font parfois mieux, mais tombent à zéro sur
  certaines paires et n'ont pas de licence ouverte.

### Intégration au noyau (proposition, rien n'est intégré)

Seulement avec `--voix`, quand deux chanteurs sont trouvés :

1. **Où** : après le nettoyage (`etape_nettoyer`, voix principale sèche prête), avant la conversion. Les nouvelles
   pistes passent par `affecter_pistes` comme la voix principale, les chœurs et la piste de secours.
2. **Passages à deux** : un BS-Roformer homme/femme (Sucial) sur la voix principale sèche. Un passage est « à deux »
   si les deux pistes sont actives à moins de 15 dB l'une de l'autre pendant au moins 0,4 s, et si l'empreinte et la
   hauteur (`reconnaitre`) donnent un chanteur différent à chacune. Ce Roformer laisse une piste vide quand une seule
   voix chante (+21 dB en alternance), ce que MedleyVox ne fait pas. En duo de même genre (`chanteur1:`/`chanteur2:`),
   aucun détecteur sûr n'a été mesuré : garder la chaîne actuelle tant qu'il n'est pas validé.
3. **Nature du passage** : les hauteurs RMVPE (déjà chargé dans le noyau) des deux pistes de détection. Écart de
   moins d'un demi-ton sur la plupart des trames voisées : unisson, donc double conversion. Sinon : MedleyVox sur le
   passage (voix principale sèche ramenée à 24 kHz, sorties remises à 44,1 kHz), chaque sortie reconnue puis
   convertie dans sa voix, au niveau de sa source.
4. **Replis**, vers la chaîne actuelle (la voix principale d'un bloc dans la voix du chanteur du passage) : piste
   séparée à plus de 20 dB sous la voix principale, deux sorties reconnues comme le même chanteur, reconnaissance
   incertaine. Une option pour couper le tout.

**Coût GPU par chanson** (3 min 48, T4) :

- détection : BS-Roformer Sucial sur la voix principale sèche, environ 40 s, plus 6 à 25 s de chargement ;
- MedleyVox sur les passages à deux : moins de 2 s, plus 12 s de chargement ; les hauteurs : quelques secondes ;
- conversion : deux pistes au lieu d'une sur les passages à deux. Sur ce duo, environ 22 % du temps est chanté à
  deux (24 s sur 110 s), soit environ 50 s par chanson : environ 40 s de conversion de plus à 30 pas sur une carte,
  la moitié à deux cartes.

Soit **environ 1 à 1,5 min de GPU de plus par chanson**, 5 à 8 % d'une session de 19 à 27 min.

### Risques

- **Banc réduit** : cinq paires de 10 s, du chant lyrique (VocalSet), une seconde voix de synthèse ; les écarts entre
  deux générations des mêmes duos vont jusqu'à 5 dB.
- **Écoute à faire** : la recommandation repose sur les mesures synthétiques. L'écoute du vrai duo (ci-dessous) dira
  si les fuites sont acceptables. La double conversion de l'unisson n'a pas pu être rendue : elle demandait une
  session GPU de plus.
- **Fuites** : chaque piste séparée garde un peu de l'autre voix, qui sera convertie dans la mauvaise voix.
- **Détection** : seuils non réglés, à valider sur plusieurs vrais duos. Un passage chanté seul, pris pour un
  unisson, serait converti dans les deux voix.
- **Licences** : Sucial est en CC BY-NC-SA 4.0 (non commercial, partage à l'identique), comme le karaoké de `doubler`
  est déjà réservé à un usage non commercial ; frazer-becruily n'a aucune licence (usage privé seulement) ; MedleyVox
  (poids Cyru5) demande de citer ses auteurs. À ajouter aux crédits du README.
- **Mémoire GPU** : un Roformer de plus pendant le nettoyage (poids de 527 Mo), MedleyVox est petit (233 Mo).

<!-- ECOUTE -->
