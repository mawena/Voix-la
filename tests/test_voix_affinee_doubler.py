"""Voix affinée, côté doubler.py : lecture de l'affinage rangé par affiner (fiche, somme SHA-256), choix du modèle voix par
voix (--affinee auto, oui ou non ; « affinage_pret » de la configuration), refus de oui hors de Kaggle, réglages envoyés au
noyau, fichiers joints au jeu de données, clés de configuration, lignes du rapport. Aucune session Kaggle, Modal ni
Runpod : tout est local, dans un dossier temporaire. Voix inventées (voix_a, voix_b)."""
import argparse
import contextlib
import hashlib
import io
import json
import unittest

from commun import Dossier, charger_doubler

OCTETS = b'point de controle de pacotille'


class Base(unittest.TestCase):
    def setUp(self):
        self._d = Dossier()
        self.tmp = self._d.__enter__()
        self.addCleanup(self._d.__exit__)
        self.D = charger_doubler(self.tmp)
        self.D.VOIX = self.tmp / 'voix'

    def voix(self, nom, f0=110.0):
        d = self.D.VOIX / nom
        d.mkdir(parents=True, exist_ok=True)
        (d / 'reference.wav').write_bytes(b'RIFF' + nom.encode())
        (d / 'voix.json').write_text(json.dumps({'nom': nom, 'hauteur_parole_mediane_hz': f0, 'duree_s': 20.0}))

    def affiner(self, nom, octets=OCTETS, sha=None, config=True, **fiche):
        """Range un affinage comme le fait affiner.py : affinage.pth, affinage.yml, affinage.json."""
        d = self.D.VOIX / nom / 'affinage'
        d.mkdir(parents=True, exist_ok=True)
        (d / 'affinage.pth').write_bytes(octets)
        if config:
            (d / 'affinage.yml').write_text('model_params: {}\n')
        (d / 'affinage.json').write_text(json.dumps({
            'format': 1, 'voix': nom, 'moteur': 'seedvc-44k', 'point': 'affinage.pth',
            'config': 'affinage.yml' if config else None, 'pas': 1500, 'complet': True,
            'sha256': sha or hashlib.sha256(octets).hexdigest(), 'taille_mo': 0.0, **fiche}))

    def choisir(self, spec='voix_a', affinee='auto', pret=False, calcul='kaggle', moteur='yingmusic'):
        a = argparse.Namespace(affinee=affinee, calcul=calcul, moteur=moteur)
        conf = {'affinage_pret': pret} if pret else {}
        voix = self.D.charger_voix_spec(spec)
        with contextlib.redirect_stdout(io.StringIO()) as sortie:
            choix = self.D.choisir_affinage(a, conf, voix)
        return choix, voix, a, sortie.getvalue()


class LectureDeLAffinage(Base):
    def test_affinage_present(self):
        self.voix('voix_a')
        self.affiner('voix_a')
        i, raison = self.D.lire_affinage('voix_a')
        self.assertIsNone(raison)
        self.assertEqual((i['fichier'], i['config'], i['pas'], i['complet']), ('affinage-voix_a.pth', 'affinage-voix_a.yml', 1500, True))
        self.assertEqual(i['sha256'], hashlib.sha256(OCTETS).hexdigest())

    def test_pas_d_affinage(self):
        self.voix('voix_a')
        self.assertEqual(self.D.lire_affinage('voix_a'), (None, "pas d'affinage"))

    def test_point_absent(self):
        self.voix('voix_a')
        self.affiner('voix_a')
        (self.D.VOIX / 'voix_a' / 'affinage' / 'affinage.pth').unlink()
        self.assertEqual(self.D.lire_affinage('voix_a')[1], 'point de contrôle absent')

    def test_somme_qui_ne_correspond_pas(self):
        self.voix('voix_a')
        self.affiner('voix_a', sha='0' * 64)
        self.assertIn('SHA-256', self.D.lire_affinage('voix_a')[1])

    def test_sans_config(self):
        self.voix('voix_a')
        self.affiner('voix_a', config=False)
        i, _ = self.D.lire_affinage('voix_a')
        self.assertIsNone(i['config'])


