"""Transposition de la chanson sur le PC : option --transposer et clé « transposer », zone de la voix cible envoyée au noyau
(tessiture.json, sinon 2 x la parole ±4 demi-tons), Rubber Band R3 (durée exacte), musique transposée gardée en cache dans
travail/, mixage et cinq pistes de la vidéo (doublage et beat seul sur la musique transposée, original et voix d'origine
jamais transposés, durées identiques), --remix qui suit la décision du rapport (et le dit si --transposer non la
contredit), et repli sans la bibliothèque. Sons de synthèse, quelques secondes ; rien d'envoyé à Kaggle."""
import argparse
import contextlib
import io
import json
import os
import subprocess
import unittest
from unittest import mock

import numpy as np

from commun import DOUBLER, FFMPEG, SR, Dossier, charger_doubler, ecrire_wav, ffprobe, python
from test_pistes_audio import JULES, bruit, decoder, position, travail, video

CENTRE_PAROLE = 100.0   # parole médiane de la voix d'essai : centre de chant 200 Hz


def pic_hz(x, bas=200.0, haut=900.0):
    """Fréquence (Hz) de la raie la plus forte de x entre bas et haut."""
    s = np.abs(np.fft.rfft(np.asarray(x, np.float64) * np.hanning(len(x))))
    f = np.fft.rfftfreq(len(x), 1 / SR)
    m = (f >= bas) & (f <= haut)
    return float(f[m][np.argmax(s[m])])


def musique_tonale(n):
    t = np.arange(n) / SR
    return 0.2 * np.sin(2 * np.pi * 440 * t) + 0.06 * np.sin(2 * np.pi * 660 * t)


class Fichiers(unittest.TestCase):
    def setUp(self):
        self._d = Dossier()
        self.tmp = self._d.__enter__()
        self.addCleanup(self._d.__exit__)
        (self.tmp / 'home').mkdir()
        self.D = charger_doubler(self.tmp / 'home')


