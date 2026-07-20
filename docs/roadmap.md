# Feuille de route - devaimazing

**Dernière mise à jour** : 2026-07-20

## État actuel

Le runtime devaimazing est **fonctionnellement complet et testé de bout en bout** :
`state.py`, `config.py`, `tools/*.py` (filesystem, git, ollama, claude_code, tracer),
`graph.py`, les 7 `nodes/*.py` (pm, architect, backend, frontend, test, security,
closer), `metrics.py` et `cli.py` (`run`, `resume`, `retry`, `run-agent`, `runs`,
`metrics`, `new-project`, `projects`, `doctor`) sont tous implémentés — voir
`CLAUDE.md` pour la convention (stub-first reste appliquée par le pipeline aux
projets *cibles*, pas à ce dépôt). **361/362 tests verts** sur `runtime/tests/`
(seul échec : `test_new_project_target_exists_not_git_repo_prints_error`,
préexistant sans rapport, dû au retour à la ligne du terminal dans les
sorties Rich).

**2026-07-20 — bug fondamental d'indexation router/phase_agent_sequence
résolu (explique l'anomalie d'identité git back-tu, chantier 5 de la
journée sur run-20260714-205712).** En dressant un panorama des blocages
de la journée, l'anomalie « `back-tu` commit sous l'identité
`front-aimazing` » (signalée dès le premier chantier du jour comme
inexpliquée) s'est révélée **systématique** : 22 commits `back-tu` sur 22
dans tout l'historique du run, sans exception — pas un aléa.

Cause trouvée et vérifiée directement : `graph.py::router()` et
`routing.py::is_last_agent_of_phase()` indexaient `phase_agent_sequence(
state)` — une sous-liste de `state.agent_sequence` **filtrée** aux rôles
de la phase courante (`PHASE_AGENT_ROLES[Phase.STUBS] = {"back", "front"}`,
2 éléments) — alors que `backend.py`/`frontend.py` résolvent le rôle réel
et incrémentent `current_agent_index` sur `state.agent_sequence`, la
séquence **complète** (6 éléments : `back, back-tu, front, front-tu, test,
secu`). Après que `back` (index 0) termine, `current_agent_index` passe à
1 : `router()` lisait `["back","front"][1]` = `"front"` → dispatchait vers
le node **frontend.py**, alors que `state.agent_sequence[1]` = `"back-tu"`
— le rôle réellement attendu. `frontend.py` résolvait quand même une fiche
et des métadonnées cohérentes (`state.agent_cards["back-tu"]` existe,
lookup par clé dict, indépendant de l'indexation buguée), donc le tour
"fonctionnait" en apparence — juste sous le mauvais prompt système
(`prompts/frontend.md`), sans le skill `non-regression`, avec l'identité
git `front-aimazing` et le préfixe de commit `feat` au lieu de `test`.
Repro minimale :
```python
router(StudioState(current_phase=Phase.STUBS,
    agent_sequence=["back","back-tu","front","front-tu","test","secu"],
    current_agent_index=1)) == "frontend"  # alors que agent_sequence[1] == "back-tu"
```

Explique aussi un symptôme relevé plus tôt sans être relié : la phase
STUBS ne faisait jamais tourner `front`/`front-tu` et sautait direct à
`AUDIT_STUBS` après `back-tu` (`is_last_agent_of_phase` avait le même bug
de comparaison filtrée/complète).

**Corrigé** : `PHASE_AGENT_ROLES[Phase.STUBS]` élargi à `{"back", "back-tu",
"front", "front-tu"}` (reflète la réalité — back-tu/front-tu tournent bien
en phase STUBS) ; `router()`/`is_last_agent_of_phase()` réécrits pour
indexer directement `state.agent_sequence` (jamais de sous-liste filtrée) ;
`phase_agent_sequence()` supprimée (plus aucun appelant) ; correction au
passage du même bug d'indexation dans `architect.py::_run_audit_stubs`
(`stubs_sequence.index(faulty_agent)` → `state.agent_sequence.index(...)`),
noté « hors scope » lors du chantier précédent sans réaliser qu'il s'agissait
de la même cause.

Un test existant (`test_router_stubs_phase_filters_sequence_to_back_and_front`)
encodait le bug lui-même (`current_agent_index=1` → attendait `"frontend"`)
— corrigé pour attendre `"backend"`.

**Non revérifié en conditions réelles** au moment d'écrire cette entrée — à
faire au prochain `resume` de `run-20260714-205712`. Si le diagnostic est
juste, `back-tu` devrait enfin tourner sous le bon prompt/skills, ce qui
peut changer significativement son taux de réussite (une partie de
l'instabilité attribuée à "qwen2.5 est peu fiable" pourrait en réalité
venir de ce mauvais routage).

**2026-07-20 — redo ciblé quand l'Architecte désigne un agent fautif
(chantier 4, dernier des 4 chantiers de la journée sur run-20260714-205712).**
Trouvé en poursuivant les chantiers précédents en conditions réelles :
`retry_scope` n'était alimenté que par `verify_python_files` (chantiers
2/3) — le redo déclenché par l'audit Architecte (`_run_audit_stubs`) ne le
touchait jamais, donc `back` retombait systématiquement en régénération
complète de son périmètre (9 fichiers) à chaque écart signalé par
l'Architecte, même pour un seul fichier en cause. Confirmé en run réel :
`fastapi==0.95.3` (déjà vu et corrigé au chantier 1) est **revenu 3 fois**
via exactement ce chemin, malgré `retry_scope` fonctionnel pour les échecs
`verify_python_files`.

