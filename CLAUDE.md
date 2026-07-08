# CLAUDE.md

Ce fichier fournit des repères à Claude Code (claude.ai/code) pour travailler dans ce dépôt.

## Langue

Toutes les réponses, tous les commentaires de code, messages de commit et documents produits
dans ce dépôt doivent être rédigés **en français**, sauf demande explicite contraire de
l'utilisateur.

## Règle de sécurité : suppression de fichiers

**Ne jamais supprimer un fichier (ou une branche, ou l'historique local) sans être certain
qu'un `git push` a déjà été effectué au préalable.** Avant toute suppression :

1. Vérifier `git status` et `git log @{u}..HEAD` (ou équivalent) pour confirmer qu'aucun commit
   local n'est en avance sur le remote.
2. Si un doute subsiste sur l'état du push, demander confirmation à l'utilisateur plutôt que de
   supprimer.

Cette règle s'applique aux suppressions de fichiers explicitement demandées comme aux nettoyages
« évidents » (fichiers jugés inutiles, obsolètes, ou générés) — le doute profite toujours au
fichier, pas à la suppression.

## Qu'est-ce que ce dépôt ?

devaimazing est un **studio de développement multi-agents local-first** : un orchestrateur
LangGraph qui exécute un pipeline fixe de 6 agents spécialisés (PM, Architecte, Back, Front,
Test, Sécu) à travers 10 phases pour concevoir, implémenter, tester, sécuriser et documenter une
fonctionnalité dans un projet *cible* — pas dans ce dépôt. Ce dépôt est le studio/orchestrateur
lui-même.

**État actuel : ce code est lui-même en phase stub-first.** Tous les fichiers sous
`runtime/studio/` (`graph.py`, `config.py`, `cli.py`, tous les `nodes/*.py` et `tools/*.py`) ne
contiennent que des signatures, des types et des docstrings complètes avec des corps de fonction
`...` — aucune logique n'est encore implémentée. `runtime/tests/test_config.py` suit le même
principe : des fonctions de test avec docstring mais sans assertion. Quand on te demande
d'« implémenter » quelque chose ici, c'est normal — il faut remplir le stub selon le contrat de
sa docstring, ne pas supposer que quelque chose est cassé parce que c'est un no-op. Voir
`skills/stub-first.md` pour le format exact d'un stub (signatures + docstring complète avec
Args/Returns/Raises/Side effects/Example, zéro logique de contrôle, zéro logique métier) — la
même convention que l'orchestrateur impose à ses propres agents en aval s'applique aussi aux
contributions manuelles à ce dépôt.
