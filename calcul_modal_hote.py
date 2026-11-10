"""Application Modal de Voix-là : lancée par calcul_modal.py avec le Python du client modal (~/modal-venv), jamais par le
Python du système (le module « modal » n'y est pas).

  <python modal> calcul_modal_hote.py <spec.json>

Application ÉPHÉMÈRE (« with app.run() ») : elle s'arrête quand ce processus s'arrête, et le calcul avec elle ; rien ne
reste ouvert (donc pas de facturation oubliée). Le même script doubler_kaggle.py tourne dans le conteneur, avec
DOUBLER_ENTREES et DOUBLER_SORTIE. Deux volumes :
- « voix-la-modeles » (persistant, PUBLIC seulement) : les outils préparés (wheels, Seed-VC, poids des modèles publics),
  monté sur /entrees/outils où le noyau les trouve, et HF_HOME. Jamais la référence d'une voix ni un son ;
- un volume ÉPHÉMÈRE (modal.Volume.ephemeral), supprimé par Modal à la fin : le son, la référence et le noyau y sont
  envoyés pour le calcul, puis effacés (aussi du conteneur, avant la fin).
Les sorties reviennent par le flux de la fonction (morceaux de 1 Mio), jamais par un volume.
Protocole sur la sortie standard : « @@ETAT <mot> » (application, demarrage, en_cours, fini), puis les lignes du noyau.
"""
import json
import os
import sys
import time

import modal

# Bibliothèques : les mêmes que le noyau Kaggle (calcul.py ; un test vérifie que les deux listes sont identiques)
PYTHON_NOYAU = '3.13'
PAQUETS = ['torch==2.11.0', 'torchaudio==2.11.0', 'numpy==2.1.3', 'librosa==0.11.0', 'transformers==5.20.2',
           'huggingface_hub==1.33.0', 'soundfile==0.14.0', 'scipy', 'einops', 'pyyaml', 'tqdm', 'matplotlib', 'munch']
INDEX_TORCH = 'https://download.pytorch.org/whl/cu128'
NOM_APP = 'voix-la'
NOM_VOLUME_MODELES = os.environ.get('VOIXLA_MODAL_VOLUME') or 'voix-la-modeles'
MORCEAU = 1024 * 1024

app = modal.App(NOM_APP)
image = (modal.Image.debian_slim(python_version=PYTHON_NOYAU)
         .apt_install('ffmpeg', 'git', 'libsndfile1')
         .pip_install(*PAQUETS, extra_index_url=INDEX_TORCH)
         .env({'HF_HOME': '/entrees/outils/hf', 'PYTHONUNBUFFERED': '1'}))
MODELES = modal.Volume.from_name(NOM_VOLUME_MODELES, create_if_missing=True)


@app.cls(image=image, gpu='L4', timeout=3600, volumes={'/entrees/outils': MODELES})
class Doublage:
    @modal.method()
    def lancer(self, spec):
        """Dans le conteneur : range les entrées, lance le noyau, efface le son, renvoie les sorties par morceaux.
        Rend un générateur de ('fichier', chemin relatif, octets) puis ('fin', {code, ...})."""
        import shutil
        import subprocess
        from pathlib import Path
        racine = os.environ.get('VOIXLA_MODAL_RACINE', '')   # essais seulement : vide dans un vrai conteneur
        ech, ent, sor = (Path(racine + spec[k]) for k in ('echange', 'entrees', 'sortie'))
        outils = spec['mode'] == 'outils'
        print('@@ETAT en_cours', flush=True)
        ent.mkdir(parents=True, exist_ok=True)
        jeu = ech / 'jeu'
        if jeu.is_dir():
            for f in jeu.iterdir():
                shutil.copy2(f, ent / f.name)
        sor_noyau = ent / 'outils' if outils else sor   # les outils se préparent dans le volume des modèles publics
        sor_noyau.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, 'DOUBLER_ENTREES': str(ent), 'DOUBLER_SORTIE': str(sor_noyau),
               'DOUBLER_DEBUT': str(time.time()), 'PYTHONUNBUFFERED': '1'}
        t0 = time.time()
        try:
            code = subprocess.run([sys.executable, str(ech / 'noyau.py')], env=env, cwd=str(ent.parent)).returncode
        finally:
            for f in ent.iterdir():   # le son et les références ne restent pas dans le conteneur
                if f.is_file():
                    f.unlink()
            shutil.rmtree(jeu, ignore_errors=True)
            (ech / 'noyau.py').unlink(missing_ok=True)
        if outils:
            try:
                MODELES.commit()   # les outils (publics) restent dans le volume des modèles
            except Exception as e:  # noqa: BLE001
                print(f'MODAL commit du volume des modèles : {e!r}', flush=True)
            a_rendre = [(sor_noyau / 'outils.json', 'outils.json')] if (sor_noyau / 'outils.json').is_file() else []
        else:
            a_rendre = [(f, str(f.relative_to(sor))) for f in sorted(sor.rglob('*')) if f.is_file()]
        for f, rel in a_rendre:
            with open(f, 'rb') as fh:
                while True:
                    bloc = fh.read(MORCEAU)
                    if not bloc:
                        break
                    yield ('fichier', rel, bloc)
            if not outils:
                f.unlink()
        yield ('fin', {'code': code, 'duree_noyau_s': round(time.time() - t0, 1)})


def client(spec):
    """Dans le client : envoie le son dans un volume éphémère, lance, reçoit les sorties dans spec['retour']."""
    from pathlib import Path
    retour = Path(spec['retour'])
    retour.mkdir(parents=True, exist_ok=True)
    gpu = spec['gpu']
    print('@@ETAT application', flush=True)
    with modal.enable_output():
        with app.run():
            with modal.Volume.ephemeral() as echange:
                with echange.batch_upload() as lot:
                    lot.put_file(spec['script'], '/noyau.py')
                    for f in sorted(Path(spec['jeu']).glob('*')) if spec.get('jeu') else []:
                        lot.put_file(str(f), f'/jeu/{f.name}')
                print('@@ETAT demarrage', flush=True)
                machine = Doublage.with_options(gpu=gpu, timeout=int(spec['timeout_s']),
                                                volumes={'/echange': echange, '/entrees/outils': MODELES})()
                fin, ouvert = None, {}
                try:
                    for genre, *reste in machine.lancer.remote_gen(
                            {'echange': '/echange', 'entrees': '/entrees', 'sortie': '/sortie', 'mode': spec['mode']}):
                        if genre == 'fichier':
                            rel, bloc = reste
                            cible = retour / rel
                            if cible not in ouvert:
                                cible.parent.mkdir(parents=True, exist_ok=True)
                                ouvert[cible] = open(cible, 'wb')
                            ouvert[cible].write(bloc)
                        elif genre == 'fin':
                            fin = reste[0]
                finally:
                    for fh in ouvert.values():
                        fh.close()
            # sortie du « with » : le volume éphémère est supprimé par Modal, puis l'application s'arrête
    print('@@ETAT fini', flush=True)
    with open(retour / 'modal.json', 'w') as f:
        json.dump(fin or {}, f)
    return 0 if fin is not None else 2


if __name__ == '__main__':
    with open(sys.argv[1]) as f:
        sys.exit(client(json.load(f)))