class ChoixParVoix(Base):
    def setUp(self):
        super().setUp()
        self.voix('voix_a')
        self.voix('voix_b', 210.0)

    def test_auto_sans_affinage_comme_avant(self):
        choix, voix, a, texte = self.choisir(pret=True)
        self.assertEqual(choix, {})
        self.assertNotIn('affinage', voix)
        self.assertEqual(a.moteur, 'yingmusic')
        self.assertEqual(texte, '')

    def test_auto_refuse_tant_que_affinage_pret_est_faux(self):
        self.affiner('voix_a')
        choix, voix, a, texte = self.choisir(pret=False)
        self.assertEqual(choix, {})
        self.assertNotIn('affinage', voix)
        self.assertEqual(a.moteur, 'yingmusic')   # rien ne change
        self.assertIn('affinage_pret', texte)

    def test_auto_prend_l_affinage_quand_il_est_pret(self):
        self.affiner('voix_a')
        choix, voix, a, texte = self.choisir(pret=True)
        self.assertEqual(list(choix), ['voix_a'])
        self.assertEqual(voix['affinage']['fichier'], 'affinage-voix_a.pth')
        self.assertEqual(a.moteur, 'seedvc')   # même en chant : YingMusic-SVC ne prend pas le point affiné
        self.assertIn('Seed-VC 44 kHz affiné', texte)

    def test_non_ne_prend_jamais(self):
        self.affiner('voix_a')
        choix, voix, a, _ = self.choisir(affinee='non', pret=True)
        self.assertEqual(choix, {})
        self.assertNotIn('affinage', voix)
        self.assertEqual(a.moteur, 'yingmusic')

    def test_oui_force_meme_sans_affinage_pret(self):
        self.affiner('voix_a')
        choix, voix, a, _ = self.choisir(affinee='oui', pret=False)
        self.assertEqual(list(choix), ['voix_a'])
        self.assertEqual(a.moteur, 'seedvc')

    def test_oui_sans_affinage_est_une_erreur(self):
        with self.assertRaises(SystemExit) as e:
            self.choisir(affinee='oui')
        self.assertIn('aucune des voix choisies', str(e.exception.code))

    def test_oui_avec_un_point_abime_est_une_erreur(self):
        self.affiner('voix_a', sha='1' * 64)
        with self.assertRaises(SystemExit) as e:
            self.choisir(affinee='oui')
        self.assertIn('SHA-256', str(e.exception.code))

    def test_auto_ignore_un_point_abime(self):
        self.affiner('voix_a', sha='1' * 64)
        choix, voix, a, _ = self.choisir(pret=True)
        self.assertEqual(choix, {})
        self.assertEqual(a.moteur, 'yingmusic')

    def test_deux_voix_seule_celle_qui_a_un_affinage_le_prend(self):
        self.affiner('voix_a')
        choix, voix, a, texte = self.choisir('homme:voix_a,femme:voix_b', pret=True)
        self.assertEqual(list(choix), ['voix_a'])
        m = {x['id']: x for x in voix['membres']}
        self.assertIn('affinage', m['voix_a'])
        self.assertNotIn('affinage', m['voix_b'])
        self.assertIn('les autres voix gardent les poids de base', texte)
        self.assertEqual(a.moteur, 'seedvc')

    def test_deux_voix_sans_affinage_comme_avant(self):
        choix, voix, a, _ = self.choisir('homme:voix_a,femme:voix_b', pret=True)
        self.assertEqual(choix, {})
        self.assertEqual(a.moteur, 'yingmusic')

    def test_un_nouveau_choix_efface_le_precedent(self):
        self.affiner('voix_a')
        _, voix, _, _ = self.choisir(pret=True)
        a = argparse.Namespace(affinee='non', calcul='kaggle', moteur='yingmusic')
        self.D.choisir_affinage(a, {}, voix)
        self.assertNotIn('affinage', voix)

    def test_comparaison_sans_effet_et_oui_refuse(self):
        self.affiner('voix_a')
        a = argparse.Namespace(affinee='auto', calcul='kaggle', moteur='yingmusic')
        voix = self.D.charger_voix_comparees('voix_a,voix_b')
        self.assertEqual(self.D.choisir_affinage(a, {'affinage_pret': True}, voix), {})
        a.affinee = 'oui'
        with self.assertRaises(SystemExit):
            self.D.choisir_affinage(a, {'affinage_pret': True}, voix)


