# Feuille de route - Implémentation du runtime devaimazing

**Dernière mise à jour** : 2026-07-14 (bug réel trouvé et corrigé via un premier
run réel de `devaimazing run-agent`)

## Bug réel trouvé via `run-agent` (2026-07-14) : chemin de fichier absolu non validé

Premier usage réel de `devaimazing run-agent` (voir chantiers ci-dessous) sur
une cible jetable minimale (backend FastAPI + sqlite3, même structure que
`demo-todo-app`), avec un vrai Ollama (conteneurisé, voir Notes
d'environnement plus bas) et une fiche demandant d'ajouter
`PATCH /todos/{id}/complete` à un `backend/main.py` déjà existant.

**Trouvé** : `qwen2.5:1.5b-instruct` (modèle volontairement sous-dimensionné
pour ce test rapide sur CPU, pas le `7b-instruct` de prod) a répondu avec
`"path": "/backend/main.py"` (slash de tête) — imitation littérale probable
de la formulation `/backend/` dans `prompts/backend.md` (« Tu travailles
UNIQUEMENT dans le dossier `/backend/` du projet cible »). Aucun code du
runtime ne validait qu'un chemin produit par un agent restait relatif :
`config.repo_path / "/backend/main.py"` (pathlib) **ignore silencieusement**
`repo_path` dès que le second opérande est absolu
(`Path("/repo") / "/etc/passwd" == Path("/etc/passwd")`) — le node a donc
tenté d'écrire directement sur `/backend/main.py` du système de fichiers
réel. Bloqué par un `PermissionError` du système d'exploitation ici (pas par
devaimazing), mais un vrai risque d'écriture hors périmètre sur un
environnement où ce chemin serait accessible en écriture.

**Fix** : nouvelle fonction `tools/filesystem.py::_validate_relative_path` —
rejette (`ValueError`) un chemin vide, absolu, ou contenant une traversée de
répertoire (`..`). Appliquée aux deux points d'entrée où un agent producteur
déclare des chemins de fichiers : `parse_structured_file_output` (Back/Front/
Test, sortie contrainte Ollama) et `parse_agent_file_blocks` (PM/Architecte,
délimiteurs `<<<DEVAIMAZING_FILE>>>` via Claude Code CLI — même classe de
risque des deux côtés, pas seulement Ollama). Aucun changement requis côté
nodes : `backend.py`/`frontend.py`/`test.py` absorbaient déjà tout
`ValueError` de `parse_structured_file_output` dans leur branche
`feedback_sent`/`WAITING_HUMAN` existante (traite un chemin invalide comme un
blocage détecté, pas un crash) ; `pm.py`/`architect.py` laissaient déjà
remonter un `ValueError` de `parse_agent_file_blocks` sans l'absorber (échec
net et visible, cohérent avec leur traitement existant d'un contrat de sortie
non respecté).

6 tests de régression ajoutés (`test_filesystem.py` : chemin absolu et
traversée `..` pour les deux fonctions, y compris `fallback_path` ;
`test_backend_node.py` : confirme la dégradation vers `feedback_sent`/
`WAITING_HUMAN` plutôt qu'un crash), **vérifiés rouges sans le fix** — la
désactivation temporaire de la validation a reproduit exactement le
`PermissionError: [Errno 13] Permission denied: '/backend'` observé en run
réel — avant d'être committés. **234/234 au total sur `runtime/tests/`**
(était 228).

**Notes d'environnement (2026-07-14, pertinent pour les prochains tests
réels)** : voir aussi la note [[project_ollama_containerized]] en mémoire.
Ollama doit tourner en conteneur Podman (l'utilisateur change souvent de
machine) :

```
podman run -d --name ollama \
  -v <chemin-large-fichiers>/Ollama:/root/.ollama \
  -e OLLAMA_MODELS=/root/.ollama \
  -p 11434:11434 \
  docker.io/ollama/ollama:latest
```

Cette machine n'a pas de GPU (`nvidia-smi` absent) — `qwen2.5:7b-instruct`
(modèle de prod, voir `config/studio.yml`) y traite le prompt à ~5
tokens/s, largement au-delà du timeout par défaut (180s) dès que le prompt
dépasse quelques centaines de tokens (skills injectés + fiche + contenu
existant). `qwen2.5:1.5b-instruct` reste utilisable pour un test rapide du
câblage (quelques dizaines de secondes), mais n'est pas représentatif de la
qualité de sortie du modèle de prod — à ne pas confondre lors de la lecture
de futurs runs de test sur cette machine.

## Priorité immédiate (ajouté 2026-07-14, avant tout le reste)

**Décision utilisateur (2026-07-14)** : les deux chantiers ci-dessous étaient
**reportés à une session dédiée**. Le chantier 1 a été traité dans cette session
dédiée (voir "Livré" ci-dessous) ; le chantier 2 reste reporté.

### 1. Fiches PM en sortie structurée (Claude Code CLI, chantier 2 du plan
   "sortie structurée" — voir section dédiée plus bas pour le contexte complet)

**Livré (2026-07-14).** Étend le chantier déjà livré côté Ollama (Back/Front/Test,
voir plus bas) au PM côté Claude Code CLI : `run_claude_code` gagne un paramètre
`response_schema`, transmis via `--json-schema` (déjà vérifié disponible en mode
`-p`, recherche 2026-07-11) ; le champ `structured_output` renvoyé par le CLI est
validé (`tools/filesystem.py::parse_pm_structured_output`, `PM_FICHES_SCHEMA`) et
remplace le parsing regex de l'ancienne ligne `SEQUENCE:`. Le contrat prose (blocs
`<<<DEVAIMAZING_FILE>>>`, contenu Markdown libre) est inchangé — le canal structuré
transporte uniquement `sequence` et, par agent, `files_to_create` / `files_to_modify`
/ `files_forbidden` / `existing_files_to_read` / `dependencies`.

**Objectif explicite de l'utilisateur atteint** : validation au moment où le PM
**écrit** la fiche, pas au moment où Back/Front/Test la **lit**. `nodes/pm.py::
_run_fiches` vérifie désormais que chaque chemin de `existing_files_to_read`
existe réellement dans le repo cible **avant** toute écriture sur disque — échec
net (`RuntimeError`) si un chemin est manquant, message actionnable référençant
la fiche et l'agent concernés. `tools/filesystem.py::read_referenced_files` (scan
regex du texte prose, skip silencieux d'un chemin absent — la source du bug)
est supprimée, remplacée par `read_files(repo_path, paths)` : chemins explicites
issus de `state.agent_card_metadata[role]["existing_files_to_read"]`, plus de
scan de texte côté Back/Front/Test.

