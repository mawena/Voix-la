from voixla_web.app import NOM_VOIX, nom_voix


def test_nom_voix_normalise_comme_doubler():
    assert nom_voix(' Charles-Gamligo ') == 'charles-gamligo'
    assert nom_voix('Élodie B') == 'elodie-b'
    assert NOM_VOIX.match(nom_voix('Élodie B'))
    assert not NOM_VOIX.match(nom_voix('a'))
    assert not NOM_VOIX.match(nom_voix('-x'))
