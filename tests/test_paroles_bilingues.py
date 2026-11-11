"""Paroles bilingues : phrases ambiguës (refrain mêlant deux langues) retranscrites dans leurs deux langues, la plus
sûre gardée ; plafond ; clé désactivée = comportement d'avant. Faux Whisper (texte, probabilités, langue), sans GPU.
Textes inventés."""
import unittest

import numpy as np

from commun import charger_noyau
from test_etape_paroles import sortie_pipeline

N = charger_noyau()


def phrases_de(n, duree=3.0, pas=4.0):
    return [[round(i * pas, 2), round(i * pas + duree, 2)] for i in range(n)]


class PhrasesAmbigues(unittest.TestCase):
    def test_phrase_sure_pas_essayee(self):
        ph = phrases_de(3)
        langues = [('es', 0.95, 3.0)] * 3
        self.assertEqual(N.phrases_ambigues(ph, langues, ['es'] * 3, [[['es', 0.95]]] * 3), [])

    def test_langue_peu_sure_essayee_dans_la_seconde(self):
        ph = phrases_de(3)
        langues = [('es', 0.95, 3.0), ('es', 0.6, 3.0), ('es', 0.95, 3.0)]
        tops = [[['es', 0.95]], [['es', 0.6], ['fr', 0.35]], [['es', 0.95]]]
        self.assertEqual(N.phrases_ambigues(ph, langues, ['es'] * 3, tops), [(1, 'es', 'fr', 0.4)])

    def test_langue_lissee_essayee_dans_la_langue_detectee(self):
        ph = phrases_de(3)
        langues = [('es', 0.95, 3.0), ('fr', 0.3, 3.0), ('es', 0.95, 3.0)]
        tops = [None, [['fr', 0.3], ['es', 0.25]], None]
        res = N.phrases_ambigues(ph, langues, ['es', 'es', 'es'], tops)
        self.assertEqual([(i, a, b) for i, a, b, _ in res], [(1, 'es', 'fr')])

    def test_phrase_seule_dans_sa_langue_entre_deux_voisines(self):
        ph = phrases_de(3)
        langues = [('es', 0.95, 3.0), ('fr', 0.9, 3.0), ('es', 0.95, 3.0)]
        res = N.phrases_ambigues(ph, langues, ['es', 'fr', 'es'], [None] * 3)
        self.assertEqual([(i, a, b) for i, a, b, _ in res], [(1, 'fr', 'es')])

    def test_phrase_trop_courte_ignoree(self):
        ph = [[0.0, 0.5], [4.0, 7.0]]
        langues = [('es', 0.3, 0.5), ('es', 0.95, 3.0)]
        self.assertEqual(N.phrases_ambigues(ph, langues, ['es', 'es'], [[['fr', 0.5]], None]), [])

    def test_plafond_en_nombre_et_en_duree(self):
        ph = phrases_de(10)
        langues = [('es', 0.5, 3.0)] * 10
        tops = [[['es', 0.5], ['fr', 0.4]]] * 10
        res = N.phrases_ambigues(ph, langues, ['es'] * 10, tops, {'bilingue_max_phrases': 3, 'bilingue_max_part': 1.0})
        self.assertEqual(len(res), 3)
        res = N.phrases_ambigues(ph, langues, ['es'] * 10, tops, {'bilingue_max_part': 0.2})   # 20 % de 30 s : 2 phrases
        self.assertEqual(len(res), 2)

    def test_les_plus_douteuses_d_abord(self):
        ph = phrases_de(3)
        langues = [('es', 0.7, 3.0), ('es', 0.4, 3.0), ('es', 0.6, 3.0)]
        tops = [[['es', 0.7], ['fr', 0.2]], [['es', 0.4], ['fr', 0.4]], [['es', 0.6], ['fr', 0.3]]]
        res = N.phrases_ambigues(ph, langues, ['es'] * 3, tops, {'bilingue_max_phrases': 1, 'bilingue_max_part': 1.0})
        self.assertEqual(res[0][0], 1)

    def test_desactive(self):
        ph = phrases_de(3)
        langues = [('es', 0.3, 3.0)] * 3
        self.assertEqual(N.phrases_ambigues(ph, langues, ['es'] * 3, [[['fr', 0.5]]] * 3, {'bilingue': False}), [])


