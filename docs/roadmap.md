# Feuille de route - Implémentation du runtime devaimazing

**Dernière mise à jour** : 2026-07-10

## État au 2026-07-10

- Aucune question en attente active dans le dépôt (vérifié : pas de point « en suspens »
  réel, seulement des descriptions du mécanisme dans `docs/workflow.md`/`prompts/pm.md`).
- `config/studio.yml` : `notifications.ntfy.topic` reste à `<PLACEHOLDER_TOPIC>` (valeur
  commitée, dépôt public) — le vrai topic vit dans `config/local.yml` (gitignoré, voir
  Point de reprise ci-dessous, résolu le 2026-07-10).
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
- `metrics.py` est implémenté (`MetricsCollector`, `TaskMetrics`) : table SQLite `tasks`
  (schéma créé de façon synchrone dans `__init__`, écritures/lectures async via
  `aiosqlite`), `get_run_summary` agrège tokens/durée par agent et par phase, lève
  `ValueError` si le run est inconnu. **Limitation documentée** :
  `record_system_metrics` ne mesure que la RSS du process devaimazing courant (module
  stdlib `resource`), pas la RAM système globale du Mac mini, et ne couvre ni CPU ni GPU
  — `psutil` permettrait un monitoring complet mais n'est pas une dépendance du projet ;
  à ajouter explicitement si nécessaire. 5 tests ajoutés. **92/92 au total sur
  `runtime/tests/`.**
- `nodes/closer.py` est implémenté. **Gap détecté et corrigé** : `StudioState` n'avait
  aucun champ pour la branche Git créée en phase 3 (PM) — nécessaire pour le merge en
  phase 10 et non recalculable après coup (le nom contient un hash basé sur le
  timestamp de création, ADR 0007). Ajout de `StudioState.branch_name`. Mise à jour de
  `project-map.md` mécanique (pas de LLM) : une ligne par fichier produit dans "Carte
  des fichiers", une ligne de résumé dans "Historique des runs", en s'appuyant sur la
  structure de `templates/project-map.md.template` ; crée le fichier depuis le template
  s'il n'existe pas. Notification ntfy avec garde-fou : no-op tant que
  `notifications.ntfy.topic` reste `<PLACEHOLDER_TOPIC>`. En cas de conflit de merge :
  pas de retry, `state.status=WAITING_HUMAN` +
  `requires_manual_intervention=True`, le repo reste dans l'état de conflit Git (résolu
  par un humain, cohérent avec `tools/git.py`). **Non câblé (documenté en Notes)** :
  aucun node déjà implémenté n'appelle encore `MetricsCollector.record_task`, donc
  `get_run_summary` ici est best-effort (absorbe `ValueError` si aucune tâche
  enregistrée) — à corriger quand ce câblage sera ajouté aux nodes producteurs. 7 tests
  ajoutés. **99/99 au total sur `runtime/tests/`.**
