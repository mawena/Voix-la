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
| BS-Roformer karaoké (frazer-becruily), `bs_roformer_karaoke_frazer_becruily.ckpt` | voix principale / reste | 44,1 kHz | non indiquée | liste de modèles d'audio-separator 0.48.0 |
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
python3 recherche/unisson/unisson.py tableau s1
python3 recherche/unisson/unisson.py ecoute s1 --ecoute banc/ecoute
```

Python du PC : numpy et soundfile (l'environnement d'audio-separator convient), ffmpeg et ffprobe.

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

  La conversion donne deux voix parfaitement calées, comme deux chanteurs qui chantent ensemble ; la voix 2 de
  l'unisson, de l'octave et de la tierce est donc une voix du pipeline (référence : une autre prise de B, 20 pas de
  diffusion).
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

<!-- RESULTATS -->

<!-- RECOMMANDATION -->
