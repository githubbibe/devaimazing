# ADR 0001 - Agents stateless sauf PM

**Date** : 2026-06  
**Statut** : Accepté

## Contexte

Dans un système multi-agents, la mémoire peut être portée par chaque agent individuellement
(chacun a son historique de conversation) ou centralisée (un seul agent porte l'état global).

## Décision

Tous les agents (Architecte, Back, Front, Test, Sécu) démarrent à chaque activation avec
uniquement leur prompt système + skills + fiche de tâche. Pas d'historique de conversation
persisté entre les activations.

Le PM seul porte la mémoire projet via un checkpointer SQLite LangGraph.

## Raisons

1. **Contrôle du contexte** : un agent stateless ne peut pas "se souvenir" d'une décision
   erronée d'un run précédent. Chaque activation repart sur une base propre.

2. **Économie de tokens** : pas d'historique = contexte minimal = moins de tokens consommés
   par activation.

3. **Déterminisme** : pour un même input (prompt + skills + fiche), l'agent produit un output
   comparable. Facilite le debugging et la reproductibilité.

4. **Scalabilité future** : des agents stateless peuvent être remplacés, mis à jour ou
   changés de modèle sans migration d'état.

## Conséquences

- Les fiches .md sont le seul vecteur de mémoire inter-agents. Elles doivent être
  suffisamment détaillées pour que l'agent puisse travailler sans contexte additionnel.
- Le PM doit maintenir un `project-map.md` à jour pour que l'Architecte (stateless)
  puisse détecter les doublons sans relire tout le codebase.
- Les erreurs et annotations de feedback doivent être écrites dans la fiche, pas transmises
  verbalement entre agents.

## Alternatives rejetées

- **Tous les agents avec historique** : coût tokens prohibitif, risque de dérive contextuelle.
- **Mémoire partagée via Redis** : complexité inutile pour un workflow séquentiel mono-user.