- Refactor : `should_checkpoint` déplacée de `graph.py` vers `studio/routing.py` (même
  raison que le refactor précédent — `architect.py`/`pm.py` doivent aussi pouvoir
  l'appeler, import circulaire sinon). Ré-exportée depuis `graph.py` pour compatibilité,
  aucun changement de comportement.
- `nodes/architect.py` est implémenté : trois handlers (`_run_audit_amont`,
  `_run_audit_stubs`, `_run_audit_aval`) sélectionnés par `state.current_phase`, tous via
  Claude Code CLI (cwd=repo cible — il lit le code/stubs lui-même). Deux nouveaux
  contrats ajoutés à `prompts/architect.md` : phase 5 répond `STATUT: CONFORME` ou
  `STATUT: ECART` + `AGENT:`/`FEEDBACK:` (parsé par `_parse_audit_decision`, lève
  `RuntimeError` si le format n'est pas respecté — pas de défaut silencieux type
  "conforme par défaut") ; phase 9 réutilise le contrat de blocs `<<<DEVAIMAZING_FILE>>>`
  déjà en place pour Back/Front/Test (documentation potentiellement multi-fichiers : ADR,
  OpenAPI, README, CHANGELOG, `architect-map.md`). Sur écart détecté en phase 5 : la
  fiche fautive est annotée, `state.current_phase` repasse à `Phase.STUBS` avec
  `current_agent_index` repositionné sur l'agent fautif dans la sous-séquence filtrée
  (pas de nouveau champ nécessaire, réutilise `PHASE_AGENT_ROLES`). `should_checkpoint`
  appliqué de façon identique aux trois phases (2, 5, 9 ont toutes une entrée dans
  `PHASE_CHECKPOINT_KEYS`). 9 tests ajoutés, tous verts au premier essai. **108/108 au
  total sur `runtime/tests/`.**
- `nodes/pm.py` est implémenté — **dernier des 7 nodes, `nodes/*.py` est complet.**
  Phase RECEPTION/CADRAGE : dialogue de cadrage synchrone (`input()`/`print()` réels,
  seul node du studio à faire de l'I/O terminal) tournant entièrement dans un seul appel
  de node, jusqu'à validation explicite de l'utilisateur — pas de mécanisme
  checkpoint/resume LangGraph pour cette phase (l'utilisateur est déjà présent à chaque
  tour). Nouveau contrat dans `prompts/pm.md` : `QUESTION: ...` pour continuer le
  dialogue, `FICHE_VALIDEE:\n<contenu>` une fois prêt (le runtime affiche la proposition
  et demande confirmation, jamais l'inverse). **Gap détecté et corrigé** : le champ
  **Nom de la feature**, requis par `docs/workflow.md` (« le PM demande explicitement un
  nom de feature ») mais absent de `templates/card-root.md.template` — ajouté au
  template, extrait par regex en phase 3 pour nommer la branche.
  Phase FICHES implémentée **en deux passes**, pour respecter l'ordre documenté
  (« à la validation de cette phase, la branche du run est créée » — donc après, pas
  avant) : 1) première invocation, génère et écrit les fiches (contrat `SEQUENCE:` +
  blocs `<<<DEVAIMAZING_FILE>>>`, même mécanique qu'Architecte/Back/Front/Test) ; si
  `should_checkpoint` est vrai, s'arrête en `WAITING_HUMAN` **sans créer la branche** ;
  2) reprise (`state.agent_cards` déjà rempli) : ne rappelle pas le LLM, crée juste la
  branche et commite. 12 tests ajoutés (dont le dialogue scripté via mock de
  `builtins.input`), tous verts après une correction de test (comparaison stricte vs
  contenu `.strip()`, pas un bug du node). **120/120 au total sur `runtime/tests/` —
  `nodes/*.py` (7/7) et `tools/*.py` complets, seul `cli.py` reste pour boucler le
  runtime.**
- `cli.py` est implémenté (`run`, `resume`, `runs`, `metrics`, `projects`, `doctor`) —
  **étape 2 entièrement terminée, runtime complet de bout en bout.** `run_id` généré par
  horodatage (`run-YYYYMMDD-HHMMSS`, pas de compteur séquentiel partagé à maintenir).
  Reprise vérifiée contre l'API réelle de LangGraph (`aget_state`/`aupdate_state`/
  `ainvoke(None, ...)` — pattern confirmé par un smoke test dédié avant l'implémentation,
  pas deviné). `resume` et `metrics` prennent `--project` en plus de `run_id` : le stub
  d'origine ne le prévoyait pas, mais aucune des deux commandes ne peut sinon savoir
  quel `config/projects/<nom>.yml` charger (chemin de `state.db`/`metrics.db`
  dépendant du projet). `runs` et le format de rapport `project-map.md` se répondent en
  miroir : `_parse_run_history_table` relit la table que `nodes/closer.py` écrit.
  **2 bugs trouvés et corrigés par les tests, avant tout usage réel** :
  1. `StudioConfig(project_name=project)` appelé directement (pas via `.from_env()`)
     ignorait `DEVAIMAZING_CONFIG_DIR` — cassait à la fois l'override utilisateur et la
     testabilité. Centralisé dans un helper `_load_config()`.
  2. `_parse_run_history_table` incluait la ligne de séparation markdown `|---|---|...|`
     comme s'il s'agissait d'une ligne de run — corrigé (et la ligne de placeholder vide
     `| | | | | |` du template est filtrée par le même mécanisme).
  16 tests ajoutés (`test_cli.py`, synchrones — `click.testing.CliRunner` + les commandes
  appellent `asyncio.run()` en interne, incompatible avec un test `async def` sous
  pytest-asyncio). **136/136 au total sur `runtime/tests/`.**
