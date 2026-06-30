# Architecture de devaimazing

Ce document décrit les décisions d'architecture structurantes du studio.
Les ADR détaillés sont dans `docs/adr/`.

## Vue d'ensemble

devaimazing est un graphe LangGraph de 6 agents spécialisés orchestrés séquentiellement.
Le runtime Python est le seul chef d'orchestre. Aucun agent ne pilote un autre agent.

## Principes fondamentaux

**1. Agents stateless sauf PM**
Chaque agent démarre avec uniquement son prompt système + skills + fiche de tâche.
Pas d'historique de conversation entre les runs. Le PM seul persiste son état via SQLite.
Conséquence : les fiches .md sont le seul vecteur de mémoire inter-agents.

**2. Stub-first obligatoire**
Avant toute implémentation, chaque agent codant produit des fichiers avec uniquement
signatures, types, docstrings, exceptions, dépendances. L'Architecte valide les stubs
avant que la moindre ligne métier soit écrite. Cadre la dérive au plus tôt.

**3. Séquentiel, pas de parallélisme**
Un seul run à la fois. Les agents interviennent chacun leur tour selon la séquence
définie par le PM. Le contrôle de concurrence est implicite (pas de locks nécessaires).

**4. Local-first**
Tous les agents d'exécution tournent sur Ollama (Qwen 2.5 7B local).
Seul le PM utilise Claude Code CLI, uniquement pour le cadrage initial (Opus)
et le raffinement des fiches (Sonnet). Mode par défaut : abonnement Pro
(humain dans la boucle, conforme aux ToS Anthropic). Mode alternatif :
connexion à l'API Anthropic pay-per-token pour des runs entièrement
non-supervisés. L'objectif reste de minimiser la consommation de tokens
côté Claude Code, quel que soit le mode.

**5. Validation humaine progressive**
Les checkpoints humains sont obligatoires au démarrage (phases 1, 2, 3, 5, 9).
Ils passent en automatique au fur et à mesure que le système est maîtrisé.

**6. Traçabilité Git par agent**
Chaque agent commit sous sa propre identité Git. Le git log est un journal d'audit
complet de qui a produit quoi.

## Composants externes

devaimazing core est strictement le runtime LangGraph + ses 6 agents + ses outils
locaux. Tout ce qui touche à l'interface utilisateur est externe au core.

**OpenClaw (interface mobile)**
OpenClaw est utilisé comme passerelle Telegram pour piloter devaimazing
depuis mobile (AFK). Il reçoit tes messages, les transmet au runtime
LangGraph via un skill dédié, et te renvoie les notifications de progression
et de checkpoints. OpenClaw n'orchestre rien : il est uniquement la couche
de transport entre toi et le runtime.

Si tu n'utilises pas OpenClaw, le runtime LangGraph est invocable directement
en CLI (`devaimazing run <project>`).

## Décisions clés

Voir `docs/adr/` pour le détail de chaque décision :

- [0001 - Agents stateless sauf PM](docs/adr/0001-stateless-agents.md)
- [0002 - Stub-first](docs/adr/0002-stub-first.md)
- [0003 - SQLite comme checkpointer](docs/adr/0003-sqlite-checkpointer.md)
- [0004 - AGPL-3.0](docs/adr/0004-agpl-licence.md)
- [0005 - LangGraph comme orchestrateur](docs/adr/0005-langgraph.md)
- [0006 - Stratégie LLM Opus/Sonnet/Qwen](docs/adr/0006-llm-strategy.md)
