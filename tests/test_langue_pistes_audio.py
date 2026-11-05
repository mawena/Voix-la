"""Étiquette de langue des pistes audio de la vidéo doublée : la langue principale des paroles reconnues (celle chantée
le plus longtemps, en additionnant la durée des lignes de chaque langue), sinon celle du son de la source, sinon
aucune ; la même pour les cinq pistes (Original et Voix d'origine seule comprises), version auto-tune comprise. Paroles
françaises sur une source étiquetée eng, chanson bilingue, sans paroles (--sans-paroles, rien de reconnu, source sans
étiquette), auto-tune, puis --remix d'un ancien doublage en ligne de commande ; vérifié par ffprobe en mp4, mkv et
webm. Vidéos, sons et paroles de synthèse."""
import argparse
import contextlib
import io
import json
import os
import subprocess
import unittest

from commun import DOUBLER, FFMPEG, SR, Dossier, charger_doubler, ecrire_wav, ffprobe, python
from test_pistes_audio import FAT1, JULES, WEBM, bruit, travail, video

CONTENEURS = ('.mp4', '.mkv') + (('.webm',) if WEBM else ())


def ligne(debut, fin, texte, langue=None):
    """Une ligne de paroles.json (langue absente : comme une ligne sans langue)."""
    mots = texte.split()
    pas = (fin - debut) / len(mots)
    s = {'debut': debut, 'fin': fin, 'texte': texte,
         'mots': [{'debut': round(debut + i * pas, 3), 'fin': round(debut + (i + 1) * pas, 3), 'mot': m}
                  for i, m in enumerate(mots)]}
    return {**s, 'langue': langue} if langue else s


def paroles(langue, lignes, traduction=None):
    """Contenu de paroles.json (format 1), temps du son doublé ; traduction : textes français, un par ligne."""
    d = {'format': 1, 'langue': langue, 'source': 'whisper-large-v3', 'decalage_s': 0.0, 'segments': lignes,
         'traductions': {}}
    if traduction:
        d['traductions']['fr'] = {'source': 'nllb-200-distilled-600M',
                                  'segments': [{'debut': l['debut'], 'fin': l['fin'], 'texte': t}
                                               for l, t in zip(lignes, traduction)]}
    return d


# paroles inventées
FRANCAIS = paroles('fr', [ligne(0.5, 2.0, 'le vent tourne sur la place', 'fr'),
                          ligne(2.5, 4.0, 'on danse encore ce soir', 'fr')])
# trois lignes courtes en anglais (1,5 s), une longue en français (2,4 s) : plus de lignes en anglais, plus de temps
# chanté en français ; la mesure est le temps chanté
BILINGUE = paroles('fr', [ligne(0.5, 1.0, 'hold the light', 'en'), ligne(1.2, 1.7, 'turn it round', 'en'),
                          ligne(1.9, 2.4, 'never mind', 'en'),
                          ligne(2.6, 5.0, 'et la nuit nous emporte au loin', 'fr')],
                   ['tiens la lumière', 'fais-la tourner', 'peu importe', 'et la nuit nous emporte au loin'])
RIEN = paroles(None, [])
# ancien paroles.json : lignes sans langue, la langue du fichier seule
ANCIEN = {'format': 1, 'langue': 'fr', 'source': 'whisper-large-v3',
          'segments': [ligne(0.5, 2.0, 'le vent tourne sur la place'), ligne(2.5, 3.5, 'on danse encore')],
          'traductions': {}}

PAROLES_FR = {'etiquette': 'fre', 'provenance': 'paroles', 'paroles': 'fr', 'nom': 'français',
              'secondes_par_langue': {'fr': 3.0}, 'source': 'eng'}


def sons(sortie):
    """Pistes audio d'une vidéo (ffprobe), dans l'ordre."""
    return [s for s in ffprobe(sortie, '-show_streams')['streams'] if s['codec_type'] == 'audio']


def langues(sortie):
    """Étiquette de langue de chaque piste audio (None : sans étiquette)."""
    return [s.get('tags', {}).get('language') for s in sons(sortie)]