- **Étape 4 terminée** : cible minimale réelle pour `demo-todo-app` construite.
  **Décision prise avec l'utilisateur** : le vrai dépôt git vit hors de devaimazing, à
  `~/code/aimazing/demo-todo-app/` (même pattern que `webaimazing-v2.yml` :
  `repo_path` externe, pas un sous-dossier du dépôt devaimazing). Contenu : backend
  FastAPI (`GET /todos`, `POST /todos`, `GET /todos/{id}`, SQLite local via `sqlite3`
  stdlib, pas d'ORM), frontend Vite + React + TypeScript (liste + création de todos,
  proxy `/todos` vers `localhost:8000`), 4 tests unitaires backend. **Vérifié
  réellement, pas seulement écrit** : `pytest -q` (4/4 verts), `npx tsc --noEmit`
  (aucune erreur), `npx vite build` (build réussi). `PATCH /todos/{id}/complete` et le
  bouton frontend correspondant sont **volontairement absents** — c'est l'objectif du
  run de démonstration (voir le README du projet cible).
  `config/projects/demo-todo-app.yml` créé (même structure que `webaimazing-v2.yml`,
  `test.command: "pytest {target_dir} -q"`), chargement vérifié avec `StudioConfig`
  réelle (`repo_path`, `test_command`, `project_constraints` corrects).
  **Nettoyage** : `examples/demo-todo-app/specs/project-map.md` (dans le dépôt
  devaimazing) décrivait un run-000 fictif avec des fichiers qui n'ont jamais existé —
  supprimé (déjà poussé, donc suppression sûre selon la règle du CLAUDE.md du dépôt) au
  profit du vrai `specs/project-map.md` qui vivra dans le repo cible. `README.md` racine
  corrigé : `devaimazing run examples/demo-todo-app` était un chemin, alors que la CLI
  attend un nom de projet (`devaimazing run demo-todo-app`, voir `cli.py::run`) ; arbre
  `examples/demo-todo-app/` annonçait un `src/` qui n'a jamais existé sous cette forme.

## Prochaines étapes

1. ~~Compléter les stubs des 7 `nodes/*.py` au contrat complet~~ — fait le 2026-07-10.
2. ~~Implémenter dans l'ordre de dépendance : `state.py` → `config.py` →
   `tools/*.py` → `graph.py` → `nodes/*.py` (7/7) → `cli.py`~~ — **fait le
   2026-07-10, étape 2 terminée.**
3. ~~Remplir les tests avec de vraies assertions au fur et à mesure de chaque
   implémentation~~ — fait en continu tout au long de l'étape 2 (136 tests, tous les
   modules du runtime couverts, aucun stub `...` restant dans `runtime/tests/`).
4. ~~Construire une cible minimale réelle pour `demo-todo-app`~~ — fait le 2026-07-10.
5. Premier run de bout en bout — en mode dégradé (humain + Claude Code, pas devaimazing
   lui-même, puisqu'il ne peut pas encore s'exécuter sur son propre code).

## Point de reprise

Le runtime devaimazing est fonctionnellement complet (`state.py` → `config.py` →
`tools/*.py` → `graph.py` → `nodes/*.py` (7/7) → `metrics.py` → `cli.py`, 157 tests
verts) et une cible réelle existe (`demo-todo-app`, testée en local — backend, frontend,
config).

