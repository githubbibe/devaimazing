# ADR 0011 - Orchestrateur custom plutôt que Claude Code remote / subagents

**Date** : 2026-07
**Statut** : Accepté

## Contexte

Claude Code propose des mécanismes natifs d'exécution multi-agents (subagents, exécution
remote/cloud). Il est légitime de se demander pourquoi devaimazing développe un
orchestrateur LangGraph dédié plutôt que de s'appuyer directement sur ces mécanismes pour
obtenir un résultat en apparence similaire (plusieurs agents spécialisés qui collaborent
sur une tâche de développement).

## Décision

devaimazing reste un orchestrateur custom (LangGraph, pipeline fixe de 6 agents sur
10 phases), et n'est pas remplacé par une utilisation directe de Claude Code remote ou
des subagents Claude pour l'ensemble du pipeline.

## Raisons

1. **Segmentation des coûts** : sur les 6 agents, seuls PM (phase 1, Opus), PM (phase 3),
   Architecte et Sécu (Sonnet) passent par l'API Anthropic facturée. Back, Front et Test
   tournent en local sur Qwen 2.5 via Ollama, à coût zéro token (voir `docs/llm-strategy.md`,
   ADR 0006). Une exécution intégralement via Claude remote/subagents facturerait tous les
   agents, y compris les tâches de production de code répétitives où un modèle local
   suffit.
2. **Couche SAST déterministe à coût zéro** : Semgrep et Bandit tournent avant l'audit
   Sonnet de la phase 8 pour ne pas payer de tokens sur ce qu'un outil déterministe détecte
   gratuitement (voir `prompts/security.md`).
3. **Gouvernance de process figée** : pipeline fixe en 10 phases, 6 identités git distinctes
   par agent, checkpoints humains explicites (ADR 0008, ADR 0010), nommage de branches
   normalisé (ADR 0007), agents stateless sauf le PM (ADR 0001). Ce sont des contraintes de
   process vérifiables et reproductibles d'un run à l'autre, pas seulement le résultat d'une
   consigne donnée à un agent générique.
4. **Local-first** : le dépôt cible explicitement une exécution locale pour les tâches de
   production (voir le README, section description du dépôt). Faire dépendre l'intégralité
   du pipeline d'une exécution cloud irait à l'encontre de cet objectif.

## Conséquences

- Ce choix coûte du temps de développement : l'orchestrateur lui-même doit être construit et
  maintenu (au 2026-07-09, encore en phase stub-first, voir `docs/roadmap.md`), alors qu'un
  usage direct de Claude Code remote aurait été utilisable immédiatement.
- En contrepartie, le coût en tokens API est maîtrisé (seuls 3 agents sur 6 y recourent) et
  le process est reproductible et auditable (pipeline fixe, identités git séparées,
  checkpoints).
- Si le coût de développement de l'orchestrateur venait à dépasser l'économie de tokens
  attendue sur la durée, ce choix devrait être réévalué explicitement — pas par glissement.

## Alternatives rejetées

- **Claude Code remote/subagents pour l'ensemble du pipeline** : solution immédiatement
  disponible, mais facture chaque agent (y compris les tâches de production répétitives) au
  tarif API, et n'offre pas nativement la couche SAST zéro-token ni la gouvernance de
  process (identités git séparées, checkpoints figés) documentée dans les ADR existants.
  Rejetée pour le coût récurrent et la perte de contrôle de process.
- **Solution hybride (Claude remote pour l'audit, subagents génériques pour la
  production)** : réduirait la maîtrise du process (pas de garantie que le pipeline reste
  figé en 10 phases d'un run à l'autre) sans supprimer le coût API sur les agents de
  production. Non retenue faute de bénéfice net par rapport à la solution actuelle.
