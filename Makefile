PYTHON ?= python3

.PHONY: test unit cli deb clean

test: unit cli

# Tests de l'interface (logique sans écran, faux doubler, sans réseau)
unit:
	cd interface && $(PYTHON) -m unittest discover -s tests -t .
	$(PYTHON) -m unittest discover -s tests

# Essai rapide de la commande doubler, directement et par son lanceur
cli:
	$(PYTHON) doubler.py --help >/dev/null
	$(PYTHON) bin/doubler --help >/dev/null
	$(PYTHON) bin/doubler --version

deb:
	bash scripts/build-deb.sh

clean:
	rm -rf dist build debian/voix-la debian/.debhelper debian/files debian/*.substvars debian/*.debhelper debian/*.debhelper.log debian/debhelper-build-stamp
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