**Les 4 points en attente avant un run réel sont résolus (2026-07-10)** :
1. **Permissions Claude Code CLI** : vérifié empiriquement (invocations réelles) qu'aucun
   flag n'est nécessaire — Read/Glob/Grep passent sans invite en mode `-p`, Write est
   refusé proprement (pas de hang). `run_claude_code` détecte maintenant explicitement
   `permission_denials`.
2. **Câblage des métriques** : `studio.metrics.record_agent_result` appelé par les 7
   nodes à chaque tentative.
3. **`agents.max_iterations`** : appliqué en tête des 4 nodes producteurs/audit
   (`studio.routing.max_iterations_exceeded`) — bascule en `RunStatus.FAILED` sans appel
   LLM au-delà de la limite.
4. **Placeholder ntfy** : le repo `githubbibe/devaimazing` étant **public**, la vraie
   valeur ne pouvait pas être committée dans `config/studio.yml` (sécurité du topic ntfy.sh
   = secret, sinon lisible par n'importe qui indéfiniment via l'historique git). Ajout
   d'un mécanisme d'override local : `config/local.yml`, gitignoré, fusionné en dernier
   par `StudioConfig` par-dessus `studio.yml`/le projet. Le vrai topic (64 caractères
   hex, fourni par l'utilisateur) vit dans ce fichier local, jamais commité.

**Étape 5 démarrée (2026-07-10)** : premier run réel lancé par l'utilisateur
(`devaimazing run demo-todo-app --objective "..."`), deux bugs réels trouvés et corrigés
avant qu'il aille au bout :

1. **`DEVAIMAZING_PROJECT` non propagée** (bug de code, corrigé commit `f5a9c1f`) :
   chaque node appelle `StudioConfig.from_env()` en interne, qui lit
   `DEVAIMAZING_PROJECT` depuis `os.environ` — mais `cli.py::_load_config()` construisait
   une config pour l'usage de la commande CLI elle-même sans jamais exporter la variable
   dans l'environnement du process. Le node `pm` levait `ValueError` dès sa première
   activation. Fix : `_export_project_env()` appelée en tête de `_run_async`/
   `_resume_async`, avant tout appel à `build_graph`/`ainvoke`. Test de régression
   vérifié rouge sans le correctif avant d'être committé (règle du CLAUDE.md du dépôt).
2. **Environnement, pas du code — `.venv` sous iCloud Drive** : ce dépôt vit sous
   `~/Library/Mobile Documents/com~apple~CloudDocs/...`. Un `.venv` créé *dans* le dépôt
   (`uv sync` par défaut) subit la synchronisation iCloud en tâche de fond sur des
   milliers de petits fichiers/symlinks, causant des échecs intermittents
   `ModuleNotFoundError: No module named 'studio'` sur l'installation editable — pas
   reproductible à chaque appel (le `.pth` d'installation editable, pourtant
   octet-pour-octet identique à une copie qui fonctionnait, échouait sporadiquement à
   être pris en compte). Diagnostiqué par élimination (fichier .pth minimal de test hors
   nom original fonctionnait, le fichier réel non, de façon intermittente ; `brctl
   status` a confirmé une synchronisation active au moment des échecs). Fix : `.venv`
   recréé hors du dépôt, à `~/.venvs/devaimazing/` — stable sur 5+ appels consécutifs
   depuis plusieurs répertoires après la correction. `README.md` mis à jour en
   conséquence (installation hors du dépôt, alias `devaimazing=~/.venvs/devaimazing/bin/
   devaimazing`).

3. **Connexion SQLite du checkpointer jamais fermée** (bug de code, corrigé commit
   `7c458cf`) : le run s'est bien lancé (phase 1, dialogue PM en terminal réussi), mais
   après validation de la fiche racine par l'utilisateur, le process ne rendait jamais
   la main — ni erreur ni sortie, juste un blocage silencieux (signalé par l'utilisateur :
   « je ne vois pas de progression je ne sais pas si le processus est planté ou en cours
   de calcul »). Diagnostiqué avec l'outil macOS `sample` sur le process bloqué :
   `_Py_Finalize` → `wait_for_thread_shutdown` attendait indéfiniment le thread worker
   d'arrière-plan d'`aiosqlite` (`_connection_worker_thread`), jamais fermé explicitement
   après la fin du graphe. Fix : `try/finally` autour de `graph.ainvoke(...)` dans
   `_run_async`/`_resume_async`, avec `await graph.checkpointer.conn.close()` dans le
   `finally`. Test de régression vérifié rouge sans le correctif (fix temporairement
   neutralisé, 3 tests passent au rouge, restauré) avant d'être committé.
4. **Prompts Sonnet (Architecte/Sécu/PM) réclamant leurs outils Write/Edit** (bug de
   prompt, pas de code) : après le fix du point 3, le run a atteint la phase 2 (audit
   amont Architecte) puis a échoué avec `RuntimeError: Claude Code CLI s'est vu refuser
   l'accès à un outil (Write)`. Cause : le contrat de sortie de `prompts/architect.md`
   dit que le contenu de la réponse est « écrit tel quel » dans `architect-brief.md`,
   mais ne dit jamais explicitement à l'agent de ne pas utiliser lui-même son outil
   Write — Sonnet, invité à « produire le brief », tente naturellement d'écrire le
   fichier directement plutôt que de répondre en texte. Le refus est correctement
   détecté par la vérification `permission_denials` ajoutée au point 1 (`RuntimeError`
   propre, pas de hang silencieux) — mais le run échoue quand même puisque
   `architect-brief.md` n'est jamais produit. Fix : ajout d'une interdiction explicite
   « Tu n'utilises jamais tes outils Write ou Edit » dans `prompts/architect.md`,
   `prompts/security.md` et `prompts/pm.md` (les trois prompts qui passent par
   `run_claude_code`). **Pas de test de régression automatisé possible** : c'est un
   comportement de modèle de langage face à un prompt, pas une branche de code
   Python testable unitairement — seule la vérification empirique (relancer le run)
   fait foi. La garde-fou `permission_denials` reste en place comme filet de sécurité
   si le prompt échoue à dissuader le modèle une prochaine fois.

Run relancé après ces quatre corrections — en cours, résultat pas encore connu.

**Backlog identifié en marge (2026-07-10, pas bloquant, pour plus tard)** :
`devaimazing resume` (`cli.py::resume`) ne sait reprendre qu'un run explicitement en
attente d'une validation humaine (`awaiting_human_validation=True` dans le state
checkpointé) — pas un run interrompu au milieu d'un nœud (crash, `kill`, coupure).
Constaté en pratique après le bug 4 : `run-20260710-185636` s'est arrêté en
`IN_PROGRESS`/phase `AUDIT_AMONT` (crash dans le nœud Architecte, pas un checkpoint
volontaire) — `resume` refuse ce cas avec « n'est pas en attente de validation », alors
que LangGraph sait très bien rejouer le nœud interrompu via `graph.ainvoke(None,
config=thread_config)` sur le même `thread_id` (vérifié manuellement, hors CLI, pour
reprendre ce run précis sans repasser par le dialogue PM de la phase 1). À faire : soit
assouplir la garde de `resume` pour accepter aussi `status == RunStatus.IN_PROGRESS`
sans validation en attente (reprise après crash), soit ajouter une commande dédiée
(`devaimazing retry <run-id>` ?) qui documente explicitement ce second cas d'usage, avec
son propre test de régression.

**Décision prise (2026-07-10, hors code)** : la mise en production de devaimazing
lui-même devra être conteneurisée Podman, cohérent avec le reste de l'infra prod (voir
CLAUDE.md du dépôt parent). Implications concrètes non câblées à ce stade : Claude Code
CLI (actuellement subprocess supposant `claude` installé sur l'hôte), accès réseau à
Ollama (actuellement `localhost:11434` en dur par défaut, alors qu'un conteneur devra
joindre `dataimazing-ramiris`/son remplaçant via `dataimazing-network`), montage du repo
projet cible en volume, persistance de `state.db`/`metrics.db`. Pas de travail engagé
là-dessus — pertinent seulement une fois qu'il y a un run réel à déployer, pas avant
l'étape 5.
