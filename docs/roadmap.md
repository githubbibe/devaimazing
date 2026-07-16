# Feuille de route - devaimazing

**Dernière mise à jour** : 2026-07-16

## État actuel

Le runtime devaimazing est **fonctionnellement complet et testé de bout en bout** :
`state.py`, `config.py`, `tools/*.py` (filesystem, git, ollama, claude_code, tracer),
`graph.py`, les 7 `nodes/*.py` (pm, architect, backend, frontend, test, security,
closer), `metrics.py` et `cli.py` (`run`, `resume`, `retry`, `run-agent`, `runs`,
`metrics`, `new-project`, `projects`, `doctor`) sont tous implémentés — voir
`CLAUDE.md` pour la convention (stub-first reste appliquée par le pipeline aux
projets *cibles*, pas à ce dépôt). **299/299 tests verts** sur `runtime/tests/`.

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

**Run laissé en pause volontaire** : `run-20260714-205712` (projet `todo-list`)
est arrêté sur `back-tu`, qui signale un `blocked_reason` factuellement faux —
limite de fiabilité de `qwen2.5:7b-instruct` sans GPU sur cette machine, pas un
bug devaimazing. Reprise : `devaimazing retry run-20260714-205712 --project
todo-list` (peut échouer une 3ᵉ fois et basculer en `FAILED`).

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
   — besoin exprimé le 2026-07-15, **pas encore cadré**. Questions à trancher
   avant tout code : streaming en direct vs consultation a posteriori (ex.
   nouvelle commande `devaimazing show <run-id>` ou `--verbose`) ; tous les
   agents ou seulement PM ; cas de succès inclus (pas seulement
   `feedback_sent`) ; où stocker ce contenu (le tracer livré le 2026-07-15,
   `specs/<run-id>/trace.jsonl`, couvre déjà une partie du besoin diagnostic —
   à évaluer si ça suffit avant de coder autre chose).
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