class ZoneEtOptions(Fichiers):
    def voix(self, nom='jules', tessiture=None, parole=CENTRE_PAROLE):
        d = self.tmp / 'donnees' / 'voix' / nom
        d.mkdir(parents=True, exist_ok=True)
        (d / 'reference.wav').write_bytes(b'RIFF')
        (d / 'voix.json').write_text(json.dumps({'nom': nom.capitalize(), 'reference': 'reference.wav',
                                                 'hauteur_parole_mediane_hz': parole}))
        if tessiture is not None:
            (d / 'tessiture.json').write_text(tessiture if isinstance(tessiture, str) else json.dumps(tessiture))
        self.D.VOIX = self.tmp / 'donnees' / 'voix'
        return self.D.charger_voix(nom)

    def test_zone_sans_tessiture_deux_fois_la_parole_plus_ou_moins_quatre_demi_tons(self):
        z = self.voix()['zone']
        self.assertEqual(z['origine'], 'parole')
        self.assertEqual(z['centre_hz'], 200.0)
        self.assertAlmostEqual(z['confort'][0], 200 * 2 ** (-4 / 12), delta=0.1)
        self.assertAlmostEqual(z['confort'][1], 200 * 2 ** (4 / 12), delta=0.1)

    def test_zone_de_tessiture_json(self):
        t = {'p05': 130.0, 'p10': 140.0, 'p50': 180.0, 'p90': 240.0, 'p95': 260.0, 'confort': [150.0, 230.0]}
        z = self.voix(tessiture=t)['zone']
        self.assertEqual(z['origine'], 'tessiture')
        self.assertEqual(z['confort'], [150.0, 230.0])
        self.assertEqual(z['p50'], 180.0)
        self.assertAlmostEqual(z['centre_hz'], (150 * 230) ** 0.5, delta=0.1)

    def test_tessiture_illisible_ou_incoherente_retombe_sur_la_parole(self):
        for t in ('pas du json', {'confort': [300, 100, 5]}, {'confort': [100, 100]}, {'p50': 200}, {'confort': ['a', 'b']},
                  {'confort': [20.0, 30.0]}):
            self.assertEqual(self.voix(tessiture=t)['zone']['origine'], 'parole', t)

    def test_lire_transposer(self):
        D = self.D
        self.assertEqual([D.lire_transposer(x) for x in (None, 'auto', 'Non', 'off', '-3', '+2', '0', 4, '−2')],
                         ['auto', 'auto', 'non', 'non', -3, 2, 0, 4, -2])
        for mauvais in ('beaucoup', '13', '-13', '1.5', True):
            with self.assertRaises(ValueError, msg=mauvais):
                D.lire_transposer(mauvais)

    def test_cle_de_configuration(self):
        D = self.D
        self.assertEqual(D.transposer_config({}), 'auto')
        with contextlib.redirect_stdout(io.StringIO()):
            D.ecrire_config('transposer', 'non')
            self.assertEqual(D.transposer_config(D.lire_config()), 'non')
            D.ecrire_config('transposer', '-2')
            self.assertEqual(D.transposer_config(D.lire_config()), -2)
            with self.assertRaises(SystemExit):
                D.ecrire_config('transposer', 'trop')
            self.assertEqual(D.transposer_config({'transposer': 'bof'}), 'auto')   # illisible : défaut, avec un avis

    def reglages(self, voix, transposer='auto', octave='auto'):
        a = argparse.Namespace(pas=30, paroles=False, moteur='yingmusic', sans_nettoyage=False, gpu='auto', mode='auto',
                               octave=octave, comparer_modeles=False, comparer_pas=None, modele_parole=None,
                               transposer=transposer)
        travaux = [{'id': 'v1', 'entree': 'e.flac', 'gain_entree': 1.0}]
        with mock.patch.object(self.D, 'transposition_disponible', return_value=(True, '')):
            return self.D.reglages_noyau(a, voix, None, travaux)

    def test_reglages_du_noyau_zone_et_transposer(self):
        v = self.voix()
        r = self.reglages(v)
        self.assertEqual(r['travaux'][0]['transposer'], 'auto')
        self.assertEqual(r['voix']['zone'], v['zone'])
        for valeur in ('non', -3):
            r = self.reglages(v, transposer=valeur, octave='-12')
            self.assertEqual(r['travaux'][0]['transposer'], valeur)
            self.assertEqual(r['travaux'][0]['octave'], -12)   # --octave garde son sens

    def test_duo_chaque_voix_porte_sa_zone(self):
        a = self.voix('jules', tessiture={'confort': [150.0, 230.0], 'p50': 180.0})
        b = self.voix('marie', parole=190.0)
        a['cle'], b['cle'] = 'homme', 'femme'
        duo = {'id': 'jules+marie', 'affichage': 'Jules et Marie', 'reference': a['reference'], 'f0': a['f0'],
               'fiche': a['fiche'], 'mode': 'genre', 'membres': [a, b], 'zone': a['zone']}
        r = self.reglages(duo)
        zones = [v['zone'] for v in r['voix_cibles']['voix']]
        self.assertEqual(zones[0]['origine'], 'tessiture')
        self.assertEqual(zones[1]['origine'], 'parole')
        self.assertEqual(zones[1]['centre_hz'], 380.0)


class Repli(Fichiers):
    def test_sans_bibliotheque_la_voix_garde_l_octave_seule(self):
        """Rubber Band absent : avertissement clair, une seule fois, et le noyau reçoit « non » (octave seule)."""
        with mock.patch.object(self.D, 'transposition_disponible', return_value=(False, 'librubberband introuvable')), \
                contextlib.redirect_stdout(io.StringIO()) as sortie:
            self.assertEqual(self.D.transposition_demandee(argparse.Namespace(transposer='auto')), 'non')
            self.assertEqual(self.D.transposition_demandee(argparse.Namespace(transposer=-3)), 'non')
        texte = sortie.getvalue()
        self.assertEqual(texte.count('ATTENTION'), 1)
        self.assertIn('librubberband2', texte)
        self.assertIn('octave seul', texte)
        # demandé « non » : pas d'avertissement du tout
        self.D._AVIS_RUBBERBAND.clear()
        with mock.patch.object(self.D, 'transposition_disponible', return_value=(False, 'absente')), \
                contextlib.redirect_stdout(io.StringIO()) as sortie:
            self.assertEqual(self.D.transposition_demandee(argparse.Namespace(transposer='non')), 'non')
        self.assertEqual(sortie.getvalue(), '')

    def test_disponible_dit_pourquoi(self):
        T = self.D.charger_transposition()
        with mock.patch.object(T, 'bibliotheque', side_effect=OSError('librubberband introuvable (paquet librubberband2)')):
            ok, raison = T.disponible()
        self.assertFalse(ok)
        self.assertIn('librubberband2', raison)


