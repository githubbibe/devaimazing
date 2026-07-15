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

## Pull en début de session

En début de session (avant la première action modifiant ce dépôt), Claude Code vérifie
si la branche locale est en retard sur `origin` (`git fetch` puis comparaison, ex.
`git status` ou `git log HEAD..@{u}`) et effectue un `git pull` si nécessaire, sans
attendre une demande explicite — pour ne pas travailler sur un état local périmé (ex. si
une autre session ou l'utilisateur a poussé des changements entre-temps). En cas de
commits locaux non poussés en avance sur `origin`, ou de conflit potentiel, appliquer les
règles de sécurité git habituelles (vérifier avant tout, demander confirmation en cas de
doute) plutôt que de forcer.

## Commit et push

À la fin de toute tâche ayant modifié des fichiers du dépôt, Claude Code commit et push
automatiquement, sans attendre une demande explicite ni une confirmation supplémentaire —
sauf indication contraire de l'utilisateur pour cette tâche précise. Le message de commit
suit les conventions habituelles (résumé de l'intention, pas juste de la description).

## Qu'est-ce que ce dépôt ?

devaimazing est un **studio de développement multi-agents local-first** : un orchestrateur
LangGraph qui exécute un pipeline fixe de 6 nodes couvrant 8 rôles d'agent spécialisés (PM,
Architecte, Back, Back-tu, Front, Front-tu, Test, Sécu — Back-tu/Front-tu partagent leur node
et identité Git avec Back/Front, voir `docs/agents.md`) à travers 10 phases pour concevoir,
implémenter, tester, sécuriser et documenter une fonctionnalité dans un projet *cible* — pas
dans ce dépôt. Ce dépôt est le studio/orchestrateur lui-même.

**État actuel (mis à jour 2026-07-15) : le runtime est entièrement implémenté**, pas en phase
stub-first. Tous les fichiers sous `runtime/studio/` (`graph.py`, `config.py`, `cli.py`,
`routing.py`, tous les `nodes/*.py` et `tools/*.py`) contiennent une logique réelle — plus aucun
corps de fonction `...`. `runtime/tests/` compte 256 tests, tous avec de vraies assertions (voir
`docs/roadmap.md` pour le détail chronologique des chantiers). Quand on te demande
d'« implémenter » ou de corriger quelque chose ici, il s'agit d'une évolution du code existant —
vérifier l'état réel du fichier avant de supposer qu'il s'agit d'un stub à remplir.

La convention stub-first (`skills/stub-first.md`, ADR 0002) reste en revanche activement
appliquée par le pipeline **lui-même** sur les projets *cibles* qu'il pilote : les agents
Back/Front y écrivent d'abord des stubs (phase 4, signatures/types/docstrings) avant
l'implémentation (phase 6) — c'est un principe de méthode imposé en aval, indépendant de la
maturité du code de ce dépôt.

