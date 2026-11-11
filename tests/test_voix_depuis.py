"""Voix des vidéos (11/11/2026) : doubler --voix-depuis garde comme voix cible la voix d'un chanteur d'un doublage fini.
Sons et noms de synthèse (voix_a, chanteur_b), sans GPU :
- passages_seuls : seuls les passages où le chanteur demandé chante seul (ni chœurs, ni unisson, ni duo de l'affectation,
  ni muet, ni trop court, ni trop peu voisé) ;
- choisir_passages : les plus présents d'abord, 25 s visées, 30 s au plus ;
- refus sans --chanteur dans un duo (message clair), --chanteur par numéro ou par clé, une seule voix sans --chanteur ;
- voix créée : référence de 15 à 30 s, fiche avec origine video, imitation_ia, « voix d'artiste, usage privé », avis ;
- doublage fait avec cette voix : « imitation par IA » dans rapport.json et dans le comment de la vidéo ;
- voix ordinaires : comme avant (rien dans le rapport, rien dans la vidéo, fiche sans imitation_ia)."""
import contextlib
import io
import json
import unittest

import numpy as np

from commun import FFMPEG, SR, Dossier, charger_doubler, ecrire_wav, ffprobe, note
from test_pistes_audio import bruit, travail, video

# (durée, chanteur, genre, niveau en dB) ; genres : seul, choeurs, duo (passage en duo de l'affectation), muet, court, creux
PLAN = [(5, 'homme', 'seul', -20), (5, 'homme', 'seul', -30), (5, 'homme', 'seul', -18), (5, 'homme', 'seul', -22),
        (5, 'homme', 'seul', -19), (5, 'homme', 'choeurs', -19), (5, 'homme', 'duo', -19), (5, 'homme', 'muet', -60),
        (5, 'femme', 'seul', -20), (5, 'femme', 'seul', -19), (1, 'homme', 'seul', -19), (5, 'homme', 'creux', -19),
        (5, 'homme', 'unisson', -19), (5, 'homme', 'seul', -19), (5, 'homme', 'seul', -21), (5, 'homme', 'seul', -17),
        (5, 'femme', 'seul', -21)]


def rapport_de(plan):
    morceaux, passages, t = [], [], 0.0
    for i, (d, chanteur, genre, niveau) in enumerate(plan):
        a, b = t, t + d
        vp = {'lead_sec': f'voix_{chanteur}'}
        if genre == 'choeurs':
            vp['choeurs_sec'] = f'voix_{chanteur}'
        if genre == 'unisson':
            vp = {'lead_sec': [f'voix_{chanteur}', 'voix_c']}
        if genre == 'duo':
            passages.append({'debut_s': a, 'fin_s': b, 'voix': ['voix_a', 'voix_b']})
        morceaux.append({'i': i, 'debut_s': a, 'fin_s': b, 'muet': genre == 'muet', 'f0_med': None if genre == 'muet' else 200.0,
                         'voise_s': 0.0 if genre == 'muet' else round(d * (0.1 if genre == 'creux' else 0.9), 2),
                         'chanteur': chanteur, 'voix_cible': f'voix_{chanteur}', 'voix_pistes': vp, 'base': 'essai'})
        t = b
    return {'mode': 'chant', 'affectation': {'passages': passages}, 'morceaux': morceaux}, t


def voix_source(plan, duree):
    x = np.zeros(int((duree + 1) * SR))
    t = 0.0
    for d, chanteur, genre, niveau in plan:
        if genre != 'muet':
            f0 = 200 if chanteur == 'homme' else 300
            n = note(f0, 0, d - 0.2, niveau, d)
            x[int(t * SR):int(t * SR) + len(n)] += n
        t += d
    return x