class HorsDeKaggle(Base):
    def setUp(self):
        super().setUp()
        self.voix('voix_a')
        self.affiner('voix_a')

    def test_oui_refuse_proprement_sur_modal_et_runpod(self):
        for calcul in ('modal', 'runpod'):
            with self.subTest(calcul=calcul), self.assertRaises(SystemExit) as e:
                self.choisir(affinee='oui', calcul=calcul)
            self.assertEqual(e.exception.code, "voix affinée : Kaggle seulement pour l'instant (--calcul kaggle, ou --affinee non)")

    def test_auto_garde_les_poids_de_base_sur_modal_et_runpod(self):
        for calcul in ('modal', 'runpod'):
            with self.subTest(calcul=calcul):
                choix, voix, a, texte = self.choisir(pret=True, calcul=calcul)
                self.assertEqual(choix, {})
                self.assertNotIn('affinage', voix)
                self.assertEqual(a.moteur, 'yingmusic')
                self.assertIn('Kaggle seulement', texte)

    def test_calcul_auto_choisit_et_la_session_ailleurs_laisse_le_point(self):
        choix, voix, a, _ = self.choisir(pret=True, calcul='auto')
        self.assertEqual(list(choix), ['voix_a'])
        # session Modal ou Runpod (bascule de calcul auto) : le réglage n'est pas envoyé, la voix reste chez le PC
        r = self.D.reglages_noyau(self.args(a), voix, None, [], kaggle=False)
        self.assertNotIn('affinage', r['voix'])

    def args(self, a):
        return argparse.Namespace(pas=30, paroles=False, moteur=a.moteur, sans_nettoyage=False, mode='auto', octave='auto',
                                  comparer_modeles=False, comparer_pas=None, modele_parole=None, affinee=a.affinee)