@unittest.skipUnless(FFMPEG, 'ffmpeg absent')
class Musique(Fichiers):
    def setUp(self):
        super().setUp()
        self.T = self.D.charger_transposition()
        ok, raison = self.T.disponible()
        if not ok:
            self.skipTest(f'Rubber Band R3 absent ({raison})')

    def test_duree_exacte_et_hauteur_en_demi_tons_entiers(self):
        for n, dt in ((SR * 3 + 123, -3), (SR * 2 + 1, 5), (SR * 2, 0)):
            x = np.stack([musique_tonale(n), 0.5 * musique_tonale(n)], axis=1).astype(np.float32)
            y = self.T.transposer(x, dt)
            self.assertEqual(y.shape, x.shape)
            self.assertEqual(y.dtype, np.float32)
            if dt:
                self.assertAlmostEqual(pic_hz(y[SR // 2:-SR // 2, 0]), 440 * 2 ** (dt / 12), delta=2.0)
            else:
                self.assertTrue(np.array_equal(x, y))

    def test_fichier_a_fichier(self):
        n = SR * 2 + 77
        entree, sortie = self.tmp / 'entree.wav', self.tmp / 'sortie.wav'
        ecrire_wav(entree, musique_tonale(n), canaux=2)
        self.assertEqual(self.T.transposer_fichier(entree, sortie, -4), n)
        self.assertEqual(self.D.echantillons(sortie)[0], self.D.echantillons(entree)[0])

    def travail(self, n=SR * 2):
        t = self.tmp / 'travail'
        t.mkdir(exist_ok=True)
        ecrire_wav(t / 'original.wav', musique_tonale(n), canaux=2)
        ecrire_wav(t / 'voix_source.wav', np.zeros(n), canaux=2)
        ecrire_wav(t / 'instrumental.wav', musique_tonale(n), canaux=2)
        return t

    def test_cache_dans_travail_et_refait_si_le_son_change(self):
        t = self.travail()
        with contextlib.redirect_stdout(io.StringIO()):
            f, info = self.D.musique_transposee(t, -3)
            self.assertEqual(f, t / 'instrumental_transpose_-3.wav')
            self.assertEqual((info['applique'], info['cache'], info['musique_dt']), (True, False, -3))
            mtime = f.stat().st_mtime_ns
            f2, info2 = self.D.musique_transposee(t, -3)
            self.assertEqual((f2, info2['cache'], f.stat().st_mtime_ns), (f, True, mtime))   # --remix : réutilisée
            # un nouveau son d'origine invalide le cache
            os.utime(t / 'original.wav', ns=(mtime + 10 ** 9, mtime + 10 ** 9))
            _, info3 = self.D.musique_transposee(t, -3)
            self.assertFalse(info3['cache'])
            # dt = 0 : la musique seule d'origine, rien d'écrit
            f0, info0 = self.D.musique_transposee(t, 0)
            self.assertEqual((f0, info0['applique']), (t / 'instrumental.wav', False))
            self.assertFalse((t / 'instrumental_transpose_0.wav').exists())
        self.assertEqual(sorted(p.name for p in t.glob('.*')), [])

    def test_sans_bibliotheque_musique_d_origine_et_avertissement(self):
        t = self.travail(SR)
        with mock.patch.object(self.D, 'transposition_disponible', return_value=(False, 'absente')), \
                contextlib.redirect_stdout(io.StringIO()) as sortie:
            f, info = self.D.musique_transposee(t, 3)
        self.assertEqual(f, t / 'instrumental.wav')
        self.assertFalse(info['applique'])
        self.assertIn('absente', info['raison'])
        self.assertIn('PAS transposée de +3', sortie.getvalue())
        self.assertFalse((t / 'instrumental_transpose_3.wav').exists())


@unittest.skipUnless(FFMPEG, 'ffmpeg absent')
class Mixage(Fichiers):
    """finir() sur un travail/ comme au retour de Kaggle : musique tonale (440 Hz) sous une voix de bruit, rapport du noyau
    avec la décision de transposition."""

    def setUp(self):
        super().setUp()
        ok, raison = self.D.charger_transposition().disponible()
        if not ok:
            self.skipTest(f'Rubber Band R3 absent ({raison})')

    def doubler(self, nom='Clip', dt=-3, mode='chant', transposer='auto', remix=False, refaire=True):
        (self.tmp / 'videos').mkdir(exist_ok=True)
        dossier = self.tmp / 'sorties' / nom
        if refaire:
            source = video(self.tmp / 'videos' / f'{nom}.mp4', 5.0)
            t, tv, _ = travail(self.D, source, dossier, JULES, None, mode)
            n = self.D.echantillons(t / 'original.wav')[0]
            voix_o = bruit(n, 2, -22)
            ecrire_wav(t / 'original.wav', musique_tonale(n) + voix_o, canaux=2)
            ecrire_wav(t / 'voix_source.wav', voix_o, canaux=2)
            (tv / 'rapport.json').write_text(json.dumps({'mode': mode, 'frise': [], 'transposition': {
                'demande': 'auto', 'mode': mode, 'musique_dt': dt, 'applique': bool(dt), 'raison': 'essai',
                'zone_origine': ['parole'], 'max_dt': 5,
                'voix': [{'voix': 'jules', 'zone_hz': [158.7, 252.0], 'avant_hz': {'p10': 200.0, 'p50': 262.0, 'p90': 300.0},
                          'apres_hz': {'p10': 168.0, 'p50': 220.0, 'p90': 252.0}, 'dans_zone': True, 'ecart_zone_dt': 0}]}}))
        else:
            t, tv = dossier / 'travail', dossier / 'travail' / JULES['id']
        a = argparse.Namespace(voix_db=None, mix_v1=False, reverb_db=-13.0, paroles=False, pistes_audio='toutes',
                               autotune=None, sans_autotune=False, autotune_gamme=None, remix=remix, transposer=transposer)
        a.autotune_reglage = self.D.reglage_autotune(a, {})
        tr = {'travail': t, 'tv': tv, 'dossier': dossier, 'base': f'{nom} - voix Jules', 'nom': nom}
        with contextlib.redirect_stdout(io.StringIO()) as texte:
            sortie = self.D.finir(tr, a, JULES)
        self.texte = texte.getvalue()
        return sortie, t, tv

    def pistes(self, sortie):
        return [decoder(sortie, f'0:a:{k}') for k in range(5)]

    def test_cinq_pistes_musique_transposee_original_intact(self):
        sortie, t, tv = self.doubler(dt=-3)
        transposee = t / 'instrumental_transpose_-3.wav'
        self.assertTrue(transposee.exists())
        # durée exacte : même nombre d'échantillons que la musique seule d'origine
        self.assertEqual(self.D.echantillons(transposee)[0], self.D.echantillons(t / 'instrumental.wav')[0])
        self.assertAlmostEqual(pic_hz(decoder(t / 'instrumental.wav')), 440.0, delta=2.0)
        cible = 440 * 2 ** (-3 / 12)
        self.assertAlmostEqual(pic_hz(decoder(transposee)), cible, delta=2.0)
        sons = [s for s in ffprobe(sortie, '-show_streams')['streams'] if s['codec_type'] == 'audio']
        self.assertEqual([s['tags']['handler_name'] for s in sons], [
            'Doublage (voix Jules)', 'Original', 'Beat seul', 'Voix convertie seule (Jules)', "Voix d'origine seule"])
        pistes = self.pistes(sortie)
        self.assertEqual(len({len(p) for p in pistes}), 1, [len(p) for p in pistes])   # durées identiques
        # le doublage et le beat seul sont dans la tonalité transposée ; l'original ne l'est pas
        self.assertAlmostEqual(pic_hz(pistes[0]), cible, delta=3.0)
        self.assertAlmostEqual(pic_hz(pistes[2]), cible, delta=3.0)
        self.assertAlmostEqual(pic_hz(pistes[1]), 440.0, delta=3.0)
        # l'original et la voix d'origine seule sont bien ceux de travail/, non transposés et calés
        for k, ref in ((1, t / 'original.wav'), (4, t / 'voix_source.wav')):
            decalage, pic = position(pistes[k], decoder(ref))
            self.assertGreater(pic, 0.9, f'piste {k}')
            self.assertLessEqual(abs(decalage), 44)
        # le beat seul est la musique transposée du cache
        _, pic = position(pistes[2], decoder(transposee))
        self.assertGreater(pic, 0.9)
        # la voix convertie seule est la voix sans la musique : du bruit, sans raie de musique (ni 440 Hz ni la transposée)
        seule = np.abs(np.fft.rfft(pistes[3] * np.hanning(len(pistes[3]))))
        f = np.fft.rfftfreq(len(pistes[3]), 1 / SR)
        bande = seule[(f > 200) & (f < 900)]
        for hz in (440.0, cible):
            self.assertLess(seule[(f > hz - 2) & (f < hz + 2)].max(), 8 * np.median(bande))
        self.assertAlmostEqual(pic_hz(decoder(tv / 'mix.wav')), cible, delta=3.0)
        rapport = json.loads((tv / 'rapport.json').read_text())
        mix = rapport['mix']['transposition']
        self.assertEqual((mix['musique_dt'], mix['applique'], mix['fichier']), (-3, True, 'instrumental_transpose_-3.wav'))
        self.assertIn('musique transposée de -3 demi-tons', self.texte)
        self.assertIn('chanson transposée de −3 demi-tons pour la voix', self.texte)

    def test_sans_transposition_comme_avant(self):
        sortie, t, tv = self.doubler('Sans', dt=0)
        self.assertEqual(list(t.glob('instrumental_transpose_*')), [])
        pistes = self.pistes(sortie)
        for k in (0, 1, 2):
            self.assertAlmostEqual(pic_hz(pistes[k]), 440.0, delta=3.0)
        rapport = json.loads((tv / 'rapport.json').read_text())
        self.assertFalse(rapport['mix']['transposition']['applique'])
        self.assertNotIn('musique transposée', self.texte)

    def test_parole_jamais_transposee_meme_si_le_rapport_le_dit(self):
        sortie, t, _ = self.doubler('Parole', dt=-4, mode='parole')
        self.assertEqual(list(t.glob('instrumental_transpose_*')), [])
        self.assertAlmostEqual(pic_hz(self.pistes(sortie)[2]), 440.0, delta=3.0)

    def test_remix_reutilise_le_cache_et_suit_le_rapport(self):
        _, t, tv = self.doubler('Remix', dt=-3)
        f = t / 'instrumental_transpose_-3.wav'
        mtime = f.stat().st_mtime_ns
        sortie, _, _ = self.doubler('Remix', dt=-3, remix=True, refaire=False)
        self.assertEqual(f.stat().st_mtime_ns, mtime)
        self.assertIn('cache de travail/', self.texte)
        self.assertNotIn('ne peut pas changer', self.texte)   # transposer auto : rien à dire
        self.assertAlmostEqual(pic_hz(self.pistes(sortie)[2]), 440 * 2 ** (-3 / 12), delta=3.0)
        self.assertTrue(json.loads((tv / 'rapport.json').read_text())['mix']['transposition']['cache'])

    def test_remix_transposer_non_le_dit_clairement(self):
        """--transposer non --remix : la voix est déjà convertie dans la chanson transposée, elle ne change pas sans
        reconvertir ; message clair, musique transposée gardée (sinon la voix serait fausse), pas d'erreur."""
        self.doubler('Non', dt=-3)
        sortie, _, _ = self.doubler('Non', dt=-3, remix=True, transposer='non', refaire=False)
        self.assertIn('--transposer non ne peut pas changer la voix avec --remix', self.texte)
        self.assertIn('-3', self.texte)
        self.assertIn('relancer sans --remix', self.texte)
        self.assertAlmostEqual(pic_hz(self.pistes(sortie)[2]), 440 * 2 ** (-3 / 12), delta=3.0)
        # --transposer N différent de la décision : même avis
        self.doubler('Non', dt=-3, remix=True, transposer=2, refaire=False)
        self.assertIn('--transposer +2 ne peut pas changer la voix avec --remix', self.texte)

    def test_remix_sans_bibliotheque_avertit_et_garde_la_musique(self):
        self.doubler('Repli', dt=-3)
        with mock.patch.object(self.D, 'transposition_disponible', return_value=(False, 'absente')):
            sortie, _, tv = self.doubler('Repli', dt=-3, remix=True, refaire=False)
        self.assertIn('ATTENTION', self.texte)
        self.assertIn('PAS transposée de -3', self.texte)
        pistes = self.pistes(sortie)
        self.assertAlmostEqual(pic_hz(pistes[2]), 440.0, delta=3.0)   # musique d'origine, faute de bibliothèque
        self.assertEqual(len({len(p) for p in pistes}), 1)
        mix = json.loads((tv / 'rapport.json').read_text())['mix']['transposition']
        self.assertFalse(mix['applique'])
        self.assertIn('Rubber Band indisponible', mix['raison'])

    def test_remix_en_ligne_de_commande(self):
        """doubler --remix --transposer non sur un doublage transposé : code 0 et message, sans trace d'erreur."""
        env = {**os.environ, 'HOME': str(self.tmp / 'home')}
        for v in ('VOIXLA_KAGGLE', 'VOIXLA_DONNEES', 'DOUBLER_VOIX'):
            env.pop(v, None)
        donnees = self.tmp / 'donnees'
        (donnees / 'voix' / 'jules').mkdir(parents=True)
        ecrire_wav(donnees / 'voix' / 'jules' / 'reference.wav', bruit(SR, 5, -20))
        (donnees / 'voix' / 'jules' / 'voix.json').write_text(json.dumps(
            {'nom': 'Jules', 'reference': 'reference.wav', 'hauteur_parole_mediane_hz': 100.0}))
        (self.tmp / 'videos').mkdir()
        source = video(self.tmp / 'videos' / 'Clip.mp4', 4.0)
        t, tv, _ = travail(self.D, source, donnees / 'sorties' / 'Clip', JULES)
        n = self.D.echantillons(t / 'original.wav')[0]
        ecrire_wav(t / 'original.wav', musique_tonale(n) + bruit(n, 2, -22), canaux=2)
        ecrire_wav(t / 'voix_source.wav', bruit(n, 2, -22), canaux=2)
        (tv / 'rapport.json').write_text(json.dumps({'mode': 'chant', 'frise': [], 'transposition': {
            'mode': 'chant', 'musique_dt': 2, 'applique': True, 'voix': []}}))
        r = subprocess.run([python(), str(DOUBLER), '--help'], capture_output=True, text=True, env=env, timeout=60)
        self.assertIn('--transposer', r.stdout)
        r = subprocess.run([python(), str(DOUBLER), '--remix', '--voix', 'jules', '--donnees', str(donnees), '--sans-paroles',
                            '--transposer', 'non', str(source)], capture_output=True, text=True, env=env, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        self.assertIn('--transposer non ne peut pas changer la voix avec --remix', r.stdout)
        self.assertTrue((t / 'instrumental_transpose_2.wav').exists())
        r = subprocess.run([python(), str(DOUBLER), '--transposer', 'beaucoup', str(source)], capture_output=True, text=True,
                           env=env, timeout=60)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('auto, non ou un nombre entier', r.stderr)


class LigneDuRapport(Fichiers):
    def test_ligne_transposition(self):
        L = self.D.ligne_transposition
        self.assertIsNone(L({}))
        self.assertIsNone(L({'transposition': {'mode': 'parole', 'musique_dt': 0}}))
        self.assertIn('non transposée', L({'transposition': {'mode': 'chant', 'musique_dt': 0, 'raison': 'déjà bien'}}))
        ligne = L({'transposition': {'mode': 'chant', 'musique_dt': -3, 'voix': [{
            'avant_hz': {'p50': 262.0}, 'apres_hz': {'p50': 220.0}, 'zone_hz': [158.7, 252.0]}]}})
        self.assertEqual(ligne, 'chanson transposée de −3 demi-tons pour la voix : médiane 262 → 220 Hz, zone 159–252 Hz')


if __name__ == '__main__':
    unittest.main()
