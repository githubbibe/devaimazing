"""
Citations piochées au hasard comme indicateur "en cours de traitement" côté
bot Telegram (ADR 0013, Décision 1 — pas d'animation personnalisée, message
texte explicite édité en place une fois le résultat prêt) — sur le modèle de
l'animation de Claude Code CLI pendant qu'il travaille sur une demande.

Copie restructurée (liste Python propre, un élément par citation) du
contenu original de citations.txt (racine du dépôt, fichier personnel de
l'utilisateur, non tracké par git, non touché par ce module) — demande
explicite de l'utilisateur (2026-08-05) : « ce fichier n'est pas structuré
comme il faut, tu feras une copie de son contenu au bon endroit ».
"""

import random

CITATIONS: list[str] = [
    """Mais, vous savez, moi je ne crois pas qu'il y ait de bonne ou de mauvaise situation. Moi, si je devais résumer ma vie aujourd'hui avec vous, je dirais que c'est d'abord des rencontres. Des gens qui m'ont tendu la main, peut-être à un moment où je ne pouvais pas, où j'étais seul chez moi. Et c'est assez curieux de se dire que les meandres, les hasards fabriquent les destinées... Parce que quand on a le goût de la chose, quand on a le goût de la chose bien faite, le beau geste, parfois on ne trouve pas l'interlocuteur en face je dirais, le miroir qui vous aide à avancer. Alors ça n'est pas mon cas, comme je disais là, puisque moi au contraire, j'ai pu : et je dis merci à la vie, je lui dis merci, je chante la vie, je danse la vie... je ne suis qu'amour ! Et finalement, quand beaucoup de gens aujourd'hui me disent : "Mais comment fais-tu pour avoir cette philosophie ?" Eh bien, je leur réponds tout simplement, je leur dis c'est ce goût de l'amour, ce goût qui m'a poussé aujourd'hui à entreprendre une construction navale, mais demain qui sait ? Peut-être simplement à me mettre au service de la communauté, à faire le don, le don de soi...""",
    """Il s'appelle Juste Leblanc, votre prénom c'est François, c'est juste ? Eh bien lui, c'est pareil, c'est Juste.""",
    """J'ai connu une Polonaise qui en prenait au petit-déjeuner... Faut quand même admettre que c'est plutôt une boisson d'homme.""",
    """Non mais j'vais lui montrer qui c'est Raoul ! Aux quatre coins d'Paris qu'on va l'retrouver éparpillé par petits bouts, façon puzzle. Moi, quand on m'en fait trop, j'correctionne plus : j'dynamite, j'disperse, j'ventile !""",
    """You talkin' to me?""",
    """La première règle du Fight Club est : il est interdit de parler du Fight Club.""",
    """Thank you Mario! But our princess is in another castle!""",
    """I am the one who knocks!""",
    """That's what she said!""",
    """Ah mais moi, je suis un fou ! Je suis un grand malade, moi ! Je brûle ma ferme, j'y fous le feu ! Je brûle sa ferme à lui ! J'y fous le feu aussi ! Je brûle le château ! Je tue les vaches ! J'égorge les poules ! Je pisse dans le puits !""",
    """Dracarys.""",
    """Never forget what you are. The rest of the world will not. Wear it like armor, and it can never be used to hurt you.""",
    """Pétrichor
Nom masculin, pluriel : pétrichors
Définition : Odeur particulière et agréable que prend la terre desséchée après la pluie.
Étymologie : Créé en 1964 par les chercheurs I. J. Bear et R. G. Thomas. Du grec pétra (pierre) et îchôr (le sang des dieux dans la mythologie grecque).
Exemple : L'odeur intense du pétrichor s'élevait du sol asphalté juste après la première averse de l'été. (L'Épopée de la pluie, Julien Green, 1994).""",
    """Agélaste
Nom masculin ou féminin (ou adjectif), pluriel : agélastes
Définition : Personne réfractaire à l'humour, qui ne rit jamais.
Étymologie : Popularisé par François Rabelais. Du grec agelastos (sans rire, triste), composé de a- (privatif) et gelaô (rire).
Exemple : Impossible de décocher un sourire à cet agélaste, même avec la meilleure des plaisanteries. (Gargantua, François Rabelais, 1534).""",
    """Psithurisme
Nom masculin, pluriel : psithurismes
Définition : Bruit doux du vent dans les feuilles des arbres.
Étymologie : Issu du grec psithurismos (chuchotement, murmure), dérivé du verbe onomatopéique psithuridzô (chuchoter).
Exemple : Le doux psithurisme de la forêt apaisait les promeneurs à mesure qu'ils s'enfonçaient sous la frondaison. (Les Solitudes, Théophile de Viau, 1621).""",
    """Baguenauder
Verbe intransitif (premier groupe)
Définition : Flâner en s'attardant à des futilités, passer son temps à des bêtises.
Étymologie : Dérivé de baguenaude, fruit du baguenaudier, une gousse gonflée d'air qui claque quand on la crève.
Exemple : Plutôt que de travailler à son dossier, il préférait baguenauder le long des quais toute l'après-midi. (Mémoires d'outre-tombe, François-René de Chateaubriand, 1848).""",
    """Coquecigrue
Nom féminin, pluriel : coquecigrues
Définition : Animal imaginaire burlesque, illusion, ou sornette/ineptie ("conter des coquecigrues").
Étymologie : Mot composé créé au XVIe siècle par croisement entre coq, ciguë et grue.
Exemple : Ne l'écoute pas, il ne raconte que des coquecigrues pour captiver son auditoire. (Le Tiers Livre, François Rabelais, 1546).""",
    """Chattemite
Nom féminin, pluriel : chattemites
Définition : Personne hypocrite qui affecte des manières douces et doucereuses pour tromper son monde.
Étymologie : Croisement de la douceur apparente du chat et du verbe de l'ancien français miter (caresser, faire le doux).
Exemple : Derrière ses airs de chattemite, elle distribuait des coups bas à tous ses collègues de bureau. (Fables, "Le Chat, la Belette et le Petit Lapin", Jean de La Fontaine, 1668).""",
    """Calembredaine
Nom féminin (souvent employé au pluriel), pluriel : calembredaines
Définition : Propos extravagant, plaisanterie frivole, bêtise.
Étymologie : Altération croisée au XVIIIe siècle entre calembour et bourdaine (action de conter des sornettes).
Exemple : Cesse de débiter des calembredaines et concentre-toi enfin sur des sujets sérieux. (Le Père Goriot, Honoré de Balzac, 1835).""",
    """Niquedouille
Nom masculin ou féminin (ou adjectif), pluriel : niquedouilles
Définition : Personne niaise, sotte, bêta.
Étymologie : Altération de nique (geste de mépris) ou du prénom Nicodème combiné à douille (mou, benêt).
Exemple : Ce pauvre niquedouille s'est encore fait avoir par une arnaque téléphonique évidente. (Les Joyeuses Commères de Paris, Paul de Kock, 1838).""",
    """Cunctateur
Nom masculin (féminin : cunctatrice), pluriel : cunctateurs
Définition : Personne qui temporise, temporisateur qui hésite à agir.
Étymologie : Du latin cunctator (celui qui retarde), surnom donné au général romain Quintus Fabius Maximus.
Exemple : Son profil de cunctateur lui a fait manquer de très belles opportunités d'investissement. (Histoire romaine, Tite-Live, Ier siècle av. J.-C.).""",
    """Grenouiller
Verbe intransitif (premier groupe)
Définition : Intriguer dans l'ombre, s'adonner à des manœuvres sournoises ou des trafics d'influence.
Étymologie : Dérivé de grenouille. Évoque le comportement des grenouilles s'agitant dans les eaux troubles.
Exemple : Il a passé sa carrière à grenouiller dans les couloirs du ministère pour obtenir une promotion. (Journal, Léon Daudet, 1917).""",
    """Noctiluque
Nom féminin ou adjectif, pluriel : noctiluques
Définition : Qui brille dans la nuit (ou organisme marin bioluminescent).
Étymologie : Du latin noctiluca (qui brille la nuit), formé de nox, noctis (nuit) et lucere (briller).
Exemple : Les noctiluques illuminaient la mer d'un éclat bleuté à chaque passage des vagues. (Vingt mille lieues sous les mers, Jules Verne, 1870).""",
    """Emberlucoquer
Verbe transitif (principalement pronominal : s'emberlucoquer)
Définition : S'enticher ou s'éprendre d'une idée fausse ou d'une personne avec obstination.
Étymologie : Formé sur l'ancien français berlucoque (caprice, idée folle), lié à berlue (vision altérée).
Exemple : Il s'est emberlucoqué d'une théorie absurde qu'il défend désormais envers et contre tous. (L'Avare, Molière, 1668).""",
    """Pellucide
Adjectif qualificatif, pluriel : pellucides
Définition : Qui laisse passer la lumière, translucide ou presque transparent.
Étymologie : Du latin pellucidus, variante de perlucidus (très éclairé, transparent), du verbe perlucere (traverser en brillant).
Exemple : La membrane pellucide de l'ovocyte joue un rôle crucial lors du processus de fécondation. (Traité d'anatomie comparée, Georges Cuvier, 1805).""",
    """Nycthémère
Nom masculin, pluriel : nycthémères
Définition : Unité de temps correspondant à une durée de 24 heures (comprenant un jour et une nuit).
Étymologie : Du grec nukthēmeron, composé de nux, nuktos (nuit) et hēmera (jour).
Exemple : L'étude du rythme biologique s'effectue généralement sur la durée précise d'un nycthémère. (Introduction à l'étude de la médecine expérimentale, Claude Bernard, 1865).""",
    """Aglet
Nom masculin, pluriel : aglets
Définition : Embout de plastique ou de métal situé à l'extrémité d'un lacet.
Étymologie : Issu du moyen français aiguillette (petite aiguille), diminutif d'aigue (pointe).
Exemple : L'aglet en plastique de son lacet s'est cassé, rendant la chaussure très difficile à enfiler. (Dictionnaire encyclopédique de l'industrie, Eugène-Oscar Lami, 1881).""",
    """Abstème
Nom masculin ou féminin (ou adjectif), pluriel : abstèmes
Définition : Personne qui s'abstient de toute boisson alcoolisée.
Étymologie : Du latin abstemius, composé de abs- (privatif) et de temetum (boisson enivrante, vin).
Exemple : En tant qu'abstème convaincu, il commande systématiquement de l'eau pétillante lors des pots de départ. (Essais, Michel de Montaigne, 1580).""",
    """Enchifrener
Verbe transitif (premier groupe)
Définition : Embarrasser le nez de quelqu'un, le rhumer au point de lui rendre la respiration difficile.
Étymologie : De l'ancien français chifrene (poussière, catarrhe), issu d'une racine pré-latine désignant ce qui bouche.
Exemple : Ce courant d'air froid a suffi à l'enchifrener pour tout le reste de la semaine. (Madame Bovary, Gustave Flaubert, 1857).""",
    """Il y a une théorie qui dit que si quelqu'un découvrait exactement à quoi sert l'Univers et pourquoi il est là, il disparaîtrait sur-le-champ pour être remplacé par quelque chose d'encore plus bizarre et inexplicable. Une autre théorie dit que cela s'est déjà produit.""",
    """La réponse à la Grande Question sur la Vie, l'Univers et le Reste est… Quarante-deux.""",
    """Je sais. C'est écrit dans les bandes cérébrales d'Arthur. Je peux la lire si vous voulez. [...] Ça ne vous plaira pas. [...] C'est imprimé dans ses ondes cérébrales. Mais ça ne vous servira à rien. Vous êtes déjà en train de chercher autre chose.""",
    """J'ai parlé au calculateur de bord. Il m'hait.""",
    """La première dix-millionième d'année a été la pire. La deuxième dix-millionième d'année, elle aussi a été la pire. La troisième dix-millionième d'année, je ne l'ai pas aimée du tout.""",
    """Le risque est notre métier. C'est pour cela que nous sommes à bord de ce vaisseau.""",
    """L'espace, la frontière infinie. Voici les voyages du vaisseau enterprise. Sa mission de cinq ans : explorer de nouveaux mondes étranges, découvrir de nouvelles vies, d'autres civilisations, et au mépris du danger, avancer vers l'inconnu.""",
    """Une fois l'impossible éliminé, ce qui reste, si improbable soit-il, doit être la vérité.""",
    """C'est illogique.""",
    """M. Sulu. Sortez-nous de là.""",
]


def pick_citation() -> str:
    """Pioche une citation au hasard (CITATIONS) — accusé de réception
    "en cours de traitement" côté bot Telegram, voir docstring module."""
    return random.choice(CITATIONS)