Nouvelle fonction `_extract_feedback_files` (architect.py) : extrait les
chemins de fichiers cités entre backticks dans le texte de feedback de
l'Architecte (ex. `` `backend/schemas.py` ``), résolus contre le périmètre
déclaré de l'agent fautif (`agent_card_metadata[faulty_agent]`,
files_to_create + files_to_modify) — un candidat non résolvable avec
certitude (ex. nom de fichier ambigu, symbole de code sans extension) est
ignoré plutôt que deviné, avec repli sur la régénération complète
existante. Pas de changement du contrat de sortie de l'Architecte
(`prompts/architect.md` inchangé) — extraction depuis le texte déjà produit,
qui cite déjà les chemins entre backticks dans son style actuel.

Bug distinct noté au passage, **non corrigé** (hors scope) : le redo
Architecte fait retraverser tout le groupe de phase (`back` puis `back-tu`)
même quand un seul des deux est fautif, à cause de l'indexation par
sous-séquence de phase appliquée à la séquence complète
(`stubs_sequence.index(faulty_agent)` utilisé comme `current_agent_index`
dans `state.agent_sequence`) — fonctionne par coïncidence pour `back`
(même position dans les deux séquences) mais serait incorrect pour
`front`. À corriger séparément si ça cause un problème observé.

**Non revérifié en conditions réelles** au moment d'écrire cette entrée
(session déjà longue) — à faire au prochain `resume` de
`run-20260714-205712`.

