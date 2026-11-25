"""Lance le service : python -m voixla_web [--config FICHIER]."""
import argparse

import uvicorn

from . import config
from .app import creer


def main():
    p = argparse.ArgumentParser(prog='voixla_web', description='Service web de Voix-là.')
    p.add_argument('--config', help='fichier de configuration (défaut : ~/.config/voix-la-web/config.toml)')
    a = p.parse_args()
    conf = config.charger(a.config)
    uvicorn.run(creer(conf), host=conf.ecoute, port=conf.port, proxy_headers=True, forwarded_allow_ips='127.0.0.1',
                log_level='info', access_log=False)


if __name__ == '__main__':
    main()
