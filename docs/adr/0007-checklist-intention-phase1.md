# ADR 0007 - Checklist d'intention produit en Phase 1

**Date** : 2026-06
**Statut** : Accepté

## Contexte

L'erreur la plus coûteuse de webaimazing V1 n'était pas une erreur de code mais une erreur de cadrage d'intention : la génération de contenu était 100 % automatique, sans choix laissé à l'utilisateur final. Détectée trop tard, elle a imposé un choix entre développer un CMS ad-hoc ou refondre le projet. Le code faisait correctement ce qu'on lui demandait : le défaut était dans la consigne, pas dans l'exécution.

Cette classe d'erreur a trois propriétés qui la rendent dangereuse :

1. Aucun test ne l'attrape (le code est conforme à une intention fausse).
2. Aucun audit de modèle ne la détecte de façon fiable : un modèle ne voit pas la dette d'intention qu'il a lui-même laissée passer au cadrage, par construction (s'il la voyait, il ne l'aurait pas laissée).
3. Elle est à la racine du run, donc sa cascade est totale : tout l'arbre en aval se construit sur une base fausse.

Le fonctionnement en raffinement successif aggrave le risque : il développe une intention mais ne la rejuge pas. Plus le raffinement avance, plus le retour à la racine coûte cher. C'est la mécanique qui a mené au CMS.

## Décision

La Phase 1 intègre une checklist d'intention produit, animée par le PM en casquette product owner (Opus), validée au checkpoint humain de fin de Phase 1.

La checklist raisonne par dimension de contrôle. Pour chaque dimension du produit cible, elle force trois questions :

1. Cette dimension existe-t-elle comme axe de contrôle distinct ?
2. L'utilisateur final (le client qui paie) peut-il prendre ou déléguer le contrôle sur cette dimension, indépendamment des autres ?
3. Ce choix est-il explicite (l'utilisateur décide) ou implicite (le système décide par défaut) ?

**Garde-fou** : toute dimension où le système décide par défaut sans choix explicite est marquée comme dette d'intention en puissance et remonte au checkpoint humain.

**Interdiction** : le PO agent ne comble jamais un trou d'intention par une valeur par défaut « raisonnable ». Un trou remonte à l'humain, il n'est pas rempli par l'agent.

La déclinaison produit de ce principe (niveaux de contrôle, arbre du tunnel, règle « un conseil n'est jamais une pré-sélection ») est une spécification du produit cible, hors périmètre de cet ADR. Pour webaimazing, elle vit dans la fiche produit (Partie 2.1, le tunnel d'onboarding).

## Raisons

1. **Allocation par la cascade** : la pire erreur historique naît à la racine (Phase 1). Le token de capacité supérieure le plus rentable du run est au cadrage, pas en audit aval. Détecter l'erreur d'intention en Phase 1 coûte une question ; la détecter après implémentation coûte une refonte.

2. **Mécanisme plutôt que perspicacité** : une checklist bat un auditeur brillant sur cette classe d'erreur, parce qu'elle force la question indépendamment de l'inspiration du jour. C'est le pendant de la non-régression : une mémoire externe qui ne flanche pas quand l'attention flanche.

3. **Contrôle par dimension, pas binaire** : la dette V1 venait d'une granularité de contrôle binaire (tout auto ou tout manuel). Le découplage par dimension permet à l'utilisateur de garder une dimension et d'en déléguer une autre. « Mettre le client au cœur » signifie lui donner le choix par dimension, pas lui donner le travail.

4. **Choisi contre subi** : la dette ne vient pas de l'automatisation, elle vient de l'automatisation non choisie. Une dimension déléguée explicitement est un service ; la même dimension automatisée sans choix est une dépossession. La ligne de partage est choisi/subi, pas auto/manuel. C'est la philosophie « dépendances contrôlées, pas de boîte noire subie » appliquée à l'utilisateur final.

5. **Représenter l'absent** : ni l'humain qui pilote le run, ni le PO agent ne sont l'utilisateur final. Rien dans la boucle Phase 1 ne force naturellement à poser sa question. La checklist représente cet absent.

## Conséquences

- Le PO agent (Opus) anime la checklist : il pose les questions, creuse, oblige à expliciter, signale les incohérences internes. Il ne répond pas aux questions d'intention à la place de l'humain.
- Le checkpoint humain de fin de Phase 1 valide les réponses de l'humain, pas les hypothèses de l'agent.
- Les dimensions de contrôle identifiées en Phase 1 alimentent la spécification du tunnel du produit cible (quelles dimensions exposer, à quelle granularité, où le système conseille sans décider). La checklist (interne devaimazing) nourrit la spec produit (repo produit). Une checklist bâclée reproduit la V1 : un tunnel sans choix, parce que personne n'a décidé au cadrage que ces choix existaient.
- La checklist est un template réutilisable instancié par produit (livrable distinct à formaliser dans `templates/`).
- Surcoût Opus en Phase 1 assumé : endroit le plus rentable du run, cohérent avec l'ADR 0006 (Opus pour la réflexion haute).

## Alternatives rejetées

- **Audit d'intention par un agent en aval (Architecte ou Sécu, Qwen)** : un agent au même plafond que le producteur ne voit pas la dette de capacité, et un audit d'intention arrive de toute façon trop tard, après cascade.
- **Laisser le PO agent combler les trous par défaut** : c'est le mécanisme qui a recréé la V1, une valeur par défaut plausible jamais réellement décidée.
- **Checklist générique figée, non instanciée par produit** : les dimensions de contrôle varient (web, shop, food). Une liste figée raterait les dimensions propres à chaque produit.
- **Raffinement successif seul, sans validation de la racine** : développe une intention potentiellement fausse sans jamais la rejuger, avec un coût de retour croissant.
