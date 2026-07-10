# Feuille de route - Implémentation du runtime devaimazing

**Dernière mise à jour** : 2026-07-10

## État au 2026-07-10

- Aucune question en attente active dans le dépôt (vérifié : pas de point « en suspens »
  réel, seulement des descriptions du mécanisme dans `docs/workflow.md`/`prompts/pm.md`).
- `config/studio.yml` : `notifications.ntfy.topic` reste à `<PLACEHOLDER_TOPIC>`, à
  remplacer avant que les notifications fonctionnent. Non bloquant pour le développement.
- Stubs runtime (~933 lignes) : `config.py`, `graph.py`, `state.py`, `metrics.py`,
  `tools/*.py` sont avancés (docstrings substantielles, typage, structure claire).
- **Étape 1 terminée** : les 7 fichiers `runtime/studio/nodes/*.py` (pm, architect,
  backend, frontend, test, security, closer) ont désormais un contrat complet
  (Args/Returns/Raises/Side effects/Example/Notes) spécifique à chaque agent, conforme à
  la checklist de `skills/stub-first.md`. Chaque docstring précise : quelles phases le
  node couvre, quel modèle il appelle, quels side effects (fichiers, commits, tokens) il
  produit, et les transitions de `state.current_phase` attendues. Aucune logique de
  contrôle ajoutée (corps toujours `...`).
- `examples/demo-todo-app/` n'a pas de code source (`src/` annoncé au README mais absent),
  et il n'existe pas de `config/projects/demo-todo-app.yml`. Aucune cible réelle pour un
  run de bout en bout pour l'instant.

## Prochaines étapes

1. ~~Compléter les stubs des 7 `nodes/*.py` au contrat complet~~ — fait le 2026-07-10.
2. Implémenter dans l'ordre de dépendance : `state.py` → `config.py` → `tools/*.py`
   (filesystem, git, ollama, claude_code) → `graph.py` → `nodes/*.py` → `cli.py` →
   `metrics.py`.
3. Remplir `runtime/tests/test_config.py` (et les futurs tests) avec de vraies assertions
   au fur et à mesure de chaque implémentation.
4. Construire une cible minimale réelle pour `demo-todo-app` (FastAPI + React +
   `config/projects/demo-todo-app.yml`) pour avoir quelque chose à exécuter.
5. Premier run de bout en bout — en mode dégradé (humain + Claude Code, pas devaimazing
   lui-même, puisqu'il ne peut pas encore s'exécuter sur son propre code).

## Point de reprise

Prochaine session : démarrer par l'étape 2 (implémentation, en commençant par
`state.py`), sauf décision contraire. Le placeholder ntfy et l'état de `demo-todo-app`
(étape 4) restent à trancher explicitement avant d'être traités — ne pas les combler par
une valeur par défaut « raisonnable » sans validation humaine (cohérent avec le principe
de l'ADR 0008).