**Décision de conception prise pendant l'implémentation** : persistance du côté
structuré via un nouveau champ `StudioState.agent_card_metadata: dict[str,
dict[str, list[str]]]` (même précédent que `branch_name`, ADR 0007), plutôt qu'un
bloc YAML front-matter injecté dans chaque fichier `.md` — évite une couche de
parsing markdown redondante avec l'état déjà persisté par le checkpointer SQLite
(ADR 0003). `files_to_create` / `files_to_modify` / `files_forbidden` /
`dependencies` sont capturés et persistés mais **pas encore appliqués en
contrôle** (vérifier que Back/Front ne touchent que leur périmètre déclaré) —
seul `existing_files_to_read` est activement consommé ; une vérification de
périmètre serait un chantier séparé.

Fichiers touchés : `runtime/studio/tools/claude_code.py`, `state.py`,
`tools/filesystem.py`, `nodes/pm.py`, `nodes/backend.py`, `frontend.py`, `test.py`,
`prompts/pm.md`, `templates/card-agent.md.template`. 10 tests ajoutés/modifiés dans
`test_claude_code.py`, 8 dans `test_filesystem.py` (dont suppression des tests de
`extract_file_paths`/`read_referenced_files`), 6 dans `test_pm_node.py` (dont 2
nouveaux garde-fous vérifiés rouges sans le fix avant commit), mise à jour des
fixtures de `test_backend_node.py`/`test_frontend_node.py`/`test_test_node.py`.
**195/195 au total sur `runtime/tests/`** (était 185).

### 2. Commande CLI par agent (`devaimazing run-agent <projet> <agent> <fiche>` ou
   équivalent — nom exact à trancher)

Idée de l'utilisateur : chaque étape/agent lançable individuellement via sa propre
commande, qui lit la fiche correspondante (et la valide contre le schéma du
chantier 1 ?), puis exécute cet agent seul — sans repasser par le graphe complet.

Formalise ce qui a été bricolé à la main tout au long de la session du 2026-07-11
(scripts `python3 -` ad hoc pour inspecter l'état, rejouer un node, `~/resume_run.py`)
en une commande CLI de premier niveau.

**Décision utilisateur (2026-07-14)** : outil de test isolé, ne mute pas le
checkpoint. Exécute l'agent en lecture/écriture sur le repo cible réel (lit la
fiche, appelle le node), mais ne touche jamais à `state.db` — purement pour
tester/déboguer un agent hors du contexte d'un run. Techniquement, ça ne demande
aucun outillage LangGraph particulier : chaque node est déjà une fonction
`async def run(state) -> dict` autonome, découplée du graphe par design
(ADR 0001, agents stateless) — exactement le pattern déjà utilisé par tous les
tests (`await backend_node.run(state)` avec un `StudioState` construit à la main).
La commande n'a donc qu'à construire ce `StudioState` minimal à partir de la
fiche et appeler le node directement, sans passer par `build_graph`/`ainvoke`.

Conséquence de cette décision : cet outil **ne résout pas** le point du backlog
« `devaimazing resume` ne gère pas la reprise après crash » (ci-dessous) — un
outil de test isolé, par définition, ne touche pas au checkpoint. Ce backlog reste
un chantier séparé si on veut le traiter (voir détail de ses deux options plus bas).