@unittest.skipUnless(FFMPEG, 'ffmpeg absent')
class VoixDepuis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._d = Dossier()
        cls.tmp = cls._d.__enter__()
        (cls.tmp / 'home').mkdir()
        cls.D = charger_doubler(cls.tmp / 'home')
        cls.rapport, cls.duree = rapport_de(PLAN)
        cls.dossier = cls.tmp / 'sorties' / 'clip_essai'
        tv = cls.dossier / 'travail' / 'voix_a+voix_b'
        tv.mkdir(parents=True)
        ecrire_wav(cls.dossier / 'travail' / 'voix_source.wav', voix_source(PLAN, cls.duree), canaux=2)
        (tv / 'rapport.json').write_text(json.dumps(cls.rapport))

    @classmethod
    def tearDownClass(cls):
        cls._d.__exit__(None, None, None)

    def creer(self, nom, chanteur=None, source=None):
        with contextlib.redirect_stdout(io.StringIO()) as sortie:
            self.D.voix_depuis(source or self.dossier, nom, chanteur)
        return sortie.getvalue()

    def fiche(self, nom):
        return json.loads((self.D.VOIX / nom / 'voix.json').read_text())

    # --- choix des passages ---

    def test_passages_seuls_du_bon_chanteur(self):
        def indices(chanteur):
            debuts = [a for a, _, _ in self.D.passages_seuls(self.rapport, chanteur)]
            return [m['i'] for m in self.rapport['morceaux'] if m['debut_s'] in debuts]
        # écartés : 5 chœurs, 6 duo, 7 muet, 10 trop court, 11 creux, 12 unisson, et l'autre chanteur
        self.assertEqual(indices('homme'), [0, 1, 2, 3, 4, 13, 14, 15])
        self.assertEqual(indices('femme'), [8, 9, 16])

    def test_passages_choisis_les_plus_presents(self):
        passages = [(0.0, 5.0, 0.9), (10.0, 15.0, 0.9), (20.0, 25.0, 0.9), (30.0, 35.0, 0.9), (40.0, 45.0, 0.9),
                    (50.0, 55.0, 0.9), (60.0, 65.0, 0.9)]
        niveaux = [-20.0, -30.0, -18.0, -22.0, -19.0, -21.0, -17.0]
        pris, total = self.D.choisir_passages(passages, niveaux)
        self.assertEqual([a for a, _, _ in pris], [0.0, 20.0, 40.0, 50.0, 60.0])
        self.assertEqual(total, 25.0)
        # la part voisée compte aussi : un passage creux et fort passe après un passage net un peu moins fort
        pris, _ = self.D.choisir_passages([(0.0, 20.0, 0.5), (30.0, 50.0, 0.95)], [-18.0, -20.0])
        self.assertEqual(pris, [(30.0, 50.0, 0.95)])

    def test_trente_secondes_au_plus(self):
        passages = [(k * 20.0, k * 20.0 + 14.0, 0.9) for k in range(4)]
        pris, total = self.D.choisir_passages(passages, [-20.0] * 4)
        self.assertLessEqual(total, self.D.REF_VIDEO_MAX_S)
        self.assertEqual(len(pris), 2)

    # --- choix du chanteur ---

    def test_duo_sans_chanteur_refuse(self):
        with self.assertRaises(SystemExit) as e:
            self.creer('refus')
        self.assertIn('--chanteur', str(e.exception))
        self.assertIn('2 voix', str(e.exception))
        self.assertFalse((self.D.VOIX / 'refus').exists())

    def test_chanteur_inconnu_refuse(self):
        with self.assertRaises(SystemExit) as e:
            self.creer('refus2', '5')
        self.assertIn('pas dans ce doublage', str(e.exception))

    def test_une_seule_voix_sans_chanteur(self):
        ch = self.D.chanteurs_du_rapport({'morceaux': [m for m in self.rapport['morceaux'] if m['chanteur'] == 'homme']})
        self.assertEqual(self.D.choisir_chanteur(ch, None), 'homme')
        ch = self.D.chanteurs_du_rapport(self.rapport)
        self.assertEqual([c for c, _ in ch], ['homme', 'femme'])
        self.assertEqual(self.D.choisir_chanteur(ch, '2'), 'femme')
        self.assertEqual(self.D.choisir_chanteur(ch, 'homme'), 'homme')

    # --- la voix créée ---

    def test_voix_creee_du_chanteur_demande(self):
        sortie = self.creer('voix_b1', '1')
        d = self.D.VOIX / 'voix_b1'
        duree = float(ffprobe(d / 'reference.wav', '-show_format')['format']['duration'])
        self.assertTrue(15 <= duree <= 30, duree)
        f = self.fiche('voix_b1')
        self.assertEqual((f['origine'], f['imitation_ia'], f['mention'], f['chanteur']),
                         ('video', True, "voix d'artiste, usage privé", 'homme'))
        self.assertIn('imitation par IA', f['consentement'])
        # les cinq passages les plus nets, pas le passage faible (indice 1, 5 à 10 s)
        debuts = sorted(a for a, _ in f['passages_s'])
        self.assertEqual(debuts, [0.0, 10.0, 20.0, 61.0, 71.0])
        self.assertNotIn(5.0, debuts)
        self.assertEqual(len(debuts), 5)
        self.assertAlmostEqual(f['hauteur_parole_mediane_hz'], 200, delta=15)
        self.assertIn("voix d'artiste : imitation par IA, pour un usage privé seulement", sortie)
        charge = self.D.charger_voix('voix_b1')
        self.assertTrue(charge['fiche']['imitation_ia'])

    def test_autre_chanteur_par_la_cle_et_par_la_video(self):
        (self.dossier / 'clip_essai - doublage.mp4').write_bytes(b'')
        self.creer('voix_b2', 'femme', self.dossier / 'clip_essai - doublage.mp4')
        f = self.fiche('voix_b2')
        self.assertEqual(f['chanteur'], 'femme')
        self.assertAlmostEqual(f['hauteur_parole_mediane_hz'], 300, delta=25)
        self.assertEqual(f['alertes'], [])

    def test_nom_et_doublon(self):
        with self.assertRaises(SystemExit):
            self.creer('Voix B', '1')
        self.creer('voix_b3', '1')
        with self.assertRaises(SystemExit) as e:
            self.creer('voix_b3', '1')
        self.assertIn('existe déjà', str(e.exception))

    def test_source_sans_travail(self):
        vide = self.tmp / 'vide'
        vide.mkdir()
        with self.assertRaises(SystemExit) as e:
            self.creer('voix_b4', '1', vide)
        self.assertIn('voix_source.wav', str(e.exception))

    # --- garde-fous dans les doublages ---

    def test_mention_dans_rapport_et_commentaire_de_la_video(self):
        D = self.D
        imitee = {'id': 'voix_b1', 'affichage': 'Voix B1', 'fiche': {'imitation_ia': True}}
        ordinaire = {'id': 'voix_c', 'affichage': 'Voix C', 'fiche': {'nom': 'Voix C'}}
        d = self.tmp / 'doublages'
        d.mkdir(exist_ok=True)
        source = video(d / 'source.mp4', 4.0)
        resultats = {}
        for cle, voix in (('imitee', imitee), ('ordinaire', ordinaire)):
            sortie = d / cle
            t, tv, _ = travail(D, source, sortie, voix)
            n = D.echantillons(t / 'original.wav')[0]
            ecrire_wav(tv / 'mix.wav', bruit(n, 7, -18), canaux=2)
            rapport = {'mode': 'chant'}
            D.mentionner_imitation_ia(voix, rapport)
            with contextlib.redirect_stdout(io.StringIO()):
                mp4 = D.remettre_sur_video(t, tv / 'mix.wav', sortie, f'{cle} - voix', [], D.plan_video(t), [], 'Doublage', 'spa')
            tags = ffprobe(mp4, '-show_format')['format'].get('tags') or {}
            resultats[cle] = (rapport, tags.get('comment'))
        rapport, comment = resultats['imitee']
        self.assertEqual(rapport['imitation_ia'], {'mention': 'imitation par IA', 'usage': "voix d'artiste, usage privé",
                                                   'voix': ['voix_b1']})
        self.assertIn('imitation par IA', comment)
        self.assertEqual(resultats['ordinaire'], ({'mode': 'chant'}, None))
        self.assertEqual(D.meta_ia(), [])   # la dernière voix était ordinaire : plus de mention

    def test_voix_ordinaire_comme_avant(self):
        D = self.D
        voix = {'id': 'voix_c', 'affichage': 'Voix C', 'fiche': {}}
        rapport = {'mode': 'chant', 'imitation_ia': 'reste d\'avant'}
        D.mentionner_imitation_ia(voix, rapport)
        self.assertEqual((rapport, D.meta_ia(), D.voix_imitation_ia(voix)), ({'mode': 'chant'}, [], []))
        # une voix combinée : il suffit qu'un membre soit une imitation
        duo = {'id': 'a+b', 'affichage': 'A et B', 'membres': [voix, {'id': 'voix_b1', 'fiche': {'imitation_ia': True}}]}
        D.mentionner_imitation_ia(duo, rapport)
        self.assertEqual(rapport['imitation_ia']['voix'], ['voix_b1'])
        D.mentionner_imitation_ia(voix, rapport)

    def test_fiche_ordinaire_sans_imitation(self):
        d = self.D.VOIX / 'voix_ordinaire'
        fiche, _ = self.D.ecrire_voix('voix_ordinaire', d, note(200, 0, 3, -20, 3), SR, 3.0, 40.0, [], 'essai')
        self.assertNotIn('imitation_ia', fiche)
        self.assertEqual(fiche['origine'], 'essai')
        self.assertTrue((d / 'reference.wav').is_file())

    def test_ajouter_voix_inchange(self):
        """--ajouter-voix (même code d'écriture) : fiche ordinaire, sans imitation_ia."""
        x = np.concatenate([note(150, 0.2, 3.8, -20, 4.0), np.zeros(SR // 2)] * 5)
        ecrire_wav(self.tmp / 'parole.wav', x)
        with contextlib.redirect_stdout(io.StringIO()):
            self.D.ajouter_voix('voix_d', self.tmp / 'parole.wav')
        f = self.fiche('voix_d')
        self.assertNotIn('imitation_ia', f)
        self.assertIn('doubler --ajouter-voix', f['origine'])
        self.assertTrue(f['consentement'].startswith('à confirmer'))


if __name__ == '__main__':
    unittest.main()
