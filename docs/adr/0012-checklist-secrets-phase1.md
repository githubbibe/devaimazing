# ADR 0012 - Checklist sécurité et gestion des secrets en Phase 1

**Date** : 2026-07
**Statut** : Accepté

## Contexte

Le repo documente déjà (ADR 0008) une checklist d'intention produit, animée par le PM
en phase 1. Rien d'équivalent n'existe côté sécurité des secrets (mots de passe admin,
certificats, clés API, tout secret d'un projet cible) : ni contrainte légale, ni
exigence sponsor, ni niveau de gestion des secrets ne sont posés au cadrage. Le sujet
n'apparaît nulle part avant la production de code.

Une piste initiale envisageait de confier ce sujet à l'agent Sécu, en audit après le
brief Architecte (phase 2) ou après implémentation (phase 8). C'est un mauvais
séquencement : la sécurité des secrets n'est pas une propriété technique vérifiable
après coup sur une architecture déjà figée, c'est une exigence produit et légale qui
doit être posée avant que l'architecture ne soit conçue. Une fois le brief Architecte
produit (phase 2), le niveau de sécurité est déjà implicitement contraint par les choix
faits. C'est la même mécanique de cascade que l'ADR 0008 (l'erreur la plus coûteuse naît
à la racine du run, pas en audit aval) — mais côté conformité légale/contractuelle
plutôt que côté intention produit.

## Décision

La Phase 1 intègre une seconde checklist, distincte de la checklist d'intention de
l'ADR 0008, animée par le même agent (le PM) au même moment (dialogue de cadrage). Les
deux checklists ne fusionnent pas : la checklist d'intention porte sur le contrôle
utilisateur par dimension produit (trois questions structurelles, applicables à
n'importe quelle dimension) ; la checklist sécurité porte sur des contraintes légales et
des exigences de sponsor (des questions factuelles, pas structurelles). Les mélanger
ferait perdre la lisibilité des deux mécanismes.

**Contenu de la checklist sécurité**, posée par le PM lors du dialogue de cadrage (le PM
reformule dans ses mots si besoin, mais le fond couvre ces points) :

1. Une contrainte légale s'applique-t-elle à ce projet (RGPD, secteur réglementé,
   obligations contractuelles spécifiques) ? Si oui, laquelle précisément ?
2. Le sponsor ou client a-t-il une exigence de sécurité au-delà du minimum légal ?
   Si oui, laquelle ?
3. Cette exigence implique-t-elle un niveau de gestion des secrets particulier
   (rotation automatisée, chiffrement au repos, séparation stricte des environnements
   dev/staging/prod, audit d'accès aux secrets) ?
4. Si aucune contrainte légale ni exigence sponsor n'est identifiée, le **niveau par
   défaut** s'applique (voir ci-dessous).

**Niveau de sécurité par défaut** (en l'absence de toute contrainte légale ou exigence
sponsor identifiée) : les secrets ne sont jamais commités en clair dans le repo cible
(pas de mot de passe, clé API ou certificat en dur ni dans un fichier `.env` versionné)
et sont gérés via un outil tiers de gestion de secrets plutôt qu'un fichier `.env` en
clair à la racine du projet. Ce niveau par défaut est documenté ici, dans cet ADR, pour
ne pas être laissé à la réinvention du PM à chaque run.

**Le choix concret de l'outil tiers (SOPS+age, Infisical self-hosted, ou autre) n'est
pas une décision d'architecture générale de devaimazing** — c'est une décision prise au
cas par cas, projet par projet, en fonction de la contrainte de sécurité identifiée en
phase 1 pour ce projet précis. Cet ADR ne fige aucun outil unique et obligatoire.
L'Architecte note l'outil retenu et sa justification dans `architect-map.md` du projet
concerné (section Sécurité), pas dans un fichier de devaimazing lui-même.

**Où atterrit la réponse** : la réponse à cette checklist est une contrainte
non-fonctionnelle inscrite dans `card-root.md` (fiche racine, fin de phase 1), dans une
sous-section dédiée de la section « Contraintes non-fonctionnelles » existante — pas un
mécanisme séparé de cette section, juste une structuration plus fine que le champ
« Sécurité : » à une ligne qui y existait jusqu'ici.

**Respect en aval** : cette contrainte, une fois inscrite dans la fiche racine, est
respectée par l'Architecte lors de la conception du brief (phase 2) — pas redécouverte
ni redéfinie par lui, au même titre que les contraintes issues de la checklist
d'intention (ADR 0008). Le skill `skills/secrets-management.md` encode les patterns
d'implémentation attendus (pas de secret en dur, chargement via variables
d'environnement ou gestionnaire de secrets, `.env.example` sans valeur réelle versionné
à la place d'un `.env` réel) et rejoint le corpus consulté par les agents producteurs
(Back, Front) et par l'Architecte en audit, sur le modèle de `data-privacy.md` (ADR
0009).

**Rôle de l'agent Sécu, précision importante** : l'agent Sécu **audite la conformité** à
l'exigence de sécurité posée en phase 1 et déclinée en phase 2 — il ne définit, ne
choisit et ne propose aucune politique de sécurité lui-même. Un écart constaté (par
exemple des secrets en clair alors que la contrainte imposait un outil tiers) est traité
comme n'importe quel autre écart qu'il détecte : annotation de la fiche de l'agent
fautif, remontée au PM. Ce n'est pas à lui de choisir la politique après coup.

## Raisons

1. **Allocation par la cascade, même mécanique que l'ADR 0008** : poser la contrainte de
   sécurité en phase 1 coûte une question ; la découvrir non respectée après
   implémentation (phase 8) coûte une refonte d'architecture, puisque la gestion des
   secrets est structurante pour les choix faits en phase 2 (schéma de configuration,
   séparation des environnements, découpage des modules).
2. **Distinction des deux checklists** : la checklist d'intention (ADR 0008) répond à
   « qui contrôle quoi », une question de granularité produit. La checklist sécurité
   répond à « quelle contrainte externe s'impose », une question factuelle de conformité
   légale/contractuelle. Ce sont deux natures de question différentes ; les fusionner
   masquerait cette différence dans le dialogue de cadrage.
3. **Auditeur ne définit pas la politique** : le principe 4 d'ARCHITECTURE.md (l'auditeur
   doit dominer le producteur) suppose que l'auditeur audite une norme déjà posée, pas
   qu'il l'invente lui-même après coup — sinon l'agent Sécu deviendrait juge et partie
   sur une politique qu'il aurait choisie a posteriori, sans checkpoint humain en amont.