class ReglagesEtJeu(Base):
    def setUp(self):
        super().setUp()
        self.voix('voix_a')
        self.voix('voix_b', 210.0)

    def args(self, a):
        return argparse.Namespace(pas=30, paroles=False, moteur=a.moteur, sans_nettoyage=False, mode='auto', octave='auto',
                                  comparer_modeles=False, comparer_pas=None, modele_parole=None, affinee=a.affinee)

    def test_sans_affinage_reglages_comme_avant(self):
        a = argparse.Namespace(pas=30, paroles=False, moteur='yingmusic', sans_nettoyage=False, mode='auto', octave='auto',
                               comparer_modeles=False, comparer_pas=None, modele_parole=None)
        r = self.D.reglages_noyau(a, self.D.charger_voix('voix_a'), None, [])
        self.assertNotIn('affinage', r['voix'])
        self.assertNotIn('affinee', r)
        self.assertEqual(r['moteur_chant'], 'yingmusic')

    def test_une_voix_affinee_envoyee_au_noyau(self):
        self.affiner('voix_a')
        _, voix, a, _ = self.choisir(pret=True)
        r = self.D.reglages_noyau(self.args(a), voix, None, [])
        self.assertEqual(r['voix']['affinage'], {'fichier': 'affinage-voix_a.pth', 'config': 'affinage-voix_a.yml',
                                                 'sha256': hashlib.sha256(OCTETS).hexdigest(), 'pas': 1500, 'complet': True})
        self.assertEqual((r['moteur_chant'], r['affinee']), ('seedvc', 'auto'))
        self.assertNotIn('chemin', json.dumps(r))   # aucun chemin du PC ne part
        json.dumps(r)   # les réglages restent du JSON pur

    def test_deux_voix_seule_la_voix_affinee_a_le_reglage(self):
        self.affiner('voix_b')
        _, voix, a, _ = self.choisir('homme:voix_a,femme:voix_b', pret=True)
        r = self.D.reglages_noyau(self.args(a), voix, None, [])
        par_nom = {v['nom']: v for v in r['voix_cibles']['voix']}
        self.assertNotIn('affinage', par_nom['voix_a'])
        self.assertEqual(par_nom['voix_b']['affinage']['fichier'], 'affinage-voix_b.pth')
        self.assertNotIn('affinage', r['voix'])   # la voix d'analyse (voix_a) n'en a pas

    def test_fichiers_joints_au_jeu_de_donnees(self):
        self.affiner('voix_a')
        _, voix, _, _ = self.choisir(pret=True)
        jeu = self.tmp / 'jeu'
        jeu.mkdir()
        taille = self.D.joindre_affinages(jeu, voix)
        self.assertEqual(sorted(p.name for p in jeu.iterdir()), ['affinage-voix_a.pth', 'affinage-voix_a.yml'])
        self.assertEqual((jeu / 'affinage-voix_a.pth').read_bytes(), OCTETS)
        self.assertEqual(taille, len(OCTETS))

    def test_sans_affinage_rien_n_est_joint(self):
        self.affiner('voix_a')
        _, voix, _, _ = self.choisir(pret=False)
        jeu = self.tmp / 'jeu'
        jeu.mkdir()
        self.assertEqual(self.D.joindre_affinages(jeu, voix), 0)
        self.assertEqual(list(jeu.iterdir()), [])


class ConfigurationEtRapport(Base):
    def test_cles_de_configuration(self):
        self.assertIn('affinee', self.D.CLES_CONFIG)
        self.assertIn('affinage_pret', self.D.CLES_CONFIG)
        self.D.ecrire_config('affinee', 'OUI')
        self.D.ecrire_config('affinage_pret', 'oui')
        conf = self.D.lire_config()
        self.assertEqual((conf['affinee'], conf['affinage_pret']), ('oui', True))
        self.assertEqual(self.D.affinee_config(conf), 'oui')
        self.assertTrue(self.D.affinage_pret_config(conf))
        for cle, valeur in (('affinee', 'peut-etre'), ('affinage_pret', 'bof')):
            with self.subTest(cle=cle), self.assertRaises(SystemExit):
                self.D.ecrire_config(cle, valeur)

    def test_valeurs_par_defaut(self):
        self.assertEqual(self.D.affinee_config({}), 'auto')
        self.assertFalse(self.D.affinage_pret_config({}))
        self.assertFalse(self.D.affinage_pret_config({'affinage_pret': 'bof'}))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.D.affinee_config({'affinee': 'bof'}), 'auto')

    def test_lignes_du_rapport(self):
        rapport = {'affinage': {'voix_a': {'utilise': True, 'pas': 1500, 'raison': None},
                                'voix_b': {'utilise': False, 'pas': 900, 'raison': 'somme SHA-256 différente'}}}
        lignes = self.D.lignes_affinage(rapport)
        self.assertEqual(lignes[0], 'voix voix_a : Seed-VC 44 kHz affiné (1500 pas)')
        self.assertIn('poids de base', lignes[1])
        self.assertIn('somme SHA-256 différente', lignes[1])
        self.assertEqual(self.D.lignes_affinage({}), [])


if __name__ == '__main__':
    unittest.main()
