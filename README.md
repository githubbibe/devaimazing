# devaimazing

> **Local-first multi-agent development studio.**
> Claude Code for high-level reasoning. Ollama agents for execution. LangGraph orchestration. Full observability. Built to maximize quality per API token.

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python](https://img.shields.io/badge/Python-3.11+-green.svg)](https://www.python.org/)
[![LangGraph](https://img.shields.io/badge/LangGraph-orchestrator-orange.svg)](https://github.com/langchain-ai/langgraph)
[![Ollama](https://img.shields.io/badge/Ollama-local%20LLM-purple.svg)](https://ollama.ai/)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-PM%20agent-red.svg)](https://claude.ai/code)

---

## What is devaimazing?

devaimazing is a **multi-agent development studio** : an orchestrated pipeline of 6 specialized AI agents that collaborates to design, implement, test, secure, and document software features autonomously, with progressive human validation checkpoints.

It is the development engine of the [*aimazing ecosystem](https://aimazing.fr) (webaimazing, shopaimazing, foodaimazing, and others), and can be pointed at any software project via configuration.

---

## Pourquoi devaimazing ?

Les studios de développement IA modernes font face à une tension : **qualité vs coût API**. Faire tourner un modèle frontier (Claude Opus, GPT-4o) pour chaque ligne de code produite est prohibitif et contre-productif. Confier tout à un LLM local sans cadrage produit du code instable.

devaimazing résout cette tension avec une philosophie simple :

- **Opus pour la réflexion haute** : cadrage du projet, découpage en fiches, architecture. Invoqué une seule fois par run, en début de projet ou en cas de blocage.
- **Agents locaux pour l'exécution** : 5 agents spécialisés tournent sur Ollama (Qwen 2.5 7B) et produisent le code, les tests, les audits. Zéro token API pour l'exécution.
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
    +-- Architecte (Ollama - Qwen 2.5 7B)
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
    +-- Secu (Ollama - Qwen 2.5 7B)
            Audit securite du code produit
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
| Agents execution | Code, test, secu, archi | Ollama + Qwen 2.5 7B Instruct |
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
│   └── adr/                     # Architecture Decision Records
│       ├── 0001-stateless-agents.md
│       ├── 0002-stub-first.md
│       ├── 0003-sqlite-checkpointer.md
│       └── 0004-agpl-licence.md
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
│   └── stub-first.md
├── templates/                   # Squelettes de fiches generiques
│   ├── card-root.md.template
│   ├── card-agent.md.template
│   ├── project-map.md.template
│   └── architect-map.md.template
├── runtime/                     # Code Python du studio
│   ├── studio/
│   │   ├── graph.py             # Definition LangGraph
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
│       └── webaimazing-v2.yml   # repo_path, branche, params
├── interfaces/
│   └── telegram-bridge/         # Configuration OpenClaw skills
├── infra/
│   ├── podman/                  # Compose files Prometheus dev
│   └── ollama/                  # Config modeles a pull
├── examples/
│   └── demo-todo-app/           # Projet exemple standalone pour demo
│       ├── specs/
│       ├── src/
│       └── README.md
└── scripts/
    ├── setup.sh                 # Installation complete
    └── new-project.sh           # Initialise un nouveau projet cible
```

---

## Installation

### Prerequis

- macOS (Apple Silicon recommande) ou Linux
- [uv](https://github.com/astral-sh/uv) installe
- [Ollama](https://ollama.ai/) installe et operationnel
- [Claude Code CLI](https://claude.ai/code) installe et configure
- [OpenClaw](https://openclaw.ai/) installe (optionnel, interface Telegram)
- Git configure avec SSH

### Setup

```bash
# Cloner le repo
git clone git@github.com:<username>/devaimazing.git
cd devaimazing

# Installer les dependances et le CLI
uv sync
uv pip install -e .

# Verifier l'installation
devaimazing --version

# Puller le modele Ollama
ollama pull qwen2.5:7b-instruct

# Configurer un projet cible
cp config/projects/webaimazing-v2.yml config/projects/mon-projet.yml
# Editer mon-projet.yml avec le chemin vers ton repo

# Lancer un run sur le projet exemple
devaimazing run examples/demo-todo-app
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

# Lister les projets configures
devaimazing projects
```

---

## Identite Git des agents

Chaque agent signe ses commits avec sa propre identite :

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

devaimazing peut piloter n'importe quel repo projet via un fichier de config :

```yaml
# config/projects/mon-projet.yml
name: mon-projet
repo_path: ~/code/aimazing/mon-projet/
branch_prefix: studio/
specs_dir: specs/
model:
  pm_opus: claude-opus-4-8
  pm_sonnet: claude-sonnet-4-6
  agents: qwen2.5:7b-instruct
```

Les projets vivent dans `~/code/aimazing/<projet>/`, au meme niveau que devaimazing. Aucun fichier projet n'est stocke dans le repo studio.

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
