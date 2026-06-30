# ADR 0005 - LangGraph comme orchestrateur

**Date** : 2026-06  
**Statut** : Accepté

## Contexte

Plusieurs frameworks permettent d'orchestrer des agents LLM : LangGraph, CrewAI,
AutoGen, Mozilla any-agent, ou un custom asyncio + Redis.

## Décision

LangGraph (Python, LangChain AI) comme runtime d'orchestration.

## Raisons

1. **Graphe d'états explicite** : le workflow en 10 phases est modélisé comme un graphe
   directionnel. Chaque phase est un node, chaque transition est une edge conditionnelle.
   La structure du workflow est visible et modifiable dans le code Python.

2. **Checkpointer natif** : LangGraph intègre nativement la persistance d'état (SQLite,
   PostgreSQL). L'état du PM est persisté sans code custom.

3. **Human-in-the-loop natif** : LangGraph supporte les `interrupt_before` et
   `interrupt_after` pour les checkpoints de validation humaine. Pas de code custom.

4. **Contrôle de concurrence** : le workflow séquentiel mono-run s'exprime naturellement
   comme un graphe linéaire. Pas besoin de locks ou de queues externes.

5. **Écosystème mature** : LangGraph est utilisé en production par des centaines d'équipes
   IA. Documentation complète, exemples abondants, bugs connus et résolus.

## Conséquences

- Dépendance à LangChain AI (entreprise privée). Risque de breaking changes à gérer.
- La logique d'orchestration est en Python pur LangGraph, pas dans les agents.
- La scalabilité multi-run (si besoin futur) nécessiterait LangGraph Platform ou
  une migration vers un orchestrateur distribué.

## Alternatives rejetées

- **CrewAI** : abstraction trop haute, contrôle de concurrence insuffisant pour notre workflow.
- **AutoGen** : orienté conversations multi-tours, pas adapté à un workflow séquentiel en phases.
- **Custom asyncio** : plus de code à maintenir, réinvente des fonctionnalités LangGraph.
- **Mozilla any-agent** : trop jeune (2025), pas assez d'adoption en production.
