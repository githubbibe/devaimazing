# Feuille de route - Implémentation du runtime devaimazing

**Dernière mise à jour** : 2026-07-09

## État au 2026-07-09

- Aucune question en attente active dans le dépôt (vérifié : pas de point « en suspens »
  réel, seulement des descriptions du mécanisme dans `docs/workflow.md`/`prompts/pm.md`).
- `config/studio.yml` : `notifications.ntfy.topic` reste à `<PLACEHOLDER_TOPIC>`, à
  remplacer avant que les notifications fonctionnent. Non bloquant pour le développement.
- Stubs runtime (~933 lignes) : `config.py`, `graph.py`, `state.py`, `metrics.py`,
  `tools/*.py` sont avancés (docstrings substantielles, typage, structure claire).
- **Trou concret** : les 6 fichiers `runtime/studio/nodes/*.py` (pm, architect, backend,
  frontend, test, security) sont des templates génériques identiques (21 lignes chacun,
  `run(state) -> state` avec docstring minimale). Aucun n'a de `Raises`, `Side effects`,
  `Example`, ni de contrat spécifique à son agent — non conforme à la checklist de
  `skills/stub-first.md`. Implémenter la logique métier dessus reproduirait la dérive que
  le stub-first est censé cadrer.
- `examples/demo-todo-app/` n'a pas de code source (`src/` annoncé au README mais absent),
  et il n'existe pas de `config/projects/demo-todo-app.yml`. Aucune cible réelle pour un
  run de bout en bout pour l'instant.

## Prochaines étapes

1. Compléter les stubs des 6 `nodes/*.py` au contrat complet (Args/Returns/Raises/Side
   effects/Example, spécifique à chaque agent — ce que fait le PM en phase 1/3 diffère de
   ce que fait Sécu en phase 8).
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

Prochaine session : démarrer par l'étape 1 (compléter les stubs des `nodes/*.py`), sauf
décision contraire. Le placeholder ntfy et l'état de `demo-todo-app` (étape 4) restent à
trancher explicitement avant d'être traités — ne pas les combler par une valeur par défaut
« raisonnable » sans validation humaine (cohérent avec le principe de l'ADR 0008).