class LangueChantee(unittest.TestCase):
    """langue_chantee et langue_pistes_audio, sans ffmpeg."""

    def setUp(self):
        self._d = Dossier()
        self.tmp = self._d.__enter__()
        self.addCleanup(self._d.__exit__)
        self.D = charger_doubler(self.tmp)

    def chantee(self, d):
        chemin = self.tmp / 'paroles.json'
        chemin.write_text(json.dumps(d), encoding='utf-8')
        lu, raison = self.D.lire_paroles(chemin)
        self.assertIsNone(raison)
        return self.D.langue_chantee(lu)

    def test_une_langue(self):
        self.assertEqual(self.chantee(FRANCAIS), ('fr', {'fr': 3.0}))

    def test_bilingue_au_temps_chante(self):
        self.assertEqual(self.chantee(BILINGUE), ('fr', {'en': 1.5, 'fr': 2.4}))
        # la langue du fichier ne l'emporte pas sur le temps chanté…
        self.assertEqual(self.chantee({**BILINGUE, 'langue': 'en'})[0], 'fr')
        # …sauf à égalité ; à égalité sans langue du fichier, la première chantée
        egal = [ligne(0.0, 2.0, 'one two', 'en'), ligne(2.0, 4.0, 'un deux', 'fr')]
        self.assertEqual(self.chantee(paroles('fr', egal))[0], 'fr')
        self.assertEqual(self.chantee(paroles('en', egal))[0], 'en')
        self.assertEqual(self.chantee(paroles(None, egal[::-1]))[0], 'en')   # triées par temps : l'anglais d'abord

    def test_lignes_sans_langue_et_cas_vides(self):
        self.assertEqual(self.chantee(ANCIEN), ('fr', {'fr': 2.5}))   # comptent pour la langue du fichier
        self.assertEqual(self.chantee(paroles(None, [ligne(0.0, 1.0, 'la la')])), (None, {}))
        self.assertEqual(self.chantee(RIEN), (None, {}))
        # une ligne d'une durée nulle compte pour 0,01 s, comme dans le noyau
        self.assertEqual(self.chantee(paroles(None, [ligne(1.0, 1.0, 'oh', 'es')]))[1], {'es': 0.01})

    def test_etiquette(self):
        L = self.D.langue_pistes_audio
        chantee = {'langue': 'fr', 'secondes_par_langue': {'fr': 3.0}}
        self.assertEqual(L(chantee, {'demande': True}, {'langue': 'eng'}), PAROLES_FR)
        self.assertEqual(L({'langue': 'yue', 'secondes_par_langue': {'yue': 1.0}}, {'demande': True}, {})['etiquette'],
                         'chi')
        self.assertEqual(L(None, {'demande': False}, {'langue': 'eng'}),
                         {'etiquette': 'eng', 'provenance': 'source', 'source': 'eng', 'raison': 'sans paroles'})
        self.assertEqual(L(None, {'demande': True, 'raison': 'aucune parole reconnue'}, {'langue': 'eng'})['raison'],
                         'aucune parole reconnue')
        self.assertEqual(L({'langue': 'xx', 'secondes_par_langue': {'xx': 2.0}}, {'demande': True}, {'langue': 'eng'}),
                         {'etiquette': 'eng', 'provenance': 'source', 'source': 'eng',
                          'raison': 'langue des paroles inconnue (xx)'})
        self.assertEqual(L({'langue': None, 'secondes_par_langue': {}}, {'demande': True}, {'langue': None})['raison'],
                         'langue des paroles non indiquée')
        self.assertEqual(L(None, {'demande': True, 'raison': 'pas de paroles.json'}, {}),
                         {'etiquette': None, 'provenance': None, 'source': None, 'raison': 'pas de paroles.json'})