11 tests ajoutés (`test_architect_node.py` : `_extract_feedback_files` +
intégration `_run_audit_stubs` avec/sans fichiers extractibles, non-
régression retry_scope d'un autre agent préservé).

**2026-07-20 — related_files : le mode ciblé couvre la chaîne d'import, pas
seulement le module importé en tête (chantier 3).** Trouvé en relançant le
chantier précédent en conditions réelles : `retry_scope` ne ciblait que le
fichier IMPORTÉ (`backend/crud.py`), pas celui réellement en cause plus
haut dans la chaîne — sur `run-20260714-205712`, un import circulaire réel
entre `models.py` et `database.py` (chacun important `Base` de l'autre)
faisait échouer l'import de `crud.py`, mais le modèle ne voyait jamais
`models.py`/`database.py` en mode ciblé : **6 tours sur 8 ont échoué à
l'identique** avant que ce soit repéré.

`VerifyFailure` gagne `related_files: list[str]` — tous les fichiers du
repo cible mentionnés dans la traceback complète (nouvelle fonction
`_extract_traceback_files`, filtre les pseudo-fichiers `<string>`/`<frozen
importlib._bootstrap>` et tout ce qui est hors `repo_path` — stdlib,
site-packages). `backend.py`/`frontend.py` ciblent désormais `verify_error.
file` **et** tous ses `related_files` au tour suivant, chacun avec le même
message d'erreur complet. Reproduit exactement le bug réel en test
(`models.py`/`database.py` mutuellement importés) — voir
`test_check_imports_circular_import_reports_related_files`.

**Validé en conditions réelles** juste après : le mode ciblé (chantier 2)
fonctionnait déjà correctement (prompt réduit de ~55 000 à ~19 000
caractères, un seul fichier régénéré au lieu de 9) mais bouclait sur le
mauvais fichier faute de `related_files` — ce chantier corrige précisément
ce point, pas encore revérifié en run réel au moment d'écrire cette entrée
(à faire au prochain `resume`).

7 tests ajoutés (`test_pyenv.py` : `_extract_traceback_files` + reproduction
exacte de l'import circulaire réel ; `test_backend_node.py` : retry_scope
avec plusieurs fichiers, prompt ciblé contenant tous les fichiers liés).

**2026-07-20 — correction ciblée après échec verify_python_files (patch
incrémental, chantier 2).** Suite directe des chantiers du même jour : en
conditions réelles sur `run-20260714-205712`, un fix manuel sur
`requirements.txt` (fastapi==0.95.3 → 0.95.2) a été écrasé au tour suivant
parce que `back` régénère l'intégralité de son périmètre (9 fichiers) à
chaque tour, sans savoir qu'un seul fichier était en cause. `tools.pyenv.
verify_python_files` retourne désormais une structure `VerifyFailure(file,
message)` au lieu d'une simple string — le fichier fautif devient une
donnée exploitable, pas juste un fragment de texte. Nouveau champ
`StudioState.retry_scope: dict[str, dict[str, str]]` (rôle → {fichier:
message}) : rempli par `backend.py`/`frontend.py` quand `verify_python_files`
échoue (fichier connu avec certitude), vidé sur succès ou sur blocage de
l'agent lui-même (`blocked_reason`, fichier non identifiable — retour à la
régénération complète dans ce cas). Quand `retry_scope[role]` est non vide,
le node bascule en mode ciblé : prompt = tâche/critères de la fiche (via
nouveau `tools.filesystem.strip_feedback_section`, l'historique de feedback
cumulé est exclu) + contenu actuel du seul fichier fautif + message d'erreur
précis — pas les 8 autres fichiers, pas le feedback obsolète déjà traité.

Explicitement **hors scope** de ce chantier : le cas où c'est l'Architecte
(audit `AUDIT_STUBS`/`AUDIT_AVAL`) qui désigne un `faulty_agent` — son
feedback est du texte libre sans liste structurée de fichiers, en extraire
un scope fiable demanderait de changer le contrat de sortie de l'Architecte.
Seul le cas `verify_python_files` (fichier connu avec certitude) est couvert.

**Non testé en conditions réelles** au moment d'écrire cette entrée — à
vérifier au prochain `resume` de `run-20260714-205712` : le mode ciblé se
déclenche bien, le prompt est effectivement réduit, et ça réduit la
régression observée (fix écrasé par régénération complète).

12 tests ajoutés (`test_pyenv.py` : VerifyFailure structuré ; `test_filesystem.py` :
`strip_feedback_section` ; `test_backend_node.py`/`test_frontend_node.py` :
retry_scope posé/vidé, prompt ciblé, non-régression régénération complète).

**2026-07-20 — vérification syntaxe + import réel avant commit (Back/Front).**
Poursuite de `run-20260714-205712` (todo-list, voir entrée 2026-07-19) : 22
cycles `back`/`back-tu`/audit sur 2 modèles (`qwen2.5:7b-instruct` puis
`qwen2.5:14b-instruct`, testé après le premier) sans converger — les mêmes
bugs (`TaskResponse` absent, `TodoError(HTTPException)`) réapparaissaient
identiquement avec le modèle plus gros, ce qui écartait la capacité du
modèle comme cause unique. `tokens_prompt` restait loin de `num_ctx`
(14432/32768 max) : pas un problème de troncature non plus (`num_ctx` relevé
à 32768 dans la foulée par précaution, todo-list basculé sur 14b via
`config/projects/todo-list.yml`, `models.agents_local`). Cause structurelle
identifiée : `back`/`front` régénèrent l'intégralité de leur périmètre à
chaque tour depuis la fiche + feedback textuel cumulé (245 lignes, 21
entrées sur ce run), sans aucune vérification avant de committer — un
`NameError`/`ImportError` trivial (import manquant, symbole absent d'un
autre fichier) n'était détecté que par l'audit Architecte (Claude Sonnet,
~60-100s, un tour entier perdu par bug).

Ajouté : `tools/pyenv.py` — pour chaque fichier `.py` produit par `back`/
`front` (no-op sur les autres extensions, donc sur `front` qui produit
surtout du `.tsx`) : (1) `ast.parse` (syntaxe, gratuit) puis (2) tentative
d'import réel dans un venv dédié au projet cible
(`~/.devaimazing/venvs/<project>/`, créé au premier besoin, dépendances de
`<backend_dir>/requirements.txt` installées avant chaque vérification,
no-op si absent). Échec (syntaxe ou import) → même chemin que
`blocked_reason` existant (`feedback_sent`, pas de commit, run en attente),
message d'erreur Python natif injecté dans le feedback. Factorisé dans un
helper `_feedback_sent` par node (dupliqué entre `backend.py`/`frontend.py`,
pas de module de nodes partagé actuellement). `test.py` **non modifié** :
il exécute déjà la vraie suite `pytest` juste après écriture (vérification
strictement plus forte, dans le même nœud) — ajouter ce check y serait
redondant.

**Non testé en conditions réelles** : ce correctif n'a pas encore été validé
sur un nouveau `resume` de `run-20260714-205712` (session terminée avant).
À vérifier au prochain run : le venv se crée bien, `pip install` réussit
sans réseau bloquant, et les bugs `NameError`/`ImportError` vus dans ce run
sont bien interceptés avant l'audit plutôt qu'après.

23 tests ajoutés (`test_pyenv.py` : 19 ; `test_backend_node.py`/
`test_frontend_node.py` : 2 chacun, cas échec + non-régression cas succès).

Deux runs réels de bout en bout ont été menés sur des projets cibles distincts
(`demo-todo-app`, `todo-list`) et ont permis de trouver/corriger plusieurs bugs
réels (validation de chemins absolus, connexion SQLite du checkpointer jamais
fermée, dégradation gracieuse du PM en phase Fiches, etc.) — tous résolus (voir
Historique ci-dessous pour le détail).

**2026-07-16 — bug de troncature de contexte Ollama corrigé.** Le run
`run-20260716-095240` (projet `todo-list2`) échouait en boucle sur l'agent
Back avec le même défaut (mauvais formatter de logging JSON) malgré un
feedback de plus en plus détaillé à chaque itération, sur `qwen2.5:7b-instruct`
et `qwen2.5:14b-instruct`. Diagnostic : `tokens_prompt` restait figé à ~2050
alors que `prompt_chars` grossissait à chaque itération (15989 → 23472
caractères) — `runtime/studio/tools/ollama.py` n'appelait jamais
`client.chat(...)` avec `options={"num_ctx": ...}`, donc Ollama retombait sur
son défaut de 2048 tokens et tronquait silencieusement le début du prompt
(system prompt + brief + feedback cumulé). Corrigé : `run_ollama` prend
maintenant un paramètre `num_ctx` (défaut **16384**, transmis via
`options={"num_ctx": ...}`), configurable par `ollama.num_ctx` dans
`config/studio.yml` ; câblé dans les 3 appelants (`backend.py`, `frontend.py`,
`test.py`). 8192 avait été envisagé puis écarté (marge insuffisante : un run
réel a déjà atteint ~6000-6700 tokens de prompt hors complétion). **Validé en
situation réelle** via `devaimazing run-agent todo-list2 run-20260716-095240
back --phase STUBS` : `tokens_prompt=4955` (contre ~2050 plafonné avant),
`status='success'` — plus de blocage sur le formatter JSON. La lenteur
CPU/Ollama qui avait motivé la pause initiale du run reste un facteur séparé,
non résolu par ce correctif.

**2026-07-16 — auto-nettoyage du worktree cible avant checkout (run).** En
relançant `todo-list2` de bout en bout après le fix ci-dessus,
`devaimazing run` échouait avec une erreur Git brute (`git checkout develop`
refusé, modifications non commitées d'un run précédent interrompu en cours de
nœud). `tools.git.checkout_branch` (appelée uniquement par
`cli.py::_run_async`, pas par `resume`) détecte maintenant un worktree sale
avant le checkout et sauvegarde son contenu en un ou plusieurs commits plutôt
que de faire échouer le run : un commit par agent propriétaire identifiable
(fiches via le nom de fichier — `specs/<run-id>/back.md` → `back-aimazing` —
et `trace.jsonl` via le champ `"agent"` de son dernier événement), puis un
commit `devaimazing-bootstrap` pour le reste (fichiers dont l'agent
propriétaire ne peut pas être déduit, ex. code source déjà écrit par un
agent — la vérification de périmètre par fichier, point 2 de "Reste à
faire", permettrait de fermer ce dernier cas). Détection basée sur
`git status --porcelain --untracked-files=all` (nécessaire pour ne pas
regrouper tout un dossier `specs/<run-id>/` jamais commité en une seule
entrée non attribuable). Un bug de parsing a été trouvé et corrigé au passage
en écrivant les tests (real-git, pas de mock) : `_run_git` strippe tout le
stdout, ce qui mangeait l'espace de tête de la première ligne de
`git status --porcelain` et décalait le parsing en position fixe — d'où une
fonction `_dirty_paths` dédiée qui n'utilise pas `_run_git`.

**2026-07-16 — erreurs de service externe affichées proprement au lieu
d'une traceback brute.** En relançant `todo-list2`, un `TimeoutError` Ollama
levé pendant l'exécution d'un node remontait tel quel à travers
LangGraph/httpx/httpcore jusqu'au CLI (`run`/`resume`/`retry`), affichant une
traceback de plusieurs dizaines de lignes au lieu du message déjà clair porté
par l'exception elle-même. Les trois commandes attrapent désormais
`(TimeoutError, ExternalServiceError, RuntimeError)` autour de
`graph.ainvoke(...)` et affichent `str(exc)` proprement (rouge, préfixé du
run_id) plutôt que de laisser planter le process — un `run_end` (`status:
"interrupted"`) est aussi émis dans `trace.jsonl` sur ce chemin, qui n'avait
jusqu'ici qu'un `run_start` sans marqueur de fin. Choix assumé : `RuntimeError`
est large, mais tous les points `raise RuntimeError` actuels du code
(`tools/git.py`, `tools/claude_code.py`, `nodes/pm.py`, `nodes/architect.py`,
`nodes/security.py`) représentent une sortie d'outil/LLM externe mal formée,
jamais un bug interne — si un futur `RuntimeError` interne apparaît, il sera
avalé silencieusement (exit 0) au lieu de remonter ; à surveiller si ça
devient un problème. `run-agent` avait déjà ce garde-fou (trouvé en le
consultant) mais sans `ExternalServiceError` dans sa liste — ajouté aussi.

**2026-07-16 — message "exécution en cours" avant graph.ainvoke (ferme
partiellement le point 4 ci-dessous).** Une fois `run`/`resume`/`retry`
lancé, le terminal restait silencieux (curseur en début de ligne, aucun
retour) tant qu'un agent n'avait pas terminé — plusieurs minutes possible
sur un modèle local en CPU, facilement confondu avec un process figé. Un
message est maintenant affiché juste avant `graph.ainvoke(...)` :
"Exécution en cours... — suivre en direct : tail -f
<repo>/specs/<run-id>/trace.jsonl" (le chemin exact de `tracer.trace_path`).
Couvre la partie "est-ce vivant / comment suivre" du point 4 (visibilité
d'avancement) — reste ouvert : une vraie commande `devaimazing show
<run-id>` qui formaterait ce trace.jsonl plutôt que de le laisser à `tail`
brut. Étendu le même jour à `run-agent` (appelle aussi Ollama/Claude Code
CLI, même silence possible) et `new-project` (message avant `git init` et
avant la création/push GitHub via `gh`, réseau). Délibérément pas appliqué à
`runs`/`metrics`/`projects`/`doctor` : lecture seule ou vérifications
bornées, pas de trou de silence à combler.

**2026-07-16 — raccourci "import de brief existant" (`devaimazing run`).**
Nouveau : au lancement d'un run, si `--objective` n'est pas fourni, un prompt
propose d'importer un document existant plutôt que de refaire le dialogue de
cadrage (phase 1) depuis zéro. Si accepté, le PM (pas l'Architecte) relit le
document directement dans `nodes/pm.py::_run_brief_import` (même contrat de
sortie `QUESTION:`/`FICHE_VALIDEE:` que le dialogue normal, boucle extraite
dans un helper partagé `_run_validation_dialogue` — refactor
comportement-préservant, confirmé par les 22 tests PM existants inchangés).
Décision actée explicitement avec l'utilisateur : le document importé
devient `architect-brief.md` **tel quel** et le run saute **à la fois**
la phase 1 (cadrage PM) et la phase 2 (audit amont Architecte) — pas
seulement le dialogue. Un `card-root.md` minimal est synthétisé en Python
déterministe (`_render_imported_card_root`, nouveau template
`templates/card-root-import.md.template`) pour satisfaire la seule
exigence codée sur ce fichier (`**Nom de la feature**`, requis par
`_extract_feature_name`, utilisé par `_run_fiches`/`_create_branch_and_advance`).
Commit d'`architect-brief.md` sous l'identité `pm` (pas `architect`, qui n'a
jamais tourné dans ce raccourci) — décision assumée, réversible en un mot
si l'usage réel suggère l'inverse.

**Point de vigilance non couvert par les tests** : toute la logique
ci-dessus n'est validée que par des tests avec `run_claude_code` mocké — la
vraie question (est-ce que le PM respecte fidèlement la consigne "commence
ta réponse validée par `**Nom de la feature** : ...`", ajoutée dans
`prompts/pm.md`) ne sera testée que par un run réel. Si le PM omet cette
ligne, `_extract_feature_name` lève une `RuntimeError` **après** que
l'utilisateur ait déjà tapé "oui" pour valider — affiché proprement (pas de
traceback, voir le correctif `_EXTERNAL_SERVICE_ERRORS` du même jour), mais
au pire moment de l'interaction. Pas encore corrigé délibérément (la
solution simple si ça arrive en pratique : redemander le nom via `input()`
au lieu d'échouer dur) — à surveiller au premier usage réel plutôt qu'à
anticiper.

**2026-07-19 — `run-20260714-205712` repris, puis `agent_iteration_count` corrigé
après un vrai échec dur.** Reprise via `devaimazing resume` : le fix `num_ctx`
du 2026-07-16 a confirmé son effet, `back-tu` a réussi dès la 3ᵉ tentative
(fichiers `backend/tests/*` committés). `AUDIT_STUBS` a ensuite légitimement
désigné `back` comme fautif (`TaskResponse` sans le champ `terminé`) ; `back`
a corrigé avec succès au `resume` suivant. Mais le redo de phase STUBS
déclenché par cette non-conformité rejoue **tout le groupe** (`back` et
`back-tu`), pas seulement l'agent fautif — et `back-tu`, déjà à
`agents.max_iterations` (2 `feedback_sent` + 1 `success` cumulés depuis le
début du run), a été bloqué **avant même l'appel Ollama** : le run est tombé
en `FAILED` (`requires_manual_intervention`) alors que `back-tu` n'était pour
rien dans ce tour. Corrigé dans `studio.routing.agent_iteration_count` : pour
les phases à agents multiples (`PHASE_AGENT_ROLES` — STUBS, IMPLEMENTATION),
un résultat `"success"` remet désormais le compteur à zéro, pour ne pas
pénaliser un agent déjà validé quand un redo est causé par un autre membre du
groupe. Les phases à agent unique (SECURITE, TESTS) gardent le comptage
cumulatif d'origine — nécessaire pour Sécu, qui peut légitimement ré-émettre
un rapport `"success"` à chaque reprise humaine tant que des findings
bloquants ne sont pas corrigés (le garde-fou doit continuer à compter ces
tentatives-là). 4 tests de régression ajoutés dans `test_routing.py`
(reset après succès en phase multi-agents, non-régression du comptage
cumulatif en phase mono-agent, non-régression du garde-fou sur boucle
d'échecs réelle). **316/316 tests verts.** Le run reste en `FAILED` dans
`state.db` — aucune commande CLI actuelle (`resume`/`retry`) ne permet de
relancer un run déjà `FAILED` (protection volontaire, voir docstring
`backend.py::run`) ; un déblocage nécessiterait une intervention manuelle sur
l'état du run, non faite ici faute de demande explicite.

Anomalie non résolue relevée au passage : le commit de `back-tu` (fichiers
`backend/tests/*`, hash `f8529e0`) a été signé git `front-aimazing` au lieu de
`back-aimazing` attendu (`backend.py::_GIT_IDENTITY_AGENT = "back"`, y compris
pour `back-tu`). Reproduit isolément (`commit_as_agent(agent="back", ...)`
dans un dépôt jetable) : le code se comporte correctement seul. Le commit
`back` suivant, dans le même run, a lui la bonne identité. Cause non
identifiée — pas de fix tenté, à investiguer séparément si ça se reproduit.

**Contraintes d'environnement à garder en tête** :
- Ollama doit tourner en conteneur Podman sur cette machine (voir mémoire
  `project_ollama_containerized`) — l'utilisateur change souvent de machine.
- Cette machine n'a pas de GPU : `qwen2.5:7b-instruct` y tourne à ~5 tokens/s,
  ce qui peut dépasser le timeout par défaut (180s) sur un prompt volumineux.

## Reste à faire

1. **Architecte : sortie structurée** (`--json-schema`, Claude Code CLI) pour le
   contrat `STATUT:`/`AGENT:`/`FEEDBACK:` de la phase 5 (audit des stubs) — fait
   côté PM (2026-07-14), pas encore côté Architecte.
2. **Contrôle de périmètre par fichier** — `files_to_create`/`files_to_modify`/
   `files_forbidden`/`dependencies` (structured output du PM, phase 3) sont
   capturés dans `state.agent_card_metadata` mais jamais vérifiés : rien
   n'empêche aujourd'hui Back/Front d'écrire hors du périmètre qu'ils ont
   eux-mêmes déclaré.
3. **Notification ntfy sur échec de test** — non câblée dans `nodes/test.py`
   (le mécanisme d'override local `config/local.yml` existe déjà, mais l'appel
   n'est fait nulle part sur un échec de non-régression).
4. **Visibilité sur l'avancement d'un run / texte brut généré par les agents**
   — besoin exprimé le 2026-07-15. Partiellement couvert le 2026-07-16 : un
   message "exécution en cours" + pointeur `tail -f trace.jsonl` répond à
   "est-ce vivant / comment suivre en direct" (voir entrée du même jour
   ci-dessus). Reste ouvert : une vraie commande `devaimazing show <run-id>`
   qui formaterait trace.jsonl (au lieu de le laisser en JSONL brut à lire
   via `tail`) ; tous les agents ou seulement PM ; cas de succès inclus (pas
   seulement `feedback_sent`).
5. **Conteneurisation de devaimazing lui-même (Podman)** — décidée mais
   explicitement reportée à la toute fin du projet (2026-07-14), aucun travail
   à engager avant. Implications non câblées : Claude Code CLI (sous-process
   supposant `claude` installé sur l'hôte), accès réseau à Ollama
   (`localhost:11434` en dur), montage du repo projet cible en volume,
   persistance de `state.db`/`metrics.db`.

Pas d'ordre de priorité déjà acté entre ces points au-delà de leur numérotation
ci-dessus — à trancher avec l'utilisateur en début de prochaine session.

## Historique (condensé)

Journal chronologique réduit à une ligne par jalon — le détail narratif complet
(raisonnement, diagnostics, comparaisons d'options) reste consultable via
`git log -p -- docs/roadmap.md` sur les commits antérieurs au 2026-07-15.

**2026-07-10** — Implémentation initiale du runtime :
- Contrats complets (Args/Returns/Raises/Side effects) écrits pour les 7 `nodes/*.py`.
- `config.py`, `filesystem.py`, `git.py`, `ollama.py`, `claude_code.py` implémentés et testés.
- `graph.py` implémenté (async, router, checkpoints) ; `routing.py` extrait pour éviter un import circulaire.
- Contrat de sortie fichiers défini : délimiteurs `<<<DEVAIMAZING_FILE>>>` (remplacé depuis par la sortie structurée côté Ollama).
- `backend.py`, `frontend.py`, `test.py`, `security.py` (SAST + Sonnet), `metrics.py`, `closer.py`, `architect.py`, `pm.py` implémentés — les 7 nodes complets.
- `cli.py` implémenté (`run`/`resume`/`runs`/`metrics`/`projects`/`doctor`) — runtime complet de bout en bout.
- Cible réelle `demo-todo-app` construite et vérifiée (FastAPI + SQLite + React, tests/build réels).
- Premier run réel : 5 bugs trouvés et corrigés (variable d'env non propagée, `.venv` instable sous iCloud Drive, connexion SQLite du checkpointer jamais fermée, prompts Sonnet réclamant Write/Edit puis Bash).
- 4 points de préparation résolus : permissions Claude Code CLI, câblage des métriques, `agents.max_iterations` appliqué, secret ntfy déplacé dans `config/local.yml` (gitignoré).

**2026-07-11** — Fiabilisation face à la variance des modèles :
- Run repris avec succès ; plusieurs échecs identifiés comme de la variance d'échantillonnage plutôt que des bugs de code.
- Bugs corrigés : fiches PM sans section `## Feedback` (validation avant écriture), producteurs Qwen sans contenu réel du fichier à modifier (contexte fichiers existants injecté), refus d'outil non fatal si contenu exploitable produit malgré tout, fallback parser pour un bloc `` ``` `` unique.
- Bug non résolu par un fix ponctuel : le PM produit parfois une séquence d'agents non conforme à sa propre doc → décision de traiter le problème de fond plutôt que de patcher au cas par cas.
- Chantier "sortie structurée" décidé : recherché et implémenté côté Ollama (Back/Front/Test) — `FILE_OUTPUT_SCHEMA`, grammar-constrained decoding, vérifié en conditions réelles (4 fichiers corrects produits en un seul appel).
- Bug corrigé : `architect-brief.md` introuvable en phase 5 (repo resté sur une branche stale) → `checkout_branch` systématique avant un nouveau run.
- Backlog noté : `devaimazing resume` ne gère pas la reprise après crash (seulement après validation humaine explicite).

**2026-07-14** — Outillage CLI et sortie structurée côté PM :
- "Fiches PM en sortie structurée" livré (`--json-schema` côté Claude Code CLI) — ancien scan regex du texte (`read_referenced_files`) supprimé, remplacé par des chemins explicites validés à l'écriture par le PM.
- `devaimazing run-agent` livré (test isolé d'un agent, ne touche jamais `state.db`), avec option `--reference-dir` (diff vs un run de référence).
- `devaimazing retry <run-id>` livré (reprise après crash, distincte de `resume` — diagnostic + confirmation avant rejeu).
- Bug corrigé : chemin de fichier absolu produit par un agent non validé (`_validate_relative_path` ajoutée).
- `devaimazing new-project <nom>` livré (init repo Git local + GitHub optionnel + `config/projects/<nom>.yml`).

**2026-07-15** — Robustesse du PM et traçabilité :
- Premier run réel de bout en bout sur un projet neuf (`todo-list`) — mis en pause volontaire sur une limite du modèle local, pas un bug devaimazing.
- Warning LangGraph au checkpoint corrigé (types `studio.state` déclarés au serde).
- PM phase Fiches : trois fixes successifs — tolérance aux fichiers produits par un agent antérieur de la séquence, séparation en deux appels LLM (métadonnées puis prose), dégradation gracieuse au lieu d'un échec net (aligné sur Back/Front/Test).
- Chantier "traçabilité d'exécution" livré : `tools/tracer.py` (JSONL par run), instrumenté sur les appels LLM (Claude Code CLI + Ollama, y compris les retries), le filesystem, les commits Git, l'entrée/sortie des 7 nodes et le cycle de vie d'un run dans `cli.py`.
