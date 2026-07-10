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
  API réel), tous verts. **`tools/*.py` complet.**
- `graph.py` est implémenté. `build_graph` est passée **async** (changement de contrat
  par rapport au stub d'origine, justifié : `AsyncSqliteSaver` de
  `langgraph-checkpoint-sqlite` exige une connexion `aiosqlite` ouverte via `await`,
  vérifié contre l'API réelle du paquet installé — impossible en fonction synchrone).
  `router` résout le prochain node depuis `state.current_phase`, et pour les phases 4/6
  (stubs/implémentation) filtre `state.agent_sequence` aux rôles concernés par la phase
  (phase 4 : back/front seuls ; phase 6 : + back-tu/front-tu) avant d'indexer avec
  `current_agent_index` — un état incohérent (index hors bornes, agent inconnu) lève
  `ValueError` plutôt que d'être absorbé silencieusement. `should_checkpoint` corrige une
  référence obsolète du stub (`state.config`, qui n'existe pas) : charge la config via
  `StudioConfig.from_env()`, avec `state.awaiting_human_validation` comme garde-fou
  prioritaire et non désactivable (ADR 0008). 19 tests ajoutés (`test_graph.py` : router,
  checkpoints, construction structurelle du graphe compilé), tous verts.
  **`tools/*.py` + `graph.py` : étape 2 terminée pour l'infrastructure.**
  57/57 au total sur `runtime/tests/`.
- Extrait `studio/routing.py` (AGENT_TO_NODE, PHASE_NODE, PHASE_AGENT_ROLES,
  PHASE_CHECKPOINT_KEYS, NEXT_PHASE_AFTER + helpers `phase_agent_sequence`/
  `is_last_agent_of_phase`) pour partager la logique de routage entre `graph.py` et
  `nodes/*.py` sans import circulaire (`graph.py` importe `studio.nodes`). `graph.py`
  refactoré pour consommer ce module, sans changement de comportement.
- **Décision prise avec l'utilisateur** : contrat de sortie fichiers pour les agents
  producteurs de code — `<<<DEVAIMAZING_FILE path="...">>>...<<<DEVAIMAZING_END>>>`
  (délimiteurs distinctifs plutôt que des ``` markdown, pour éviter toute ambiguïté avec
  du code cité en dehors d'un vrai bloc fichier). Alternative écartée : function-calling
  Ollama (donner à Qwen de vrais outils Read/Write/Edit) — plus robuste mais nettement
  plus de travail, reporté. Contrat documenté dans `prompts/backend.md`,
  `prompts/frontend.md`, `prompts/test.md` (section Format de sortie). Parseur :
  `tools/filesystem.py::parse_agent_file_blocks`.
- `nodes/backend.py` et `nodes/frontend.py` sont implémentés : lecture fiche, appel
  Ollama, parsing des blocs, écriture des fichiers, commit conventionnel sous l'identité
  back/front (back-tu et front-tu commitent sous l'identité de leur agent principal, avec
  le skill `non-regression.md` injecté en plus), avancement de
  `current_agent_index`/`current_phase` via `studio.routing`. Si l'agent ne produit aucun
  bloc reconnu (auto-détection de blocage) : le texte est ajouté à sa propre section
  Feedback et le run passe en `WAITING_HUMAN` plutôt que d'échouer silencieusement.
  **Non appliqué** : `agents.max_iterations` (limite de 3 renvois) — compteur
  `AgentResult.iteration` informatif seulement. 14 tests ajoutés. 72/72 au total.
- `nodes/test.py` est implémenté (génération des tests d'intégration/non-régression via
  le même contrat que Back/Front). **Décision prise avec l'utilisateur** : la commande de
  test est définie par projet (`config/projects/<nom>.yml`, nouvelle section
  `test.command`, placeholder `{target_dir}`) plutôt que globalement — les stacks cibles
  sont hétérogènes, et ça permet de tester le SI dans un environnement de développement
  distinct de celui de devaimazing. `StudioConfig.test_command` ajouté (None si non
  défini pour le projet — pas de commande par défaut). Si la commande est définie et
  échoue : traité comme non-régression (feedback + `WAITING_HUMAN`). Si non définie : les
  tests sont écrits et commités mais pas exécutés (dégradé, non bloquant). **Non câblé** :
  la notification ntfy sur échec (pas d'outil de notification, topic toujours
  `<PLACEHOLDER_TOPIC>`). 7 tests ajoutés (dont exécution réelle de sous-process via
  `python3 -c`, pas de mock sur `_run_test_command` lui-même). **79/79 au total sur
  `runtime/tests/`.**
- `nodes/security.py` est implémenté : couche 1 (bandit/semgrep, zéro token) puis couche
  2 (Sonnet via Claude Code CLI, cwd=repo cible — il lit le code lui-même, pas de
  réinjection intégrale dans le prompt). **Schémas JSON bandit/semgrep vérifiés par
  exécution réelle** (pas seulement déduits de la doc) : `bandit` →
  `results[].issue_severity` (LOW/MEDIUM/HIGH) ; `semgrep` →
  `results[].extra.severity` (INFO/WARNING/ERROR, normalisé vers LOW/MEDIUM/HIGH).
  Aucun des deux ne produit nativement `CRITICAL` en config par défaut — noté en Notes.
  **Décision/correction de contrat** : le stub d'origine disait que
  `sast.fail_on_severity` bloquait « l'exécution des outils SAST eux-mêmes » — inexact
  (bandit/semgrep ne s'arrêtent jamais en cours de scan sur une sévérité, vérifié). Contrat
  clarifié : un finding atteignant le seuil produit et commite quand même le rapport,
  mais bascule `state.status=WAITING_HUMAN` au lieu d'avancer automatiquement à
  `Phase.AUDIT_AVAL` (cohérent avec la préférence du projet pour les checkpoints
  explicites, ADR 0008/0010).
  **Bug trouvé et corrigé par les tests** : `_run_sast_tool` utilisait `str.format()`
  sur la commande brute pour substituer `{target_dir}` — casse dès que la commande
  contient d'autres accolades (ex. un literal JSON dans un test). Remplacé par
  `.replace("{target_dir}", ...)`. Même bug latent corrigé dans
  `nodes/test.py::_run_test_command` (test de régression ajouté aux deux). 8 tests
  ajoutés pour security.py + 1 test de régression pour test.py. **87/87 au total sur
  `runtime/tests/`.**
- `examples/demo-todo-app/` n'a pas de code source (`src/` annoncé au README mais absent),
  et il n'existe pas de `config/projects/demo-todo-app.yml`. Aucune cible réelle pour un
  run de bout en bout pour l'instant.

## Prochaines étapes

1. ~~Compléter les stubs des 7 `nodes/*.py` au contrat complet~~ — fait le 2026-07-10.
2. Implémenter dans l'ordre de dépendance : ~~`state.py`~~ (rien à faire) → ~~`config.py`~~
   → ~~`tools/filesystem.py`, `tools/git.py`, `tools/ollama.py`, `tools/claude_code.py`~~
   → ~~`graph.py`~~ → ~~`nodes/backend.py`, `nodes/frontend.py`, `nodes/test.py`,
   `nodes/security.py`~~ (fait le 2026-07-10) → `nodes/closer.py`, `nodes/architect.py`,
   `nodes/pm.py` → `cli.py` → `metrics.py`.
3. Remplir `runtime/tests/test_config.py` (et les futurs tests) avec de vraies assertions
   au fur et à mesure de chaque implémentation.
4. Construire une cible minimale réelle pour `demo-todo-app` (FastAPI + React +
   `config/projects/demo-todo-app.yml`) pour avoir quelque chose à exécuter.
5. Premier run de bout en bout — en mode dégradé (humain + Claude Code, pas devaimazing
   lui-même, puisqu'il ne peut pas encore s'exécuter sur son propre code).

## Point de reprise

Prochaine session : poursuivre `nodes/*.py` par `closer.py`, `architect.py`, `pm.py`
(chacun a son propre point de conception à trancher — voir journal ci-dessus), puis
`cli.py` et `metrics.py`, sauf décision contraire. Le placeholder ntfy et l'état de
`demo-todo-app` (étape 4) restent à trancher explicitement avant d'être traités — ne pas
les combler par une valeur par défaut « raisonnable » sans validation humaine (cohérent
avec le principe de l'ADR 0008).