## Conséquences

- `prompts/pm.md` gagne une section « Checklist sécurité et gestion des secrets »,
  distincte de la checklist d'intention, dans les responsabilités de phase 1.
- `docs/workflow.md` (phase 1) documente ce second mécanisme aux côtés de la checklist
  d'intention.
- `ARCHITECTURE.md` gagne un principe 11 symétrique aux principes 8 (intention) et 9
  (pseudonymisation) côté sécurité des secrets.
- `templates/card-root.md.template` gagne une sous-section structurée « Sécurité et
  gestion des secrets » sous « Contraintes non-fonctionnelles ».
- `templates/architect-map.md.template`, section « Sécurité » (déjà présente mais
  vide), gagne des champs structurés : contrainte héritée de la phase 1, outil retenu
  pour le projet, justification, dette assumée.
- `prompts/architect.md` (phase 2) est mis à jour pour que l'Architecte reprenne
  explicitement la contrainte de sécurité de `card-root.md`, applique le skill
  `secrets-management.md`, et la décline par agent dans le brief — sans la redéfinir.
- `prompts/security.md` est mis à jour pour clarifier que l'agent Sécu audite une
  conformité déjà définie en amont, il ne choisit ni ne recommande de solution de
  gestion des secrets lui-même.
- `skills/secrets-management.md` est créé, sur le modèle de `data-privacy.md`, et
  référencé dans `README.md` (arborescence `skills/`).
- `templates/card-agent.md.template`, champ « Sécurité » (déjà présent), est renommé
  « Sécurité et gestion des secrets » et référence désormais explicitement le skill,
  sur le modèle des champs Gestion d'erreurs/Logging/Résilience déjà présents.

## Alternatives rejetées

- **Audit de la gestion des secrets par l'agent Sécu après le brief Architecte (phase 2)
  ou après implémentation (phase 8)** : erreur de séquencement identifiée comme point de
  départ de cette réflexion — arrive après que l'architecture ait déjà implicitement
  contraint le niveau de sécurité possible, donc trop tard pour être autre chose qu'un
  audit de conformité coûteux à corriger.
- **Fusionner la checklist sécurité dans la checklist d'intention (ADR 0008)** :
  masquerait la différence de nature entre les deux (contrôle utilisateur par dimension
  produit vs. contrainte légale/contractuelle externe), et complexifierait une checklist
  déjà éprouvée sans bénéfice clair.
- **Figer un outil tiers unique de gestion des secrets pour tous les projets** : le choix
  dépend de la contrainte réelle du projet (contexte réglementaire, taille d'équipe,
  infrastructure déjà en place) ; un choix unique et obligatoire au niveau devaimazing
  serait soit trop lourd pour un POC sans contrainte légale, soit insuffisant pour un
  projet réglementé.
