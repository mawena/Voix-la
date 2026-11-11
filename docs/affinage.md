# Affiner sa voix sur son chant

Voix-là double une chanson avec une voix clonée. Aujourd'hui la voix vient de 20 s de **parole** : le modèle y devine
le timbre chanté (notes hautes, voyelles tenues, voix de tête) qu'il n'a jamais entendu. L'**affinage** apprend ce
timbre sur 5 à 10 minutes de **chant** de la personne, par un court entraînement de Seed-VC 44,1 kHz sur le GPU gratuit
de Kaggle, pour qu'on la reconnaisse dans ses doublages.

État : la commande `affiner`, le noyau Kaggle et la page Voix sont écrits et testés **en simulation** (rien n'a encore
tourné sur un vrai GPU). Le bouton de l'interface reste désactivé, avec la mention « en préparation : l'essai réel
n'est pas encore fait », tant que la clé `affinage_pret` de `config.json` n'est pas vraie.

Sommaire : [La recherche](#la-recherche) · [Guide d'enregistrement](#guide-denregistrement) ·
[Fonctionnement](#fonctionnement) · [Utilisation](#utilisation) · [Premier essai réel](#premier-essai-réel) ·
[Intégration au doublage](#intégration-au-doublage-à-faire)

## La recherche

Résumé de la recherche du 9 novembre 2026 (`banc/resultats/qualite-recherche/05-affinage-agent.md`, et la synthèse
`SYNTHESE.md` du même dossier, qui classe les causes d'un doublage où l'on ne reconnaît pas la voix : la référence est
de la parole de téléphone, le registre, le style du chanteur d'origine, le fantôme de la voix d'origine, le mixage).

- **YingMusic-SVC** ([GiantAILab/YingMusic-SVC](https://github.com/GiantAILab/YingMusic-SVC)) : code MIT ; la licence des
  poids n'est pas indiquée dans le README (le noyau suppose CC BY-NC 4.0, à vérifier sur la page Hugging Face). Il n'y a
  **aucun script d'entraînement ni d'affinage** : le README décrit un entraînement en trois étapes (pré-entraînement
  continu, SFT, RL Flow-GRPO) sans commande. Il est construit sur Seed-VC, avec un résidu de style. Impossible de
  l'affiner avec ce qui est publié.
- **Seed-VC** ([Plachtaa/seed-vc](https://github.com/Plachtaa/seed-vc)), GPL-3.0 : `train.py` officiel et préréglage de
  chant `configs/presets/config_dit_mel_seed_uvit_whisper_base_f0_44k.yml`.
  - Seuls `cfm` et `length_regulator` sont entraînés, clip de gradient à 10, sans précision mixte.
  - Taux d'apprentissage : 1e-4 dans le préréglage, 1e-5 dans le script : à trancher à l'essai.
  - Données : fichiers de 1 à 30 s, propres. Reprise par `run-name` ; `ft_model.pth` à la fin.
  - Le README annonce « 100 pas en 2 min sur T4 » (pour la parole). Le banc du 4 novembre mesurait 1,56 s par pas sur T4
    en batch 2, et un gain faible avec 2,8 min de **parole**.
- **Estimation** : 1500 à 2000 pas, soit 35 à 70 min de session T4.
- **Compatibilité** : le point de contrôle Seed-VC affiné ne se charge **pas** dans YingMusic. Une voix affinée passe donc
  par Seed-VC 44k (voir [l'intégration](#intégration-au-doublage-à-faire)).

## Guide d'enregistrement

Il faut de **5 à 10 minutes de chant a cappella** (2 minutes exploitables au minimum), en prises de 1 à 2 minutes. Plus de
10 minutes : le gain est faible et le temps de GPU monte.

- **Au casque**, bien fermé, volume modéré : la musique ne doit pas passer dans le micro. Chanter a cappella ou sur
  l'accompagnement seul de la séance.
- **Sans saturer** : crête sous −3 dBFS. Baisser le niveau d'entrée plutôt que de reculer au dernier moment.
- **Sans effet** : ni réverbération, ni auto-tune, ni égaliseur, ni réduction de bruit du téléphone.
- Téléphone à 15 ou 20 cm, **meilleure qualité d'enregistrement** (m4a ou wav ; pas de vocal de messagerie), mode avion,
  pièce calme et meublée. Ne pas envoyer les fichiers par une messagerie qui recompresse le son.
- **Couvrir toute la tessiture**, des graves aux aigus confortables, avec des nuances (doux, fort), des **voyelles tenues**
  et des **phrases chantées** (pas seulement des notes isolées).
- Une seule personne, et c'est la sienne (ou celle d'une personne d'accord).

La **séance d'enregistrement** de la page Voix guide tout cela (sirènes, gamme, notes à tenir, chansons du domaine public
avec accompagnement et paroles) et range les prises dans `voix/<nom>/chant/` ; les prises de parole vont dans
`voix/<nom>/parole/`. Les fichiers du téléphone sont acceptés tels quels : m4a, aac, mp3, wav, ogg, opus, 3gp, amr (et
flac), à n'importe quelle fréquence ; `affiner` les convertit par ffmpeg en FLAC 16 bits mono 44,1 kHz. Un son à moins de
16 kHz (amr, appel téléphonique) est accepté avec une alerte : il manquera d'aigus.

## Fonctionnement

1. **Contrôle sur le PC.** Chaque prise est mesurée : durée (au moins 5 s), crête, saturation, bruit de fond, écart
   chant / fond (au moins 15 dB), silence, niveau actif. Une prise saturée, bruitée, trop faible ou illisible n'est pas
   envoyée ; au moins 2 minutes de chant exploitable sont exigées (5 à 10 conseillées).
2. **Envoi.** Les prises retenues partent en FLAC dans un **jeu de données privé**, avec un **noyau privé**
   (`kaggle/affiner_kaggle.py`), sur le compte et la machine de `doubler`. Seul le chant part : jamais la référence ni une
   vidéo. Les fonctions d'envoi, de suivi et de rapatriement sont celles de `doubler.py` ; un seul travail Kaggle à la fois.
3. **Noyau.** Il découpe aux silences en morceaux de 3 à 12 s (Seed-VC ignore ce qui dure moins de 1 s ou plus de 30 s), nettoie, contrôle, installe Seed-VC, lance son `train.py` (préréglage de
   chant 44 kHz, `cfm` et `length_regulator` seuls, sauvegardes intermédiaires) et rend un point de contrôle compact et un
   rapport. Budget de temps : 2 h par défaut.
4. **Rangement** dans `voix/<nom>/affinage/` : `affinage.pth`, `affinage.yml`, `affinage.json` (la fiche : moteur, pas,
   durée de chant, somme SHA-256…) et `rapport.json`. C'est `affinage.json` avec son point de contrôle qui fait la voix
   « affinée » (le badge de la page Voix).
5. **Reprise.** Un affinage interrompu (budget, panne) garde son dernier point dans `affinage/reprise/` ; `affiner
   --reprendre` finit la session notée, ou la relance depuis ce point.

Tout est testé en simulation (`tests/test_affinage_noyau.py`, `tests/test_affinage_commande.py` : fausse commande `kaggle`,
sons de synthèse, dossiers temporaires, HOME déplacé). `affiner --essai` écrit le jeu de données et le noyau tels qu'ils
partiraient, avec le plan et l'estimation, sans rien envoyer.

## Utilisation

Depuis l'interface (page Voix) : le bouton **Affiner ma voix sur mon chant…** apparaît quand la voix choisie a au moins
2 minutes de chant exploitable ; il ajoute un travail « affinage » dans la file des travaux, avec suivi par étapes (contrôle,
envoi, attente d'un GPU, affinage sur le GPU, rapatriement) et annulation. **Reprendre l'affinage** finit un affinage
interrompu. Voir [l'interface](../interface/README.md#affiner-sa-voix).

En ligne de commande :

```sh
affiner voixA --essai          # tout sauf l'envoi : plan, estimation, jeu de données et noyau écrits localement
affiner voixA                  # l'affinage (1500 pas, 2 h au plus)
affiner voixA --reprendre      # finit un affinage interrompu
affiner voixA --etat           # la fiche du dernier affinage
```

`man affiner` donne toutes les options.

## Premier essai réel

À faire une fois, avec une vraie voix, pour trancher le taux d'apprentissage, mesurer le temps par pas et écouter le
résultat avant d'activer le bouton (`affinage_pret`). Commande : `affiner <nom> --pas 1500 --budget-h 2` (voir le rapport
de la branche pour la durée et le quota estimés : de l'ordre de 30 à 60 min de GPU, soit au plus 3 % des 30 h
hebdomadaires).

## Intégration au doublage (faite, à vérifier au premier vrai affinage)

Une voix affinée sert à doubler une chanson. Ce qui est fait :

1. **Choix par voix** (`doubler --affinee auto|oui|non`, clé `affinee`). `auto` (défaut) : une voix qui a un
   `affinage.json` avec son point de contrôle (somme SHA-256 conforme) prend son affinage, **seulement si `affinage_pret`
   est vrai** (à écrire par `doubler --config affinage_pret oui` quand l'affinage a été jugé bon à l'écoute) ; `oui` : toute
   voix qui en a un, prêt ou non (pour l'essayer) ; `non` : jamais. Chaque voix d'un duo choisit seule. Cette voix passe sur
   **Seed-VC 44 kHz affiné**, même en chant (YingMusic ne charge pas le point) ; dans un duo mixte, le chant de toute la
   chanson passe alors par Seed-VC 44 kHz (la voix sans affinage garde les poids de base).
2. **Envoi.** `affinage.pth` et `affinage.yml` partent dans le jeu de données privé de la session (comme la référence),
   Kaggle seulement : avec `--calcul modal` ou `runpod`, `--affinee oui` est refusé (« voix affinée : Kaggle seulement pour
   l'instant ») et `auto` garde les poids de base ; la voix n'est jamais gardée sur Modal ni sur Runpod.
3. **Noyau de doublage.** Le modèle 44 kHz de cette voix est chargé avec `Namespace(checkpoint=…, config=…)` de Seed-VC (la
   config est celle de l'affinage, sinon le préréglage 44 kHz) ; le modèle de base reste chargé pour l'analyse. La
   référence de parole reste le prompt de timbre.
4. **Repli.** Point absent, somme SHA-256 différente ou chargement refusé : poids de base, sans échec ; `rapport.json`
   (clé `affinage`, par voix : `utilise`, `raison`, `pas`) et le journal le disent.
5. **À mesurer avant de généraliser.** Comparer zéro-shot et affiné sur les mêmes chansons (comparateur de voix de
   l'interface, proches de la voix) et ne passer `affinage_pret` à vrai qu'après un gain net.