@unittest.skipUnless(FFMPEG, 'ffmpeg absent')
class LanguePistes(unittest.TestCase):
    def setUp(self):
        self._d = Dossier()
        self.tmp = self._d.__enter__()
        self.addCleanup(self._d.__exit__)
        (self.tmp / 'home').mkdir()
        self.D = charger_doubler(self.tmp / 'home')

    def doubler(self, nom, ext, contenu=None, langue_source='eng', paroles_on=True, autotune=None, extrait=None,
                duree=6.0):
        """finir() sur un travail/ de synthèse (source étiquetée langue_source, paroles.json = contenu) ; rend (vidéo,
        rapport.json)."""
        (self.tmp / 'videos').mkdir(exist_ok=True)
        source = video(self.tmp / 'videos' / f'{nom}{ext}', duree, langue=langue_source)
        dossier = self.tmp / 'sorties' / nom
        t, tv, _ = travail(self.D, source, dossier, JULES, extrait, paroles=contenu or False)
        a = argparse.Namespace(voix_db=None, mix_v1=False, reverb_db=-13.0, paroles=paroles_on, pistes_audio='toutes',
                               autotune=autotune, sans_autotune=False, autotune_gamme=None)
        a.autotune_reglage = self.D.reglage_autotune(a, {})
        tr = {'travail': t, 'tv': tv, 'dossier': dossier, 'base': f'{nom} - voix Jules', 'nom': nom}
        with contextlib.redirect_stdout(io.StringIO()) as texte:
            sortie = self.D.finir(tr, a, JULES)
        self.texte = texte.getvalue()
        return sortie, json.loads((tv / 'rapport.json').read_text())

    def test_paroles_francaises_sur_source_anglaise(self):
        for ext in CONTENEURS:
            with self.subTest(ext=ext):
                extrait = (1.0, 4.5) if ext == '.webm' else None   # extrait : paroles recalées, même étiquette
                sortie, rapport = self.doubler(f'Place{ext[1:]}', ext, FRANCAIS, extrait=extrait)
                self.assertEqual(sortie.suffix, ext)
                self.assertEqual(langues(sortie), ['fre'] * 5)
                self.assertEqual(rapport['pistes_audio']['langue'], PAROLES_FR)
                self.assertEqual(len(rapport['pistes_audio']['video']), 5)
                self.assertNotIn('langue_chantee', rapport['paroles_sorties'])
                self.assertIn("pistes audio en français (fre), la langue des paroles (son d'origine : eng)", self.texte)
                st = [s for s in ffprobe(sortie, '-show_streams')['streams'] if s['codec_type'] == 'subtitle']
                self.assertEqual([s['tags']['language'] for s in st], ['fre'])   # « Paroles (français) »

    def test_chanson_bilingue(self):
        for ext in CONTENEURS:
            with self.subTest(ext=ext):
                sortie, rapport = self.doubler(f'Deux{ext[1:]}', ext, BILINGUE, langue_source='spa')
                self.assertEqual(langues(sortie), ['fre'] * 5)
                self.assertEqual(rapport['pistes_audio']['langue'],
                                 {'etiquette': 'fre', 'provenance': 'paroles', 'paroles': 'fr', 'nom': 'français',
                                  'secondes_par_langue': {'en': 1.5, 'fr': 2.4}, 'source': 'spa'})
        # l'anglais chanté plus longtemps : eng, même si le fichier dit fr
        plus_anglais = paroles('fr', [ligne(0.5, 3.0, 'hold the light and turn it round', 'en'),
                                      ligne(3.2, 4.0, 'au loin', 'fr')])
        sortie, rapport = self.doubler('Anglais', '.mkv', plus_anglais, langue_source='spa')
        self.assertEqual(langues(sortie), ['eng'] * 5)
        self.assertEqual(rapport['pistes_audio']['langue']['secondes_par_langue'], {'en': 2.5, 'fr': 0.8})

    def test_sans_paroles(self):
        """--sans-paroles (paroles.json présent mais pas lu) ou rien de reconnu : l'étiquette de la source, comme
        avant ; source sans étiquette : aucune (en mp4, ffmpeg écrit und, comme avant)."""
        for ext in CONTENEURS:
            with self.subTest(ext=ext, cas='--sans-paroles'):
                sortie, rapport = self.doubler(f'Muet{ext[1:]}', ext, FRANCAIS, paroles_on=False, duree=4.0)
                self.assertEqual(langues(sortie), ['eng'] * 5)
                self.assertEqual(rapport['pistes_audio']['langue'], {'etiquette': 'eng', 'provenance': 'source',
                                                                     'source': 'eng', 'raison': 'sans paroles'})
        sortie, rapport = self.doubler('Rien', '.mkv', RIEN, duree=4.0)
        self.assertEqual(langues(sortie), ['eng'] * 5)
        self.assertEqual(rapport['pistes_audio']['langue'], {'etiquette': 'eng', 'provenance': 'source',
                                                             'source': 'eng', 'raison': 'aucune parole reconnue'})
        for ext in ('.mp4', '.mkv'):   # webm : comme mkv
            with self.subTest(ext=ext, cas='source sans étiquette'):
                sortie, rapport = self.doubler(f'Nu{ext[1:]}', ext, langue_source=None, duree=4.0)
                attendu = 'und' if ext == '.mp4' else None   # la source mp4 elle-même est étiquetée und
                self.assertEqual(langues(sortie), [attendu] * 5)
                self.assertEqual(rapport['pistes_audio']['langue'],
                                 {'etiquette': attendu, 'provenance': 'source' if attendu else None, 'source': attendu,
                                  'raison': 'pas de paroles.json'})
        # paroles dans une langue hors de la table : celle de la source
        inconnue = paroles('xx', [ligne(0.5, 2.0, 'ka lo mi', 'xx')])
        sortie, rapport = self.doubler('Inconnue', '.mkv', inconnue, duree=4.0)
        self.assertEqual(langues(sortie), ['eng'] * 5)
        self.assertEqual(rapport['pistes_audio']['langue']['raison'], 'langue des paroles inconnue (xx)')

    @unittest.skipUnless(FAT1, 'x42-autotune (fat1) absent')
    def test_auto_tune(self):
        sortie, rapport = self.doubler('Juste', '.mp4', FRANCAIS, autotune=60)
        self.assertTrue(rapport['autotune']['applique'], self.texte)
        at = sortie.with_name(f"{self.D.base_autotune('Juste - voix Jules')}.mp4")
        for v in (sortie, at):
            self.assertEqual(langues(v), ['fre'] * 5, v.name)
        self.assertEqual(len(rapport['pistes_audio']['video_autotune']), 5)
        self.assertEqual(rapport['pistes_audio']['langue'], PAROLES_FR)