class Note(unittest.TestCase):
    def test_note_et_penalite(self):
        self.assertEqual(N.note_transcription('', -0.2), float('-inf'))
        self.assertEqual(N.note_transcription('bonjour', None), float('-inf'))
        self.assertAlmostEqual(N.note_transcription('la luna baila', -0.3), -0.3)
        boucle = 'la ' * 60
        self.assertLess(N.note_transcription(boucle, -0.3), -0.3 - 0.5)

    def test_departage(self):
        notes = {'es': -1.2, 'fr': -0.3}
        gagnante, n_es, n_fr, texte = N.departager_langue(
            None, 'es', 'fr', lambda a, lg: f'texte {lg}', lambda a, lg, t: notes[lg])
        self.assertEqual((gagnante, texte), ('fr', 'texte fr'))
        # écart sous la marge : la langue retenue reste
        notes = {'es': -0.5, 'fr': -0.4}
        self.assertEqual(N.departager_langue(None, 'es', 'fr', lambda a, lg: f'texte {lg}',
                                             lambda a, lg, t: notes[lg])[0], 'es')

    def test_essai_en_echec_ne_gagne_pas(self):
        def transcrire(a, lg):
            if lg == 'fr':
                raise RuntimeError('boum')
            return 'texte es'
        self.assertEqual(N.departager_langue(None, 'es', 'fr', transcrire, lambda a, lg, t: -2.0)[0], 'es')


class Enchainement(unittest.TestCase):
    """transcrire_video avec un faux Whisper : la 2e phrase, détectée peu sûre en espagnol, est en fait française."""

    def setUp(self):
        self.x16 = np.full(16000 * 20, 0.1, np.float32)
        self.v = {'phrases': [[1.0, 4.0], [6.0, 9.0], [11.0, 14.0]], 'chant': [[1.0, 4.0], [6.0, 9.0], [11.0, 14.0]],
                  'piste': 'lead_sec'}
        self.n_detections = 0
        self.notes_appels = []

    def detecter(self, audio):
        n = self.n_detections
        self.n_detections += 1
        return [('es', 0.95, [['es', 0.95]]), ('es', 0.55, [['es', 0.55], ['fr', 0.4]]), ('es', 0.95, [['es', 0.95]])][n % 3]

    def transcrire(self, audio, lg, mots=True):
        faux = {'es': ['Mi', 'ke', 'ri', 'da'], 'fr': ['Ma', 'chère', 'amie', 'ici']}
        return sortie_pipeline(faux[lg]), 'mots', []

    def noter(self, audio, lg, texte):
        self.notes_appels.append(lg)
        return -1.5 if lg == 'es' else -0.3

    def test_phrase_ambigue_retranscrite_dans_l_autre_langue(self):
        r = {}
        lignes = N.transcrire_video(self.v, self.x16, self.detecter, self.transcrire, r, self.noter)
        self.assertEqual([p[4] for p in r['langues_phrases']], ['es', 'fr', 'es'])
        self.assertEqual(r['bilingue']['essayees'], 1)
        self.assertEqual(r['bilingue']['changees'], 1)
        self.assertEqual(r['bilingue']['secondes'], 3.0)
        self.assertEqual([l['langue'] for l in lignes if 'chère' in l['texte']], ['fr'])
        self.assertEqual(self.notes_appels, ['es', 'fr'])

    def test_phrases_sures_pas_retranscrites(self):
        r = {}
        N.transcrire_video(self.v, self.x16, lambda a: ('es', 0.95, [['es', 0.95]]), self.transcrire, r, self.noter)
        self.assertEqual(r['bilingue']['essayees'], 0)
        self.assertEqual(self.notes_appels, [])

    def test_la_langue_retenue_reste_si_elle_est_plus_sure(self):
        r = {}
        N.transcrire_video(self.v, self.x16, self.detecter, self.transcrire, r,
                           lambda a, lg, t: -0.2 if lg == 'es' else -0.9)
        self.assertEqual([p[4] for p in r['langues_phrases']], ['es', 'es', 'es'])
        self.assertEqual(r['bilingue']['changees'], 0)

    def test_plafond_respecte(self):
        r = {}
        det = lambda a: ('es', 0.5, [['es', 0.5], ['fr', 0.45]])
        N.transcrire_video(self.v, self.x16, det, self.transcrire, r, self.noter, {'bilingue_max_phrases': 1})
        self.assertEqual(r['bilingue']['essayees'], 1)
        self.assertEqual(self.notes_appels, ['es', 'fr'])

    def test_desactive_comme_avant(self):
        avant = N.transcrire_video(self.v, self.x16, self.detecter, self.transcrire, {}, None)
        r = {}
        lignes = N.transcrire_video(self.v, self.x16, self.detecter, self.transcrire, r, self.noter, {'bilingue': False})
        self.assertNotIn('bilingue', r)
        self.assertEqual(self.notes_appels, [])
        self.assertEqual([p[4] for p in r['langues_phrases']], ['es', 'es', 'es'])
        self.assertEqual(lignes, avant)


if __name__ == '__main__':
    unittest.main()
