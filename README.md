# devaimazing

> **Local-first multi-agent development studio.**
> Claude Code for high-level reasoning. Ollama agents for execution. LangGraph orchestration. Full observability. > Built to maximize quality per token, whether subscription or API.

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python](https://img.shields.io/badge/Python-3.11+-green.svg)](https://www.python.org/)
[![LangGraph](https://img.shields.io/badge/LangGraph-orchestrator-orange.svg)](https://github.com/langchain-ai/langgraph)
[![Ollama](https://img.shields.io/badge/Ollama-local%20LLM-purple.svg)](https://ollama.ai/)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-PM%20agent-red.svg)](https://claude.ai/code)

---

## What is devaimazing?

devaimazing is a **multi-agent development studio** : an orchestrated pipeline of 8 specialized agent roles (across 6 LangGraph nodes — Back-tu and Front-tu run as distinct activations of Back/Front, sharing their node and Git identity) that collaborates to design, implement, test, secure, and document software features autonomously, with progressive human validation checkpoints.

It is the development engine of the [*aimazing ecosystem](https://aimazing.fr) (webaimazing, shopaimazing, foodaimazing, and others), and can be pointed at any software project via configuration.

---

## Pourquoi devaimazing ?

Les studios de développement IA modernes font face à une tension : **qualité vs coût API**. Faire tourner un modèle frontier (Claude Opus, GPT-4o) pour chaque ligne de code produite est prohibitif et contre-productif. Confier tout à un LLM local sans cadrage produit du code instable.

devaimazing résout cette tension avec une philosophie simple :

- **Opus pour la réflexion haute** : cadrage du projet, découpage en fiches, architecture. Invoqué une seule fois par run, en début de projet ou en cas de blocage.
- **Agents locaux pour la production** : Back, Front et Test tournent sur Ollama (Qwen 2.5 7B) et produisent le code et les tests. Zéro token API pour la production.
- **Agents auditeurs sur Sonnet** : Architecte et Sécu tournent sur Claude Sonnet, car un auditeur doit dominer en capacité l'agent qu'il audite pour attraper sa dette.
- **Stub-first pour cadrer la dérive** : chaque agent écrit d'abord les signatures, types, docstrings et contrats AVANT d'implémenter. L'Architecte valide avant que la moindre ligne métier soit écrite.
- **Validation humaine progressive** : tu valides les étapes clés au départ. Une fois le système maîtrisé, les checkpoints passent en automatique.

L'objectif : produire du code de qualité production tout en **minimisant la consommation de tokens API** par run.

---

## Architecture

```
Toi (Telegram, mobile)
    |
OpenClaw (daemon Mac mini, passerelle Telegram)
    |
Runtime LangGraph (Python, orchestrateur)
    |
    +-- PM (Claude Code CLI - Opus/Sonnet selon la phase)
    |       Phase 1 : cadrage, fiche racine (Opus)
    |       Phase 3 : fiches dependantes (Sonnet)
    |       Phase 10 : cloture pure Python, 0 token
    |
    +-- Architecte (Claude Sonnet - agent auditeur)
    |       Audit non-fonctionnel amont et aval
    |       Detection doublons, factorisation
    |       Documentation complete (ADR, OpenAPI, runbooks)
    |
    +-- Back (Ollama - Qwen 2.5 7B)
    |       Perimetre /backend/
    |       Stub-first puis implementation
    |
    +-- Front (Ollama - Qwen 2.5 7B)
    |       Perimetre /frontend/
    |       Stub-first puis implementation
    |
    +-- Test (Ollama - Qwen 2.5 7B)
    |       Tests unitaires, integration, non-regression
    |       Une fiche -tu par agent codant
    |
    +-- Secu (Claude Sonnet - agent auditeur)
            SAST deterministe (Semgrep, Bandit) puis audit securite du code produit
```

**Agents stateless sauf PM.** A chaque run, les agents demarrent avec uniquement leur prompt systeme + skills + fiche de tache. Pas d'historique. Le PM seul porte la memoire projet via un checkpointer SQLite.

---

## Workflow (10 phases)

```
Phase 0   Reception          Toi → Telegram → OpenClaw → PM
Phase 1   Cadrage            PM (Opus) : fiche racine, objectif, criteres, contraintes
                             → checkpoint validation humaine
Phase 2   Audit amont        Architecte : contraintes non-fonctionnelles, carte fichiers,
                             zones d'impact non-regression
                             → checkpoint validation humaine
Phase 3   Fiches             PM (Sonnet) : une fiche par agent, sequencee par le PM
          dependantes        → checkpoint validation humaine
Phase 4   Stub-first         Back + Front : fichiers avec signatures, types,
                             docstrings, exceptions. Pas d'implementation.
Phase 5   Audit stubs        Architecte : coherence inter-fichiers, respect contraintes,
                             detection derive. Renvoi a l'agent si ecart.
                             → checkpoint validation humaine
Phase 6   Implementation     Back + Front remplissent le code.
                             Back-tu + Front-tu ecrivent les tests unitaires.
Phase 7   Tests transverses  Agent Test : integration + non-regression
Phase 8   Audit securite     Agent Secu : audit du code produit
Phase 9   Audit aval         Architecte : conformite non-fonctionnelle, factorisation,
                             documentation complete
                             → checkpoint validation humaine
Phase 10  Cloture            Python pur : MAJ project-map, commit Git signe
                             par agent, notification Telegram. 0 token.
```

**Boucle de feedback erreur** : si l'agent N+1 detecte une erreur de l'agent N, il annote la fiche de l'agent N et le relance avec les annotations en contexte. L'agent N corrige, N+1 reprend.

**Fallback** : si un agent local echoue apres plusieurs iterations, la fiche annotee est disponible pour reprise manuelle avec Cursor ou Claude Code.

---

## Stack technique

| Composant | Role | Technologie |
|---|---|---|
| Orchestrateur | Runtime du graphe d'agents | LangGraph (Python) |
| PM agent | Cadrage et sequencement | Claude Code CLI (Opus/Sonnet) |
| Agents producteurs | Back, Front, Test | Ollama + Qwen 2.5 7B Instruct |
| Agents auditeurs | Architecte, Secu | Claude Sonnet (API) |
| Persistance etat | Checkpointer PM | SQLite |
| Metriques | Tokens, temps, RAM, latence | SQLite + Prometheus |
| Observabilite | Dashboards | Grafana (datasource dev dedié) |
| Interface utilisateur | Pilotage mobile | OpenClaw + Telegram |
| Gestionnaire deps | Python | uv |
| Versioning | Commits par agent | Git (identite par agent) |

---

## Metriques et observabilite

devaimazing collecte des metriques a 3 niveaux :

**Par tache (atomique)**
- Tokens prompt / completion / total
- Temps de calcul LLM et total
- Nombre d'appels Claude Code en sous-process
- Statut (succes, erreur, renvoi)
- Modele utilise

**Par fiche**
- Somme tokens et temps
- Nombre d'iterations (renvois inclus)
- Agents intervenants
- Cout equivalent API (comparaison Ollama vs API)

**Par run**
- Repartition tokens par agent et par phase
- Segmentation : tokens Opus / tokens Sonnet / tokens Ollama / tokens fallback manuel
- Duree wall-clock depuis ouverture jusqu'au commit
- Nombre de checkpoints humains

**Metriques systeme**
- RAM / CPU / GPU Mac mini pendant le run
- Latence Ollama par modele
- Erreurs runtime LangGraph

Toutes les metriques sont exportees vers un **Prometheus dev dedie** (Podman) et visualisees dans le **Grafana existant** (datasource prod + dev dans la meme UI).

---

## Structure du repo

```
devaimazing/
├── README.md
├── ARCHITECTURE.md              # Decisions d'architecture (ADR)
├── LICENSE                      # AGPL-3.0
├── pyproject.toml               # Config uv, entry point CLI devaimazing
├── docs/
│   ├── workflow.md              # Les 10 phases en detail
│   ├── agents.md                # Roles, perimetres, sequencement
│   ├── metrics.md               # Schema metriques complet
│   ├── llm-strategy.md          # Opus/Sonnet/Qwen : quand quoi
│   ├── infra-topology.md        # Topologie reseau Podman
│   ├── roadmap.md               # Feuille de route runtime
│   └── adr/                     # Architecture Decision Records
│       ├── 0001-stateless-agents.md
│       ├── 0002-stub-first.md
│       ├── 0003-sqlite-checkpointer.md
│       ├── 0004-agpl-licence.md
│       ├── 0005-langgraph.md
│       ├── 0006-llm-strategy.md
│       ├── 0007-branch-naming-and-incremental-commits.md
│       ├── 0008-checklist-intention-phase1.md
│       ├── 0009-pseudonymisation-anti-fraude.md
│       ├── 0010-quatre-piliers-non-fonctionnels-dette-justifiee.md
│       └── 0011-orchestrateur-custom-vs-claude-remote.md
├── prompts/                     # Prompts systeme des agents
│   ├── pm.md
│   ├── architect.md
│   ├── backend.md
│   ├── frontend.md
│   ├── test.md
│   └── security.md
├── skills/                      # Skills partages (references par prompts)
│   ├── error-handling.md
│   ├── logging-conventions.md
│   ├── retry-patterns.md
│   ├── non-regression.md
│   ├── factorization.md
│   ├── documentation.md
│   ├── stub-first.md
│   ├── data-privacy.md
│   └── scalability.md
├── templates/                   # Squelettes de fiches generiques
│   ├── card-root.md.template
│   ├── card-root-import.md.template  # Squelette import de fiche root existante
│   ├── card-agent.md.template
│   ├── project-map.md.template
│   ├── architect-map.md.template
│   └── project-config.yml.template  # Squelette config/projects/<nom>.yml
├── runtime/                     # Code Python du studio
│   ├── studio/
│   │   ├── cli.py                # CLI (run, resume, retry, run-agent, new-project...)
│   │   ├── graph.py             # Definition LangGraph
│   │   ├── routing.py           # Routage phases/agents, checkpoints, max_iterations
│   │   ├── state.py             # Schema etat
│   │   ├── config.py            # Chargement projet cible, modeles
│   │   ├── metrics.py           # Collecte + export Prometheus
│   │   ├── nodes/               # Un node par agent
│   │   │   ├── pm.py
│   │   │   ├── architect.py
│   │   │   ├── backend.py
│   │   │   ├── frontend.py
│   │   │   ├── test.py
│   │   │   ├── security.py
│   │   │   └── closer.py        # Phase 10 pure Python
│   │   └── tools/
│   │       ├── claude_code.py   # Wrapper subprocess Claude Code CLI
│   │       ├── ollama.py        # Wrapper LLM local
│   │       ├── git.py           # Ops Git (commits signes par agent)
│   │       └── filesystem.py    # Lecture/ecriture fiches
│   └── tests/
├── config/
│   ├── studio.yml               # Config globale (modeles, paths)
│   └── projects/                # Config par projet cible
│       ├── webaimazing-v2.yml   # repo_path, branche, params
│       ├── demo-todo-app.yml    # idem, pour le projet de demo
│       ├── todo-list.yml        # idem, premier run reel de bout en bout
│       └── todo-list2.yml       # idem, run bout en bout post-fix troncature Ollama (num_ctx)
├── interfaces/
│   └── telegram-bridge/         # Configuration OpenClaw skills
├── infra/
│   ├── podman/                  # Compose files Prometheus dev
│   └── ollama/                  # Config modeles a pull
├── examples/
│   └── demo-todo-app/           # Doc sur le projet de demo (le code vit hors
│       └── README.md            # de ce depot, voir config/projects/demo-todo-app.yml)
└── scripts/
    └── setup.sh                 # Installation complete
```

---

## Installation

### Prerequis

- macOS (Apple Silicon recommande) ou Linux
- Python 3.11+ et pip (ou [uv](https://github.com/astral-sh/uv) si installe — attention
  a l'emplacement du venv dans ce cas aussi, voir Setup ci-dessous)
- [Ollama](https://ollama.ai/) installe et operationnel
- [Claude Code CLI](https://claude.ai/code) installe et configure
- [OpenClaw](https://openclaw.ai/) installe (optionnel, interface Telegram)
- Git configure avec SSH

### Setup

**Important — emplacement du venv** : ce depot vit sous iCloud Drive (`~/Library/Mobile
Documents/com~apple~CloudDocs/...`). Un `.venv` cree *dans* le depot (comportement par
defaut de `uv sync`/`python -m venv .venv`) subit la synchronisation iCloud en tache de
fond, ce qui provoque des echecs intermittents `ModuleNotFoundError: No module named
'studio'` sur l'installation editable (des milliers de petits fichiers/symlinks dans un
`.venv` sont un tres mauvais candidat pour la sync cloud — deja constate et corrige le
2026-07-10, voir `docs/roadmap.md`). Creer le venv **hors** du depot :

```bash
# Cloner le repo
git clone git@github.com:<username>/devaimazing.git
cd devaimazing

# Installer les dependances et le CLI — venv HORS du depot (hors iCloud Drive)
python3 -m venv ~/.venvs/devaimazing
~/.venvs/devaimazing/bin/pip install -e .
# alias pratique pour la suite :
alias devaimazing=~/.venvs/devaimazing/bin/devaimazing

# Verifier l'installation
devaimazing --version

# Puller le modele Ollama
ollama pull qwen2.5:7b-instruct

# Initialiser un nouveau projet cible (dossier frere de devaimazing, repo Git,
# config/projects/mon-projet.yml) — voir section "Projets cibles" plus bas
devaimazing new-project mon-projet

# Lancer un run sur le projet exemple (nom de projet, pas un chemin — voir
# config/projects/demo-todo-app.yml)
devaimazing run demo-todo-app
```

### Verifier l'environnement

```bash
devaimazing doctor
```

Verifie : Ollama accessible, Claude Code CLI disponible, modele present, SQLite initialisable, Git configure.

---

## Usage

```bash
# Demarrer un run sur un projet configure
devaimazing run <project-name>

# Lister les runs d'un projet
devaimazing runs <project-name>

# Voir les metriques d'un run
devaimazing metrics <run-id>

# Reprendre un run apres un checkpoint humain
devaimazing resume <run-id>

# Initialiser un nouveau projet cible
devaimazing new-project <project-name>

# Lister les projets configures
devaimazing projects
```

---

## Identite Git des agents

Chaque agent signe ses commits avec sa propre identite (auteur/committer Git — pas de
signature GPG : `sign_commits: false` par defaut dans `config/studio.yml`) :

| Agent | Git identity |
|---|---|
| PM | `pm-aimazing <pm@aimazing.fr>` |
| Architecte | `architect-aimazing <architect@aimazing.fr>` |
| Back | `back-aimazing <back@aimazing.fr>` |
| Front | `front-aimazing <front@aimazing.fr>` |
| Test | `test-aimazing <test@aimazing.fr>` |
| Secu | `security-aimazing <security@aimazing.fr>` |

Le `git log` permet de tracer exactement quel agent a produit quel code.

---

## Projets cibles

devaimazing peut piloter n'importe quel repo projet via un fichier de config. La commande
`new-project` automatise l'initialisation complete :

```bash
devaimazing new-project mon-projet
```

Ce que fait la commande :

1. Cree le dossier `mon-projet/` **au meme niveau que devaimazing** (dossier frere, quel
   que soit l'emplacement du checkout devaimazing sur la machine).
2. Initialise un repo Git local dedie dans ce dossier (branche `develop`, un commit initial).
3. Propose (confirmation interactive, `--private`/`--public`, defaut prive) de creer le
   repo GitHub distant correspondant via `gh` et d'y pousser `develop` — desactivable avec
   `--skip-github` pour un repo local uniquement.
4. Ecrit `config/projects/mon-projet.yml` (depuis `templates/project-config.yml.template`)
   avec le `repo_path` resolu.

Si le dossier ou le repo GitHub existent deja, `new-project` reutilise ce qui est present
(idempotent) et se contente d'ecrire la config manquante. Aucun fichier projet n'est stocke
dans le repo studio : le seul lien entre devaimazing et un projet cible est ce fichier de
config, via `repo_path`.

---

## Contribuer

Les contributions sont les bienvenues : bug fixes, ameliorations des skills, nouveaux templates de fiches, support de nouveaux modeles Ollama, ameliorations des metriques.

> **Note importante sur la licence** : devaimazing est distribue sous AGPL-3.0. Toute contribution implique que tu acceptes que ton code soit distribue sous cette meme licence. Si le projet evolue vers un modele commercial, un Contributor License Agreement (CLA) pourra etre mis en place. La transparence sera totale sur ce point avant toute decision.

**Pour contribuer**

```bash
# Fork le repo sur GitHub
# Clone ton fork
git clone git@github.com:<ton-username>/devaimazing.git

# Cree une branche
git checkout -b feature/ma-contribution

# Fais tes modifications
# Lance les tests
uv run pytest

# Push et ouvre une PR
git push origin feature/ma-contribution
```

**Ce qu'on cherche**
- Ameliorations des prompts agents (dossier `prompts/`)
- Nouveaux skills partages (dossier `skills/`)
- Nouveaux templates de fiches (dossier `templates/`)
- Ameliorations du runtime LangGraph (dossier `runtime/`)
- Corrections de bugs et ameliorations de la documentation

---

## Ecosysteme *aimazing

devaimazing est le studio de developpement de l'ecosysteme *aimazing, une suite d'outils IA destines aux TPE et artisans francais.

| Projet | Description | Statut |
|---|---|---|
| dataimazing | Socle de donnees et racine de l'ecosysteme | En cours |
| webaimazing | Creation de sites web guidee par IA | V1 prod, V2 en dev |
| shopaimazing | Interface e-commerce IA | En conception |
| foodaimazing | Commande et livraison pour restaurateurs | En conception |
| devaimazing | Studio de developpement multi-agents | Ce repo |

---

## Licence

devaimazing est distribue sous **GNU Affero General Public License v3.0 (AGPL-3.0)**.

Tu peux librement utiliser, modifier et redistribuer ce logiciel, y compris dans un contexte commercial, a condition que toute version derivee soit distribuee sous la meme licence, y compris si elle est accessible via un service reseau.

Voir le fichier [LICENSE](LICENSE) pour le texte complet.

---

*devaimazing est un projet independant, non affilie a Anthropic, LangChain, Ollama ou OpenClaw.*