**Livré (2026-07-14).** `devaimazing run-agent <projet> <run-id> <agent> --phase <PHASE>`
(`cli.py::run_agent`/`_run_agent_async`) : construit un `StudioState` minimal et
appelle directement `<node>.run(state)` (aucun `build_graph`/`ainvoke`/
checkpointer — `state.db` n'est ni lu ni écrit, conforme à la décision ci-dessus).
`<agent>` accepte les mêmes noms que `state.agent_sequence` (`back`, `back-tu`,
`front`, `front-tu`, `test`, `secu`) plus `pm`, `architect` et `closer` (routage
via `studio.routing.AGENT_TO_NODE`, complété pour `closer` qui n'y figure pas —
absent de `agent_sequence` par construction, mappé à part).

**Reconstruction du `StudioState` par découverte sur disque**, pas par lecture du
checkpoint (qui n'existe pas forcément, ou qu'on choisit justement d'ignorer) :
- `state.agent_cards` : scan de `specs/<specs_dir>/<run-id>/<role>.md` pour
  chacun des 6 rôles connus (convention déjà utilisée par `pm.py::_run_fiches`,
  aucune nouvelle convention introduite). Complétable/remplaçable par `--card`
  pour une fiche à un chemin non conventionnel.
- `state.agent_sequence`/`current_agent_index` : dérivés des fiches trouvées sur
  disque (ordre canonique back/back-tu/front/front-tu/test/secu), avec l'agent
  ciblé ajouté s'il n'y figure pas encore — suffisant pour que
  `state.agent_sequence[state.current_agent_index]` (lu par backend.py/
  frontend.py/test.py/security.py) résolve vers le bon rôle, et pour que
  l'audit de stubs de l'Architecte (qui filtre `agent_sequence` par
  `PHASE_AGENT_ROLES`) retrouve les fiches Back/Front réellement présentes.
- `state.card_root_path`/`architect_brief_path` : déduits de
  `specs/<run-id>/{card-root,architect-brief}.md` s'ils existent, sinon
  overridables via `--card-root`/`--architect-brief`.
- `--phase` est **obligatoire, jamais déduit** : c'est le seul champ qui
  détermine la branche de comportement d'un node (ex. Back en Phase.STUBS vs
  Phase.IMPLEMENTATION) sans qu'aucun signal sur disque ne permette de la
  reconstituer de façon fiable.

**Limite documentée, assumée plutôt que contournée** :
`state.agent_card_metadata[role]["existing_files_to_read"]` (contexte fichiers
existants pour Back/Front/Test, voir chantier "Fiches PM en sortie structurée"
ci-dessus) provient du `structured_output` du PM en phase 3 et **n'est persisté
nulle part sur disque** en dehors du checkpoint — non reconstructible par scan
de fichiers. Exposé en CLI via `--existing-file` (répétable), vide par défaut
(dégradé silencieux si omis : l'agent perd juste ce contexte, ne plante pas).
Même logique pour `state.branch_name` (agent `closer`, pas déductible sans
checkpoint) : exposé via `--branch-name`.

**Erreurs du node affichées proprement, pas de traceback** : `RuntimeError`,
`KeyError`, `FileNotFoundError`, `TimeoutError`, `ValueError`, `TypeError`
levées par `<node>.run` sont interceptées et affichées en rouge — cohérent avec
l'usage prévu (diagnostiquer un agent en isolation, pas untooling interne).
Le dict `updates` retourné par le node (jamais un `StudioState` complet malgré
la docstring des nodes — tous retournent un dict partiel, vérifié en pratique)
est affiché tel quel, clé par clé — dump brut plutôt qu'un résumé formaté,
volontairement : cet outil formalise exactement le geste ad hoc de
`~/resume_run.py` (inspection directe de l'état), pas une UX finie.

16 tests ajoutés (`test_cli.py`, section « run-agent ») : jamais d'appel à
`build_graph` (garde-fou explicite), découverte des fiches sur disque,
`--card`/`--card-root`/`--architect-brief`/`--existing-file`/`--branch-name`,
dispatch vers le bon node pour les 9 valeurs d'agent, prompt interactif de
l'objectif pour `pm` si `--objective` omis, affichage du dict `updates`, erreur
de node (KeyError réel, phase non gérée par l'Architecte) affichée sans
traceback. **223/223 au total sur `runtime/tests/`** (était 207).

**Livré (2026-07-14, complément) — `--reference-dir` : distinguer une fiche
d'entrée mal lue d'une fiche de sortie mal produite.** Besoin exprimé par
l'utilisateur après les sessions de test manuel : impossible de savoir si un
échec venait de l'agent producteur (mauvaise fiche en sortie) ou de l'agent
suivant (mauvaise lecture d'une fiche pourtant correcte). Réponse : fournir en
entrée une fiche connue comme bonne (déjà possible via la découverte sur
disque/`--card`), lancer l'agent, et comparer la fiche qu'il produit à la
fiche qu'un run de référence avait produite au même point de la chaîne.

- Nouvelle option `--reference-dir <chemin>` sur `run-agent`. Après l'appel du
  node, chaque chemin de `AgentResult.output_files` (déjà renseigné par tous
  les nodes producteurs de fiche/fichier — aucune donnée nouvelle à faire
  remonter) est comparé au fichier de même chemin relatif sous
  `reference-dir` (structure miroir du repo cible, ex.
  `<reference-dir>/specs/<run-id>/back.md`).
- **Décision utilisateur (2026-07-14) : diff texte exact** pour ce premier
  jet, pas de comparaison sémantique/structurelle. Assumé comme limite :
  Sonnet/Qwen reformulent différemment d'un appel à l'autre (variance déjà
  documentée plus haut dans ce fichier, ex. points 9/11) — un diff peut donc
  signaler un écart sur du contenu par ailleurs correct. Pas de comparaison
  structurelle (agent_sequence, files_to_create/forbidden/dependencies)
  envisagée pour l'instant : hors périmètre de cette itération, à reconsidérer
  si le diff texte s'avère trop bruyant en pratique.
  `difflib.unified_diff` (stdlib, aucune dépendance ajoutée).
- Cas couverts : identique (✓), diffère (✗ + diff unifié), fichier de
  référence absent (avertissement, pas un échec), fichier produit introuvable
  sur disque (cas anormal, avertissement), aucun `output_files` pour cette
  invocation (ex. `architect` en phase 5 sans écart détecté : rien à
  comparer, note explicite plutôt qu'un silence ambigu).
- **Limite connue, non résolue à ce stade** : l'annotation de feedback que
  l'Architecte ajoute sur la fiche d'un agent fautif (phase 5,
  `append_feedback`, modification en place d'un fichier existant) n'est pas
  couverte — l'Architecte ne la déclare pas dans son propre
  `AgentResult.output_files` (`_run_audit_stubs` passe `[]`, voir
  `nodes/architect.py`). Comparer ce texte de feedback nécessiterait un
  mécanisme séparé si le besoin se confirme.
- Toujours pas de code de sortie non-nul en cas de divergence (cohérent avec
  le reste de `cli.py`, qui n'utilise cette convention nulle part — un run
  scriptable/CI sur le résultat de la comparaison resterait à faire si
  nécessaire).
- 5 tests ajoutés (`test_cli.py`) : identique, diffère (contenu du diff
  vérifié), référence absente, aucun fichier produit, comportement par défaut
  inchangé sans `--reference-dir`. **228/228 au total sur `runtime/tests/`**
  (était 223).

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

5. **Interdiction Write/Edit du point 4 trop étroite — le même agent a rebondi sur
   `Bash`** (bug de prompt, pas de code) : après reprise manuelle de
   `run-20260710-185636` sur le nœud Architecte (voir Backlog ci-dessous pour comment),
   nouvel échec : `RuntimeError: Claude Code CLI s'est vu refuser l'accès à un outil
   (Bash)`. Cause : le point 4 n'interdisait explicitement que Write et Edit — la seule
   mention de Bash dans `prompts/architect.md` était la phrase comportementale « Tu
   n'exécutes pas de commandes shell », qui décrit ce que l'agent produit, pas une
   interdiction d'utiliser son propre outil Bash. Sonnet a exploré le repo cible avec
   Bash (probablement `ls`/`find`/`grep` shell plutôt que les outils dédiés) plutôt que
   de s'en tenir à Read/Glob/Grep. Fix : généralisation de l'interdiction dans les trois
   prompts (`architect.md`, `security.md`, `pm.md`) — au lieu d'énumérer Write/Edit,
   interdiction de **tout outil de mutation**, avec Read/Glob/Grep comme seule liste
   blanche explicite. Choisi plutôt que d'ajouter Bash à la liste noire pour éviter un
   troisième aller-retour si un futur outil de mutation (autre que Write/Edit/Bash)
   apparaît côté Claude Code CLI. Même limite qu'au point 4 : pas de test de régression
   automatisé possible, seule la vérification empirique fait foi.

Run relancé après ces cinq corrections — **échec identique** (refus Bash), malgré le
fix généralisé du point 5. Diagnostic complémentaire (2026-07-10) : reproduction directe
du prompt exact envoyé par `architect.py::_call_architect` (system prompt + skills
injectés + user prompt de la phase 2, mêmes fichiers réels que le run) via `claude -p`
en dehors du runtime, répétée plusieurs fois. Sur 3 appels exploitables, **aucun n'a
déclenché de refus d'outil** — brief produit proprement à chaque fois avec le prompt du
point 5. Conclusion : le fix fonctionne dans la majorité des cas ; l'échec Bash constaté
juste après le commit `95bbb45` est très probablement de la **variance d'échantillonnage
du modèle** (Sonnet choisit parfois un raccourci shell malgré l'instruction, de façon non
reproductible à prompt identique) plutôt qu'un défaut de prompt résiduel. Cette
diagnostic a épuisé le quota de session Claude Code CLI du compte (`429`, « session
limit · resets 12:20am Europe/Paris ») — aucune nouvelle tentative de run possible avant
ce reset, indépendamment du code.

**Implication pour la suite** : si l'échec Bash se reproduit après le reset malgré un
prompt déjà correct dans la majorité des cas, la vraie question n'est plus le libellé du
prompt (déjà au maximum de généralité raisonnable) mais le traitement de
`permission_denials` dans `run_claude_code` (`tools/claude_code.py:109-116`) — actuellement
**tout** refus est fatal, même si le modèle a fini par produire un `result` exploitable
malgré le refus (non vérifié faute d'avoir capturé un cas réel de refus avant la limite
de session). Piste à évaluer avec l'utilisateur avant de coder : ne faire échouer le run
que si `result` est vide/invalide après un refus, pas systématiquement dès qu'un refus a
eu lieu — traiter le refus comme un signal à logger, pas comme un échec en soi, tant que
l'agent a fini par respecter le contrat de sortie. Décision à valider avant implémentation
(changement de comportement de sécurité, pas un simple fix de prompt).

**Suite (2026-07-11, après reset du quota)** : run repris avec succès jusqu'à la phase 3
— confirmation de l'hypothèse de variance du point ci-dessus, aucun nouveau refus d'outil.
Déroulé exact, conforme au design deux-passes documenté dans `pm.py::_run_fiches` :
1. `devaimazing resume run-20260710-185636 --project demo-todo-app` (1er appel) : phase 2
   (Architecte) puis phase 3 1re passe (PM génère les fiches `back.md`, `back-tu.md`,
   `test.md`, `secu.md`) — checkpoint avant création de branche, comme prévu.
2. 2e appel `resume` : progression bien plus loin que prévu en une seule invocation
   (branche créée, phase 4 stubs, phase 5 audit stubs Architecte) — jusqu'à un nouveau
   bug réel (point 6 ci-dessous).

6. **Fiches PM (phase 3) sans section `## Feedback`** (bug de code + prompt) : l'Architecte,
   en phase 5 (audit des stubs), a détecté un écart sur `back.md` et tenté de l'annoter via
   `append_feedback` — `ValueError: La fiche .../back.md ne contient pas de section
   '## Feedback'`. Cause : `prompts/pm.md` dit d'« utiliser le template
   `card-agent.md.template` » pour la phase 3, mais sans indiquer que la section
   `## Feedback` est un contrat technique obligatoire (dont dépend `append_feedback`,
   `filesystem.py:59-98`) plutôt qu'une simple suggestion de structure — Sonnet a produit
   des fiches reformulées librement (contenu pertinent, bien structuré) mais sans cette
   section, un écart non détecté avant la phase 5, bien après l'écriture des fiches.
   Fix à deux niveaux :
   - **Prompt** (`prompts/pm.md`) : section `## Feedback` explicitement décrite comme
     contrat obligatoire, distincte du reste (librement adaptable).
   - **Code** (`nodes/pm.py::_run_fiches`) : validation de la présence de `## Feedback`
     dans chaque fiche générée, **avant** l'écriture sur disque (pas seulement au moment
     où l'Architecte en aurait besoin, potentiellement 2 phases plus tard) — échec net et
     atomique (aucune fiche écrite si une seule est non conforme) avec message actionnable
     référençant `prompts/pm.md`. Contrairement aux bugs 4/5 (comportement de modèle non
     testable), celui-ci a un test de régression : `test_fiches_missing_feedback_section_
     raises_runtime_error` (`test_pm_node.py`), vérifié rouge sans le fix (RuntimeError non
     levée, fiches écrites sur disque) avant d'être committé.
   - **Données déjà produites** : les 4 fiches déjà écrites par le run réel
     (`~/code/aimazing/demo-todo-app/specs/run-20260710-185636/{back,back-tu,test,secu}.md`)
     patchées manuellement (section `## Feedback` ajoutée) pour permettre la reprise du
     run sans regénération ni nouvel appel Claude Code CLI.

7. **Producteurs Qwen (Back/Front/Test) sans le contenu réel des fichiers à modifier**
   (gap d'architecture, pas un simple bug de prompt) : après le fix du point 6, l'audit
   des stubs (phase 5) a détecté un écart réel et légitime — le stub produit par Back pour
   `backend/main.py` remplaçait le pattern SQLite natif (`get_connection()`) par
   SQLAlchemy + `Depends`, incompatible avec `database.py`. Feedback annoté correctement
   (pipeline producteur/auditeur fonctionnel, voir `ARCHITECTURE.md` principe 4). Mais les
   2 tentatives de correction suivantes (itérations 2 et 3) ont échoué avec le **même**
   type d'erreur — pas du bruit d'échantillonnage : Qwen reconstruisait `backend/main.py`
   de mémoire (`from fastapi import APIRouter` au lieu de `FastAPI`, tous les handlers
   existants réécrits) au lieu d'éditer chirurgicalement, malgré la fiche qui dit
   explicitement « Conserver tous les imports existants ». Cause racine : `backend.py`,
   `frontend.py` et `test.py` construisaient tous les trois leur `user_prompt` avec
   uniquement le texte de la fiche (`user_prompt=card_content`) — jamais le contenu réel
   du fichier à modifier. Pour une tâche de création pure ça suffit ; pour une
   modification, l'agent (contexte limité, 7B local) n'a que la description prose de
   l'existant et invente le reste. Run relancé, confirmé `FAILED`
   (`back` a atteint `max_iterations=3`) avant de corriger — comportement attendu, pas un
   bug de `max_iterations_exceeded`.
   Fix : nouvelle fonction `tools.filesystem.read_referenced_files(repo_path, text)` —
   détecte par regex les chemins entre backticks avec extension reconnue dans le texte
   d'une fiche, lit le contenu de ceux qui existent réellement sur disque (ignore
   silencieusement les chemins qui n'existent pas, cas normal pour les fichiers à créer),
   retourne un contexte concaténé. Câblée dans `backend.py`, `frontend.py`, `test.py` :
   le `user_prompt` envoyé à Ollama devient `{contexte fichiers existants}\n\n---\n\n
   {fiche}` au lieu de la fiche seule. `security.py` non concerné : c'est un auditeur
   Sonnet via Claude Code CLI, qui peut déjà lire le repo lui-même via Read/Glob/Grep.
   6 tests de régression ajoutés (3 au niveau `filesystem.py`, 1 par node producteur),
   vérifiés rouges sans le fix (désactivation temporaire dans `backend.py`, confirmé
   échec, restauré) avant de committer.
   **Le run réel `run-20260710-185636` reste en `FAILED`** (le fix ne rétroagit pas sur
   un run déjà terminé) — décision à prendre avec l'utilisateur pour le débloquer
   (reprise manuelle du state vs nouveau run propre avec le fix en place).
   **Décision utilisateur (2026-07-11)** : nouveau run propre plutôt que réanimation
   manuelle du state — plus simple et plus représentatif d'un usage réel futur. Le run
   `FAILED` reste dans l'historique comme trace du bug.

8. **Nouveau run (`run-20260710-234216`), échec immédiat en phase 1 (PM)** : même
   refus d'outil (`Bash`), cette fois dans `_run_cadrage` — alors que `prompts/pm.md`
   contient déjà l'interdiction généralisée depuis le commit `95bbb45`. Confirme la
   conclusion du diagnostic du point 5 : le refus revient par variance
   d'échantillonnage du modèle, indépendamment de la qualité du prompt, sur n'importe
   lequel des 3 agents Sonnet (architect, pm, et probablement secu). Plutôt que de
   continuer à durcir des prompts déjà corrects, décision prise avec l'utilisateur de
   traiter la question laissée ouverte au point 5 : **un refus d'outil n'est plus
   automatiquement fatal dans `run_claude_code`** (`tools/claude_code.py`) — il ne
   l'est que si le modèle n'a produit aucun contenu exploitable (`result` vide) après
   le refus. Constat empirique motivant ce changement : un modèle qui tente un outil
   refusé s'en remet normalement dans la même invocation et produit quand même une
   réponse texte valide (comportement standard d'un agent Claude Code face à un refus
   d'outil — l'inverse aurait dû être vérifié avant de coder l'ancien comportement
   strict, voir Notes mises à jour de `run_claude_code`). Le refus reste tracé via
   `logging.warning` même non fatal, pour garder un signal si un prompt donné dérive
   de façon récurrente. Test existant `test_run_claude_code_permission_denial_raises_
   runtime_error` remplacé par deux tests distincts (avec contenu → pas d'exception ;
   sans contenu → exception), vérifiés rouge/vert avant de committer.

9. **Nouveau run (`run-20260710-234844`) après le fix des points 7/8 : Back produit un
   contenu correct mais mal délimité** (bug de prompt) : conséquence positive du fix du
   point 7 — le stub `backend/main.py` généré par Back est désormais correct (bons
   imports `FastAPI`/`get_connection`/`TodoCreate`, handlers existants préservés,
   nouvel endpoint conforme à la fiche). Mais 2 tentatives consécutives (itérations 1
   et 2) ont échoué à produire le délimiteur `<<<DEVAIMAZING_FILE...>>>` attendu,
   utilisant à la place de simples balises ` ``` ` markdown — `parse_agent_file_blocks`
   ne reconnaît rien, statut `feedback_sent` à chaque fois. Root cause identifiée par
   inspection directe de la fiche : la section "Spécification complète du fichier
   final" (générée par le PM) affiche elle-même le code cible entre balises ` ``` `
   classiques — Qwen (7B, imitatif) reproduit très probablement ce format qu'il vient
   de lire dans son propre prompt plutôt que le contrat `<<<DEVAIMAZING_FILE>>>` du
   system prompt. Fix : renforcement explicite de `prompts/backend.md`,
   `prompts/frontend.md`, `prompts/test.md` — avertissement direct sur ce risque
   d'imitation, juste après la définition du format attendu. **Pas de test de
   régression automatisé possible** (comportement de modèle face à un prompt).
   Intervention manuelle complémentaire : la section `## Feedback` de `back.md`
   contenait déjà 2 tentatives ratées, chacune avec ses propres balises ` ``` ``` —
   nettoyée (remise à `_Aucun feedback pour l'instant._`) avant la 3e et dernière
   tentative (`max_iterations=3`), pour ne pas réinjecter le mauvais pattern dans le
   contexte de la prochaine tentative. Le compteur d'itérations n'est pas affecté (basé
   sur `state.agent_results`, pas sur le contenu du fichier).
   **Le prompt renforcé n'a pas suffi** : la 3e tentative a échoué exactement de la même
   façon (contenu correct, mêmes balises ` ``` ` markdown). 3/3 échecs identiques malgré
   deux niveaux de renforcement de prompt → conclusion : limite de pilotabilité du
   modèle local (Qwen 2.5 7B) sur ce point précis, pas un problème de formulation.
   `run-20260710-234844` confirmé `FAILED` (`back`, max_iterations).

10. **Fallback parser pour le délimiteur de fichier** (fix de code, décidé avec
    l'utilisateur suite au point 9 plutôt que de continuer à espérer un meilleur
    prompt) : `tools/filesystem.py::parse_agent_file_blocks` accepte maintenant un
    paramètre optionnel `fallback_path` — si aucun bloc `<<<DEVAIMAZING_FILE>>>`
    n'est trouvé mais que la sortie contient un **unique** bloc de code balisé ` ``` `
    markdown standard, ce bloc est associé à `fallback_path` plutôt que de lever
    `ValueError`. Le repli ne s'applique que si l'appelant (le node) peut déterminer
    sans ambiguïté le chemin attendu — nouvelle fonction `extract_file_paths(text)`
    (chemins entre backticks avec extension reconnue, réutilisée par
    `read_referenced_files` en interne) appliquée au contenu de la fiche : si elle ne
    référence qu'un seul chemin de fichier, il devient `fallback_path` ; sinon (zéro ou
    plusieurs chemins candidats), pas de repli, comportement inchangé. Câblé dans
    `backend.py`, `frontend.py`, `test.py`. Aucune devinette en cas d'ambiguïté (0 ou
    plusieurs blocs ` ``` `, ou plusieurs chemins candidats dans la fiche) — le
    comportement strict d'origine (échec net, `feedback_sent`) reste la valeur par
    défaut dans tous les cas ambigus. 8 tests de régression ajoutés (6 sur
    `filesystem.py`, 1 sur `backend.py` couvrant le cas représentatif des 3 nodes),
    vérifiés rouges sans le fix avant de committer.

11. **Nouveau run (`run-20260711-010821`), même fiche racine que le run précédent
    réussi : le PM choisit une séquence différente et non conforme à sa propre
    doc** (variance de modèle, pas un bug de code) : `card-root.md` quasi identique
    au run précédent (`run-20260710-234844`, réussi jusqu'à cette phase). Le run
    précédent avait produit la séquence `back, back-tu, test, secu` (4 fiches
    distinctes, une par responsabilité) ; celui-ci a produit `back` seul — une
    unique fiche demandant à Back de gérer `backend/main.py` **et**
    `tests/unit/backend/test_main.py` **et** un nouveau fichier d'intégration en un
    seul appel. `prompts/pm.md` documente pourtant explicitement « Feature backend
    only : back → back-tu → test → secu » pour ce type de run. Conséquence : Qwen
    (7B), surchargé, n'a traité qu'un fragment du premier fichier (le handler seul,
    pas le fichier complet) et n'a pas touché aux 2 autres — `feedback_sent`. Le
    fallback du point 10 ne s'applique pas ici à raison (3 chemins candidats dans
    la fiche → ambigu, pas de devinette). `state.agent_cards` étant déjà figé pour
    ce run, un simple `resume` ne corrige pas la séquence (rejoue seulement `back`
    avec la même fiche surchargée) — nécessiterait soit une chirurgie manuelle de
    l'état (aupdate_state, risqué, non testé), soit un nouveau run. **Décision avec
    l'utilisateur** : ne pas patcher au cas par cas cette fois — cette variance,
    combinée aux points 5/8 (refus d'outil) et 9 (délimiteur ignoré), révèle un
    problème de méthode plus large : trois contrats de sortie différents
    (`SEQUENCE:` texte libre, refus d'outil silencieusement toléré, délimiteur
    `<<<DEVAIMAZING_FILE>>>` texte libre) reposent tous sur « demander au modèle en
    prose et re-parser après coup », sans contrainte structurelle. Voir chantier
    prioritaire ci-dessous plutôt qu'un fix ponctuel de plus.

## Chantier prioritaire (ajouté 2026-07-11) : sortie structurée pour tous les agents

Repenser le flux d'entrée/sortie des agents (PM, Architecte, Sécu via Claude Code
CLI ; Back, Front, Test via Ollama/Qwen) pour remplacer le pattern actuel
« instruction en prose + parsing regex après coup » par une contrainte
structurelle sur la sortie, chaque fois que c'est possible — MCP, hooks, ou
tool-calling natif, séparément pour les deux familles de modèles :

- **Côté Claude Code CLI** (PM/Architecte/Sécu, actuellement `claude -p
  --output-format json` en sous-process, prompt via stdin, aucun `--mcp-config`
  ni hook configuré) : un serveur MCP custom exposant des outils à schéma
  contraint (ex. `submit_sequence(agents: enum[...])`) pourrait remplacer la
  ligne `SEQUENCE:` texte libre ; à vérifier si `--mcp-config` fonctionne en mode
  `-p` non-interactif/headless, et si `--allowedTools` peut restreindre le modèle
  à n'utiliser QUE cet outil.
- **Côté Ollama** (Back/Front/Test, actuellement `ollama.AsyncClient.chat()`,
  contrat de sortie par délimiteurs texte `<<<DEVAIMAZING_FILE>>>`) : à vérifier
  si le package `ollama` supporte le function-calling (`tools=[...]`) ou un
  `format` JSON Schema contraint, et si `qwen2.5:7b-instruct` le supporte de
  façon fiable pour produire un ou plusieurs blocs {path, content} potentiellement
  volumineux (centaines de lignes de code).

**Recherches terminées le 2026-07-11** (relancées après le premier épuisement de
quota à 6h, complétées vers 11h) :

**Côté Claude Code CLI** (agent `claude-code-guide`, sources officielles
`code.claude.com/docs`) :
- `--mcp-config` fonctionne en mode `-p` non-interactif. Mais `--allowedTools` ne
  peut pas bloquer les outils natifs (Read/Write/Bash restent accessibles même en
  ne listant que des outils MCP), et surtout **rien ne force le modèle à appeler
  un outil MCP plutôt que de répondre en texte libre** — un outil MCP à schéma
  enum ne garantit donc pas le respect de l'enum.
- Les hooks `PreToolUse` fonctionnent en `-p` (pas les `PermissionRequest` hooks),
  mais ne peuvent que bloquer/modifier un appel d'outil, pas forcer le modèle à en
  déclencher un.
- **`--json-schema` existe et est directement utilisable** :
  `claude -p ... --output-format json --json-schema '{...}'` ajoute un champ
  `structured_output` conforme au schéma — sans garantie à 100%, mais plus fort
  qu'un simple regex sur texte libre.
- Verdict de l'agent : aucune solution ne contraint à 100% côté Claude Code CLI ;
  la voie recommandée est `--json-schema` + validation post-hoc qui rejette et
  rejoue si non conforme (proche de ce que fait déjà le runtime avec ses
  `RuntimeError`, mais sur une sortie mieux structurée en amont).

**Côté Ollama/Qwen** (agent général, sources officielles `docs.ollama.com` +
issue GitHub `ollama/ollama#7051`) :
- Le package `ollama` supporte le **function-calling** (`tools=[...]`, depuis
  juillet 2024) ET un paramètre **`format`** distinct pour du **structured output
  contraint par JSON Schema** (depuis Ollama ≥0.5, décembre 2024) — grammar-
  constrained decoding, sortie **syntaxiquement garantie conforme au schéma**.
- Piège documenté (`ollama/ollama#7051`, non résolu) : `qwen2.5:7b-instruct`
  hallucine des champs avec le **function-calling** sur des schémas à champs
  optionnels imbriqués complexes — spécifique au tool-calling, pas au `format`.
- Recommandation de l'agent : utiliser `format` (pas `tools`) avec un schéma
  **simple, sans champs optionnels imbriqués** — ex.
  `{"files": [{"path": str, "content": str}]}` — directement applicable au
  contrat actuel `<<<DEVAIMAZING_FILE>>>`. Élimine structurellement la classe de
  bug observée aujourd'hui (balises ``` markdown au lieu du délimiteur) : il n'y
  a plus de format libre à respecter, la sortie est syntaxiquement valide par
  construction.
- Limite connue : le masquage de tokens invalides (grammar-constrained decoding)
  n'est pas parallélisé GPU — plus le schéma/la sortie est complexe/longue, plus
  la génération peut ralentir. Pas de mesure chiffrée disponible comparant
  fiabilité structured-output vs délimiteurs texte pour un 7B local.

**Recommandation de priorité** : **Ollama d'abord** (`format` structured output
pour Back/Front/Test) — mécanisme à plus haute confiance (contrainte
syntaxique réelle, pas juste indicative) et corrige directement le bug du
point 9/11 constaté aujourd'hui, avec un schéma simple qui évite le piège
documenté. **Claude Code CLI ensuite** (`--json-schema` pour la ligne
`SEQUENCE:` du PM et le format `STATUT:`/`AGENT:`/`FEEDBACK:` de l'Architecte)
— amélioration réelle mais moins garantie, à traiter comme un chantier
séparé une fois le côté Ollama validé en pratique.

**Implémenté le 2026-07-11** (côté Ollama uniquement, décision explicite de
l'utilisateur — "maintenant") :

- `tools/ollama.py` : nouveau paramètre `response_format` sur `run_ollama`,
  passé à `client.chat(..., format=response_format)`. Nouvelle constante
  `FILE_OUTPUT_SCHEMA` (`{"files": [{"path", "content"}], "blocked_reason"}`).
  Vérifié contre la signature réelle du client installé (`ollama==0.6.2`,
  `format: Literal['', 'json'] | dict[str, Any] | None` — largement au-dessus
  du minimum ≥0.5 requis pour le JSON Schema arbitraire).
- `tools/filesystem.py` : nouveau `parse_structured_file_output(content)` —
  parse le JSON, retourne `(files, blocked_reason)` ; lève `ValueError` si le
  JSON est invalide ou incomplet (garde-fou, la contrainte de schéma n'est pas
  supposée à 100 %, cohérent avec le verdict des deux recherches).
- `nodes/backend.py`, `frontend.py`, `test.py` : appellent `run_ollama` avec
  `response_format=FILE_OUTPUT_SCHEMA`, parsent via
  `parse_structured_file_output`. `blocked_reason` non vide (ou `files` vide)
  remplace l'ancien "aucun bloc reconnu" comme déclencheur `feedback_sent` —
  échappatoire structurée au lieu de texte libre à parser. Le mécanisme de
  repli `fallback_path`/`extract_file_paths` du point 10 (délimiteur ` ``` `
  toléré si un seul fichier attendu) est retiré de ces trois nodes : devenu
  inutile, la sortie structurée élimine la classe de bug par construction —
  `parse_agent_file_blocks` et son paramètre `fallback_path` restent dans
  `filesystem.py` (toujours utilisés par `pm.py`/`architect.py`, côté Claude
  Code CLI, hors périmètre de ce chantier).
- `prompts/backend.md`, `frontend.md`, `test.md` : section "Format de sortie"
  réécrite — contrat `{"files": [...], "blocked_reason": ""}`, plus de
  délimiteur `<<<DEVAIMAZING_FILE>>>` à décrire ni de mise en garde contre
  l'imitation des balises ` ``` ` (devenue sans objet).
- Tests : 4 nouveaux dans `test_ollama.py`/`test_filesystem.py`
  (`response_format` transmis, `parse_structured_file_output` succès/erreurs) ;
  les 3 fichiers de tests de nodes réécrits intégralement (fixtures JSON au
  lieu des blocs `<<<DEVAIMAZING_FILE>>>`, nouveaux tests `blocked_reason`,
  `malformed_json`, `calls_ollama_with_structured_output_schema` ; suppression
  du test de repli désormais obsolète côté `backend.py`). 185 tests verts au
  total (was 174). Vérifié rouge sans le fix sur les trois fichiers modifiés
  (`ollama.py`, `filesystem.py`) avant de committer.

**Non fait dans ce commit, volontairement hors périmètre** : le contrat de
sortie du PM (ligne `SEQUENCE:`) et de l'Architecte (`STATUT:`/`AGENT:`/
`FEEDBACK:`), tous deux côté Claude Code CLI — chantier séparé (`--json-schema`),
pas commencé, cohérent avec la recommandation de priorité ci-dessus. **Mise à
jour (2026-07-14)** : le côté PM est fait (voir "Priorité immédiate" en tête de
document, chantier 1, livré) ; le côté Architecte (`STATUT:`/`AGENT:`/`FEEDBACK:`)
reste non traité.

**Vérifié en conditions réelles le 2026-07-11** (`run-20260711-101842`,
serveur Ollama local confirmé supporter `format` via un appel `curl` direct
à `/api/chat` avant de relancer le run) :
- **Structured output confirmé fonctionnel en pratique** : Back a produit,
  en un seul appel, 4 fichiers corrects (`backend/main.py`,
  `tests/integration/backend/__init__.py`,
  `tests/integration/backend/test_complete_flow.py`,
  `tests/unit/backend/test_main.py`) — alors que la même fiche (PM regroupant
  à nouveau tout dans un seul agent "back", cf. point 11) avait fait échouer
  l'ancien contrat par délimiteurs (1 seul fichier partiel produit, 2 autres
  ignorés). Le refus d'outil Bash côté PM (variance déjà documentée au point
  5/8) s'est aussi reproduit, mais n'a plus fait échouer le run grâce au fix
  du point 12 (non fatal si contenu exploitable) — juste un warning loggé.

12. **`architect-brief.md` introuvable en phase 5** (bug de code réel, trouvé
    pendant cette vérification) : le repo était resté sur la branche d'un run
    précédent (`studio/test-todo-49de4`) au moment de lancer ce nouveau run.
    Les commits de phase 1/2 (`card-root.md` non commité en fait — seul
    `architect-brief.md` l'est, via `commit_as_agent` en phase 2) ont atterri
    sur cette branche stale, puisque `create_run_branch` (qui bascule sur
    `base_branch`) n'est appelée qu'en **fin** de phase 3 — rien ne garantit
    que le repo est sur `develop` avant. Quand la phase 3 a ensuite basculé
    sur `develop` pour créer la nouvelle branche du run, `architect-brief.md`
    (tracké, commité sur l'ancienne branche, absent de `develop`) a été
    supprimé du working tree par le `checkout` — `architect.py::
    _run_audit_stubs` a levé `FileNotFoundError` en le cherchant.
    Root cause : rien dans `devaimazing run` ne garantit que le repo cible
    est sur `base_branch` avant que la phase 1 démarre — seule la phase 3
    (fin) s'en préoccupe, trop tard pour les commits des phases 1/2.
    Fix : nouvelle fonction `tools/git.py::checkout_branch(repo_path, branch)`,
    appelée dans `cli.py::_run_async` (nouveau run uniquement, jamais
    `_resume_async` — un run repris est potentiellement déjà sur sa propre
    branche de feature, y forcer un checkout serait destructeur) juste avant
    `build_graph`, donc avant toute activation de phase 1. 3 tests de
    régression (`test_cli.py`), vérifiés rouges sans le fix avant de
    committer. **`run-20260711-101842` reste cassé** (le fix ne rétroagit
    pas) — récupération manuelle à faire séparément.

**Backlog identifié en marge (2026-07-10, pas bloquant, pour plus tard)** :
`devaimazing resume` (`cli.py::resume`) ne sait reprendre qu'un run explicitement en
attente d'une validation humaine (`awaiting_human_validation=True` dans le state
checkpointé) — pas un run interrompu au milieu d'un nœud (crash, `kill`, coupure).
Constaté en pratique après le bug 4 : `run-20260710-185636` s'est arrêté en
`IN_PROGRESS`/phase `AUDIT_AMONT` (crash dans le nœud Architecte, pas un checkpoint
volontaire) — `resume` refuse ce cas avec « n'est pas en attente de validation », alors
que LangGraph sait très bien rejouer le nœud interrompu via `graph.ainvoke(None,
config=thread_config)` sur le même `thread_id` (vérifié manuellement, hors CLI, pour
reprendre ce run précis sans repasser par le dialogue PM de la phase 1).

**Détail des deux options (2026-07-14, demandé par l'utilisateur)** :

- **Option A — assouplir `resume`** pour accepter aussi `status == RunStatus.IN_PROGRESS`
  sans validation en attente. Un seul command à retenir, mais confond deux
  situations sémantiquement différentes : reprise après validation humaine
  délibérée (état stable, connu, explicitement approuvé) vs reprise après crash
  (état potentiellement incohérent — écriture de fichier interrompue en plein
  milieu, appel Ollama/Claude Code coupé net). Traiter les deux pareil fait
  disparaître un signal de sécurité : plus aucune visibilité qu'un crash a eu lieu
  avant de rejouer.
- **Option B — commande dédiée `devaimazing retry <run-id>`**. Distingue
  explicitement les deux cas ; pourrait afficher un diagnostic avant de rejouer
  (quel node a planté, quand, présence d'un état partiel suspect). Formalise
  exactement le geste répété manuellement tout au long de la session du
  2026-07-11 (`~/resume_run.py`, appelé à chaque crash de node) — déjà validé
  empiriquement comme fonctionnel, juste pas industrialisé. Coût : une commande
  de plus à maintenir.
- Ne pas confondre avec `run-agent` (chantier 2 ci-dessus) : ce dernier est un
  outil de test isolé qui ne touche jamais au checkpoint (décision du
  2026-07-14) — il ne résout donc pas ce point, qui reste un chantier séparé si
  traité.
- **Décision utilisateur (2026-07-14) : option B**, commande dédiée
  `devaimazing retry <run-id>`.

**Livré (2026-07-14).** `devaimazing retry <run-id> --project <project>`
(`cli.py::retry`/`_retry_async`) : cible spécifiquement un run `status ==
RunStatus.IN_PROGRESS` avec `awaiting_human_validation == False` (le cas
crash, distinct de `resume`). Refus avec message orienté dans les autres cas :
`awaiting_human_validation == True` ou `status == WAITING_HUMAN` → invite à
utiliser `resume` ; tout autre statut (`COMPLETED`/`FAILED`/`PARTIAL`/
`PENDING`) → « rien à rejouer ». Si éligible, affiche un diagnostic à partir
des champs déjà existants de `StudioState` (aucun champ ajouté, pas
d'horodatage — décision actée avant implémentation, `AgentResult` n'a pas de
timestamp) : phase courante, agent courant (`agent_sequence[current_agent_
index]`, "inconnu" si l'index est hors bornes), statut, dernier
`AgentResult`, et `intervention_reason` si `requires_manual_intervention` est
vrai. Demande une confirmation interactive (`click.confirm`, défaut non)
avant de rejouer — décision actée : contrairement à `resume`, le risque
d'état incohérent après un crash (écriture de fichier interrompue, appel LLM
coupé net) justifie un arrêt bloquant. Si confirmé : `graph.ainvoke(None,
config=thread_config)` (pas d'`aupdate_state` préalable,
`awaiting_human_validation` déjà `False`), même pattern que `resume` pour la
fermeture de la connexion checkpointer (`finally` + `conn.close()`). 12 tests
ajoutés (`test_cli.py`) : run introuvable, chacun des cas de refus (attente
validation, `COMPLETED`, `FAILED`, `PENDING`), diagnostic affiché avant
confirmation (cas normal + agent hors bornes), confirmation refusée/acceptée,
fermeture de connexion sur les deux chemins, affichage de la raison
d'intervention manuelle. **207/207 au total sur `runtime/tests/`** (était 195).

**Décision prise (2026-07-10, hors code) — reportée en fin de projet (2026-07-14)** :
la mise en production de devaimazing lui-même devra être conteneurisée Podman,
cohérent avec le reste de l'infra prod (voir CLAUDE.md du dépôt parent).
Implications concrètes non câblées à ce stade : Claude Code CLI (actuellement
sous-process supposant `claude` installé sur l'hôte), accès réseau à Ollama
(actuellement `localhost:11434` en dur par défaut, alors qu'un conteneur devra
joindre `dataimazing-ramiris`/son remplaçant via `dataimazing-network`), montage du
repo projet cible en volume, persistance de `state.db`/`metrics.db`. **Décision
explicite de l'utilisateur (2026-07-14) : traiter ce point en toute fin de projet**,
pas avant — aucun travail à engager dessus tant que le reste n'est pas stabilisé.
