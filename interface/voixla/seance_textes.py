"""Les textes de la séance d'enregistrement (sans GTK) : tout ce qu'il faut pour que n'importe qui suive les étapes sans
rien chercher ailleurs. Ils s'adressent à la personne qui enregistre, sans prénom."""

# --- Comment ça marche (en haut de la séance) ------------------------------------------------------------------------

COMMENT_CA_MARCHE = (
    "Installe ton téléphone : à 20 cm de ta bouche, en mode avion, réglé sur la meilleure qualité d’enregistrement ; "
    "le casque est branché sur le PC.",
    "Lance l’enregistrement sur le téléphone AVANT de lancer la piste, puis arrête-le quand l’étape est finie.",
    "Une étape = un fichier : donne-lui le nom conseillé affiché sur chaque étape (« 1-parole », « 2-tessiture »…).",
    "À la fin, importe tes fichiers avec le bouton en bas de la page : l’étape de chacun est reconnue à son nom.",
    "Compte environ 20 minutes en tout. Tu peux t’arrêter : les cases « Fait » gardent ta place.",
)

# --- Étape 1 : parole ------------------------------------------------------------------------------------------------

CONSIGNE_PAROLE = "Lis à voix haute, à ta vitesse normale, comme si tu racontais à un ami. Si tu te trompes, continue."

TEXTE_PAROLE = (
    "Le matin, quand la ville se réveille, j'aime ouvrir la fenêtre et écouter les bruits de la rue. Un bus passe, un "
    "chien aboie, quelqu'un rit sur le trottoir d'en face. Je prépare un café bien chaud, je coupe une tranche de pain "
    "et je regarde le ciel : va-t-il pleuvoir, ou fera-t-il beau ?",

    "Hier, mon voisin m'a raconté une histoire incroyable. Il avait perdu ses clés au marché, entre les oranges et les "
    "poivrons. Il a cherché partout pendant une heure ! Finalement, c'est une petite fille de six ans qui les a "
    "retrouvées, cachées sous une caisse de tomates. Il était si content qu'il lui a offert un énorme bouquet de "
    "fleurs jaunes.",

    "Est-ce que tu connais ce sentiment ? Quand on croit avoir tout perdu, et que tout s'arrange au dernier moment… "
    "C'est rare, mais c'est magique.",

    "Ce week-end, je voudrais partir au bord de la mer. Marcher pieds nus sur le sable, sentir le vent salé, compter "
    "les vagues : une, deux, trois, quatre, cinq… Puis rentrer le soir, fatigué mais heureux, et chanter à tue-tête "
    "dans la voiture.",

    "Voilà. Merci de m'avoir écouté ! À bientôt, et surtout, prends soin de toi.",
)

TITRE_LIBRE = "Parle librement (1 min)"
CONSIGNE_LIBRE = "Choisis un sujet, lance-toi sans préparer : parle une minute, naturellement, sans t’arrêter."
SUJETS_LIBRES = (
    "Raconte la meilleure journée de ton année.",
    "Décris ton plat préféré et comment on le prépare.",
    "Explique à quelqu'un comment venir chez toi.",
)

# --- Étape 2 : tessiture ---------------------------------------------------------------------------------------------

CONSIGNES_TESSITURE = (
    "Imite le son avec ta voix : monte doucement du plus grave au plus aigu, puis redescends. Ne force jamais : "
    "arrête-toi avant que ça coince.",
    "La piste joue d’abord 6 sirènes : trois fois sur « aaa », puis trois fois sur « ou », avec 3 secondes de silence "
    "entre chacune.",
    "Elle joue ensuite une gamme, deux fois : le nom de la note en cours s’affiche en grand ; chante-la sur « la », puis "
    "sur « mi ».",
)
CHOIX_VOIX = (("grave", "Voix grave", 190.0), ("aigue", "Voix aiguë", 380.0))
EXPLICATION_CENTRE = (
    "La piste est réglée sur ta voix : {source}. Centre de ta voix : {hz} Hz.",
    "Pour régler la piste sur ta voix, choisis ce qui te ressemble le plus :",
)

# --- Étape 5 : chanson libre -----------------------------------------------------------------------------------------

CONSIGNES_CHANSON_PUBLIQUE = (
    "Deux chansons très connues, que tout le monde peut chanter : choisis-en une (ou les deux, un fichier chacune).",
    "Lance l’enregistrement sur le téléphone, puis la piste « accompagnement seul » : 4 bips, tape dans tes mains sur "
    "le 4e, et chante en suivant les paroles à l’écran.",
    "Avant, tu peux t’entraîner avec la piste « avec la mélodie », qui joue aussi la mélodie au piano.",
    "Les chansons sont placées dans la hauteur de ta voix : tu n’as rien à régler.",
)
CONSIGNES_CHANSON_DOUBLAGE = (
    "Chante la chanson d’un de tes doublages, comme à l’étape 4 : choisis le doublage et la tonalité, puis fais la "
    "prise sur la musique seule.",
)