@unittest.skipUnless(FFMPEG, 'ffmpeg absent')
class Remix(unittest.TestCase):
    def setUp(self):
        self._d = Dossier()
        self.tmp = self._d.__enter__()
        self.addCleanup(self._d.__exit__)
        self.home = self.tmp / 'home'
        self.home.mkdir()
        self.D = charger_doubler(self.home)

    def test_remix_d_un_ancien_doublage(self):
        """doubler --remix (sans Kaggle) sur un ancien doublage d'une chanson française étiquetée eng, avec son
        paroles.json (ancien, sans langue par ligne) : les cinq pistes passent de eng à fre ; --sans-paroles garde
        eng, comme avant."""
        env = {**os.environ, 'HOME': str(self.home)}
        for v in ('VOIXLA_KAGGLE', 'VOIXLA_DONNEES', 'DOUBLER_VOIX'):
            env.pop(v, None)
        donnees = self.tmp / 'donnees'
        (donnees / 'voix' / 'jules').mkdir(parents=True)
        ecrire_wav(donnees / 'voix' / 'jules' / 'reference.wav', bruit(SR, 5, -20))
        (donnees / 'voix' / 'jules' / 'voix.json').write_text(json.dumps(
            {'nom': 'Jules', 'reference': 'reference.wav', 'hauteur_parole_mediane_hz': 120.0}))
        (self.tmp / 'videos').mkdir()
        for ext in ('.mp4', '.mkv'):
            with self.subTest(ext=ext):
                source = video(self.tmp / 'videos' / f'Ancien{ext[1:]}{ext}', 4.0, langue='eng')
                dossier = donnees / 'sorties' / source.stem
                _, tv, _ = travail(self.D, source, dossier, JULES, paroles=ANCIEN)
                sortie = dossier / f'{source.stem} - voix Jules{ext}'

                def doubler(*options):
                    r = subprocess.run([python(), str(DOUBLER), '--remix', '--voix', 'jules', '--donnees', str(donnees),
                                        *options, str(source)], capture_output=True, text=True, env=env, timeout=300)
                    self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
                    rapport = json.loads((tv / 'rapport.json').read_text())
                    return langues(sortie), rapport['pistes_audio']['langue'], r.stdout

                avant, langue, _ = doubler('--sans-paroles')   # comme l'ancien doublage : l'étiquette de la source
                self.assertEqual(avant, ['eng'] * 5)
                self.assertEqual(langue['provenance'], 'source')
                apres, langue, texte = doubler()
                self.assertEqual(apres, ['fre'] * 5)
                self.assertEqual(langue, {**PAROLES_FR, 'secondes_par_langue': {'fr': 2.5}})
                self.assertIn('pistes audio en français (fre)', texte)
                self.assertEqual([s['tags'].get('handler_name' if ext == '.mp4' else 'title') for s in sons(sortie)],
                                 ['Doublage (voix Jules)', 'Original', 'Beat seul', 'Voix convertie seule (Jules)',
                                  "Voix d'origine seule"])


if __name__ == '__main__':
    unittest.main()
