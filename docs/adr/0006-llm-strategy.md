# ADR 0006 - Stratégie LLM Opus/Sonnet/Qwen

**Date** : 2026-06  
**Statut** : Accepté

## Contexte

Le studio dispose de deux types de LLM : l'API Anthropic (Claude Code CLI) et
des modèles locaux via Ollama. Le coût des tokens API est la contrainte principale.
Le Mac mini M4 Pro dispose de 24 Go de RAM unifiée.

## Décision

**PM - Claude Code CLI (API Anthropic)**
- Phase 1 (cadrage, fiche racine) : Opus 4.x. Raisonnement de haut niveau, architectural.
  Invoqué une seule fois par run au démarrage, et en cas de blocage uniquement.
- Phase 3 (raffinement, fiches dépendantes) : Sonnet 4.6. Mode de croisière du PM.
- Coordination inter-phases (transitions triviales) : Python pur. 0 token.
- Phase 10 (clôture) : Python pur. 0 token.

**Agents producteurs - Ollama local**
- Back, Front, Test : Qwen 2.5 7B Instruct (Q4_K_M, ~4.5 Go).
- Un seul modèle chargé à la fois (contrainte RAM 24 Go avec containers Podman actifs).
- Fallback : si un agent échoue après plusieurs itérations, reprise manuelle avec
  Cursor ou Claude Code. Pas de fallback API automatique (contrôle des coûts).

**Agents auditeurs - Claude Sonnet 4.6 (API Anthropic)**
- Architecte (phases 2, 5, 9) et Sécu (phase 8) : Claude Sonnet 4.6.
- Un auditeur doit dominer en capacité l'agent qu'il audite : un modèle ne peut pas
  détecter correctement la dette qu'il aurait lui-même laissée passer à la génération.
  Qwen 2.5 7B ne peut donc pas auditer du code produit par Qwen 2.5 7B.
- Phase 8 (Sécu) : un passage SAST déterministe (Semgrep, Bandit — voir
  `config/studio.yml` section `sast`) tourne en premier, à coût zéro token. Sécu
  audite ensuite ce que le SAST ne couvre pas (logique métier, autorisation,
  cohérence globale).

## Raisons

1. **Contrainte RAM** : avec macOS + Podman + containers v1 prod, environ 6-10 Go
   disponibles pour Ollama. Un modèle 7B en Q4 consomme ~4.5 Go. Un 14B serait tendu.

2. **Opus uniquement pour la réflexion haute** : le cadrage du projet et le découpage
   en fiches sont les tâches les plus complexes et les plus structurantes. Elles justifient
   Opus. L'exécution (code, tests, audit) est guidée par les fiches et ne nécessite pas
   Opus.

3. **Sonnet pour le raffinement** : les fiches dépendantes sont plus structurées que le
   cadrage initial. Sonnet 4.6 est suffisant et 3-5x moins cher qu'Opus.

4. **Qwen 2.5 7B pour l'exécution locale** : bon compromis code/raisonnement pour 7B.
   A benchmarker contre Qwen 2.5 Coder 7B et Qwen 2.5 14B une fois le pipeline stable.

5. **Le modèle est une variable de config** : `config/studio.yml` déclare les modèles
   (clé `agent_auditor` pour Architecte/Sécu, `agents_local` pour Back/Front/Test).
   Changer de modèle ne nécessite pas de modifier le code.

6. **Auditeur doit dominer producteur** : voir `ARCHITECTURE.md` principe 4. C'est la
   raison pour laquelle Architecte et Sécu ont été déplacés de Qwen vers Sonnet après
   la version initiale de cette décision — un agent auditeur de même capacité que le
   producteur ne peut pas être fiable pour catcher sa propre classe d'erreurs.

## Métriques de segmentation tokens

Les tokens sont comptés séparément :
- Tokens API Opus (coût €, surveiller de près)
- Tokens API Sonnet (coût €, surveiller)
- Tokens Ollama local (coût électricité, surveiller pour RAM/perf)
- Tokens fallback manuel Cursor/Claude Code (coût € + temps humain)

## Conséquences

- Les runs en phase de démarrage (Opus) sont les plus coûteux. Investissement justifié.
- Si Ollama OOM pendant un run, l'agent marque sa fiche en échec et notifie via Telegram.
- Benchmarking des modèles Ollama à faire après stabilisation du pipeline LangGraph.

## Alternatives rejetées

- **Tout Opus** : coût prohibitif pour l'exécution (code, tests, audit).
- **Tout local** : qualité insuffisante pour le cadrage architectural (PM).
- **Fallback API automatique** : perte de contrôle sur les coûts. Refusé.
- **Deux modèles Ollama simultanés** : RAM insuffisante sur 24 Go avec Podman actif.
