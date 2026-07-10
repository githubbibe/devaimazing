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
- **Étape 2 en cours** : `state.py` ne demandait aucune implémentation (dataclasses/enums
  déjà complets, pas de corps `...`). `config.py` est implémenté (chargement de
  `studio.yml` + fichier projet, fusion récursive — une section imbriquée comme `git:`
  n'est pas remplacée en bloc, seules les clés redéfinies par le projet sont écrasées ;
  expansion de `~` dans les chemins) et testé : 6 tests dans
  `runtime/tests/test_config.py`, tous verts (les 4 stubs d'origine + 2 tests ajoutés
  pour `from_env`).
- `tools/filesystem.py` et `tools/git.py` sont implémentés. `filesystem.py` :
  lecture/écriture de fiches, `append_feedback` s'appuie sur la section `## Feedback` du
  template `templates/card-agent.md.template` (retire le marqueur `_Aucun feedback pour
  l'instant._`, ajoute une ligne `[date] [agent] : texte`). `git.py` : commandes git
  réelles en sous-process (`asyncio.create_subprocess_exec`), identité par agent via
  `GIT_AUTHOR_*`/`GIT_COMMITTER_*`, hash de branche basé sur timestamp+nom de feature.
  20 tests ajoutés (`test_filesystem.py`, `test_git.py` — ce dernier sur de vrais dépôts
  git temporaires, y compris un cas de conflit de merge), tous verts.
- `tools/ollama.py` est implémenté, via le client officiel `ollama.AsyncClient`
  (`/api/chat`, messages system+user). Retry avec backoff exponentiel (3 tentatives,
  aligné sur `ollama.max_retries` dans `config/studio.yml`, pattern de
  `skills/retry-patterns.md`) sur les erreurs de connexion et les 5xx ; pas de retry sur
  un timeout ni sur les codes non retryables (400/401/403/404, voir
  `skills/retry-patterns.md`). `ExternalServiceError` (déclarée dans le module — pas
  encore de hiérarchie d'exceptions partagée côté runtime devaimazing, contrairement aux
  projets cibles qui ont `backend/exceptions.py`) et `TimeoutError` sont levées selon le
  contrat du stub. `httpx` ajouté en dépendance explicite de `pyproject.toml` (utilisé
  directement pour capter `httpx.TimeoutException`, jusque-là seulement transitif via le
  paquet `ollama`). 7 tests ajoutés (`test_ollama.py`, faux client scripté — aucun appel
  réseau réel), tous verts.
- `tools/claude_code.py` est implémenté : sous-process `claude -p --model <model>
  --output-format json`, prompt transmis via stdin (pas en argument, pour éviter les
  limites de taille d'argv sur les fiches/skills volumineux), schéma JSON de sortie
  vérifié par un appel réel au CLI (`result`, `usage.input_tokens`/`output_tokens`,
  `duration_ms`, `is_error`). **Point non tranché, noté dans le docstring** : aucun flag
  de permissions (`--dangerously-skip-permissions`, `--allowedTools`) n'est ajouté —
  un agent dont la fiche implique des accès fichiers déclenchera une invite de
  permission bloquante en exécution non interactive ; à trancher avant un run de bout en
  bout. 5 tests ajoutés (`test_claude_code.py`, faux sous-process scripté — aucun appel
  API réel), tous verts. **`tools/*.py` complet — étape 2 terminée pour les tools.**
  38/38 au total sur `runtime/tests/`.
- `examples/demo-todo-app/` n'a pas de code source (`src/` annoncé au README mais absent),
  et il n'existe pas de `config/projects/demo-todo-app.yml`. Aucune cible réelle pour un
  run de bout en bout pour l'instant.

## Prochaines étapes

1. ~~Compléter les stubs des 7 `nodes/*.py` au contrat complet~~ — fait le 2026-07-10.
2. Implémenter dans l'ordre de dépendance : ~~`state.py`~~ (rien à faire) → ~~`config.py`~~
   → ~~`tools/filesystem.py`, `tools/git.py`, `tools/ollama.py`, `tools/claude_code.py`~~
   (fait le 2026-07-10) → `graph.py` → `nodes/*.py` → `cli.py` → `metrics.py`.
3. Remplir `runtime/tests/test_config.py` (et les futurs tests) avec de vraies assertions
   au fur et à mesure de chaque implémentation.
4. Construire une cible minimale réelle pour `demo-todo-app` (FastAPI + React +
   `config/projects/demo-todo-app.yml`) pour avoir quelque chose à exécuter.
5. Premier run de bout en bout — en mode dégradé (humain + Claude Code, pas devaimazing
   lui-même, puisqu'il ne peut pas encore s'exécuter sur son propre code).

## Point de reprise

Prochaine session : poursuivre l'étape 2 par `graph.py` (câblage LangGraph — nodes,
routing dynamique selon state.current_phase/agent_sequence, checkpoints), sauf décision
contraire. Le placeholder ntfy et l'état de
`demo-todo-app` (étape 4) restent à trancher explicitement avant d'être traités — ne pas
les combler par une valeur par défaut « raisonnable » sans validation humaine (cohérent
avec le principe de l'ADR 0008).
