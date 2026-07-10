# ADR 0006 - Stratégie LLM Opus/Sonnet/Qwen

**Date** : 2026-06  
**Mis à jour** : 2026-07 (correction auditeurs)  
**Statut** : Accepté

## Contexte

Le studio dispose de deux types de LLM : l'API Anthropic (Claude Code CLI) et
des modèles locaux via Ollama. Le coût des tokens API est la contrainte principale.
Le Mac mini M4 Pro dispose de 24 Go de RAM unifiée.

Un principe fondamental a émergé en cours de conception : **un modèle ne peut pas
auditer la dette qu'il a lui-même produite**. S'il pouvait la voir, il ne l'aurait
pas produite. La dette résiduelle est exactement l'ensemble de ses angles morts.
Donc détecter cette dette exige une capacité strictement supérieure au producteur.

## Décision

**PM - Claude Code CLI (API Anthropic)**
- Phase 1 (cadrage, fiche racine) : Opus 4.x. Raisonnement de haut niveau, architectural.
  Invoqué une seule fois par run au démarrage, et en cas de blocage uniquement.
- Phase 3 (raffinement, fiches dépendantes) : Sonnet 4.6. Mode de croisière du PM.
- Coordination inter-phases (transitions triviales) : Python pur. 0 token.
- Phase 10 (clôture) : Python pur. 0 token.

**Architecte - Claude Sonnet 4.6 (API Anthropic)**
- Phases 2, 5, 9 : audit non-fonctionnel, détection doublons, factorisation, documentation.
- Sonnet domine Qwen (le producteur). Barre suffisante pour le principe.
- Pas besoin d'Opus : l'auditeur doit dominer le producteur, pas le cadreur.

**Agents producteurs - Ollama local**
- Back, Front, Test : Qwen 2.5 7B Instruct (Q4_K_M, ~4.5 Go).
- Un seul modèle chargé à la fois (contrainte RAM 24 Go avec containers Podman actifs).

**Sécu - Claude Sonnet 4.6 (API Anthropic), deux couches complémentaires**
- SAST déterministe (Semgrep, Bandit — voir `config/studio.yml` section `sast`) :
  premier passage, zéro token.
- Agent Sécu (Sonnet) : second passage sur ce que le SAST ne couvre pas (logique
  métier, autorisation, cohérence globale).

**Fallback** : si un agent échoue après plusieurs itérations, reprise manuelle avec
Cursor ou Claude Code. Pas de fallback API automatique (contrôle des coûts).

## Raisons

1. **Principe auditeur/producteur** : Qwen produit le code. Sonnet audite.
   Sonnet > Qwen en capacité de raisonnement. La barre est respectée sans
   atteindre Opus, ce qui préserve l'objectif de minimisation des tokens API.

2. **Contrainte RAM** : avec macOS + Podman + containers v1 prod, environ 6-10 Go
   disponibles pour Ollama. Un modèle 7B en Q4 consomme ~4.5 Go. Un 14B serait tendu.

3. **Opus uniquement pour la réflexion haute** : le cadrage du projet et le découpage
   en fiches sont les tâches les plus complexes et les plus structurantes. Elles justifient Opus. L'exécution (code, tests, audit) est guidée par les fiches et ne nécessite pas Opus.

4. **Sonnet pour le raffinement** : les fiches dépendantes sont plus structurées que le
   cadrage initial. Sonnet 4.6 est suffisant et 3-5x moins cher qu'Opus.

5. **Qwen 2.5 7B pour l'exécution locale** : bon compromis code/raisonnement pour 7B.
   A benchmarker contre Qwen 2.5 Coder 7B et Qwen 2.5 14B une fois le pipeline stable.

6. **SAST déterministe** : attrape le volume connu de vulnérabilités (injections,
   secrets en dur, patterns classiques) sans aucun token et sans plafond cognitif.
   Complément naturel à l'agent Sécu pour la couverture.

7. **Le modèle est une variable de config** : `config/studio.yml` déclare les modèles
   (clé `agent_auditor` pour Architecte/Sécu, `agents_local` pour Back/Front/Test).
   Changer de modèle ne nécessite pas de modifier le code.

## Métriques de segmentation tokens

Les tokens sont comptés séparément :
- Tokens API Opus (coût €, surveiller de près) : PM phase 1
- Tokens API Sonnet (coût €, surveiller) : PM phase 3 + Architecte + Sécu
- Tokens Ollama local (coût électricité, surveiller pour RAM/perf) : Back, Front, Test
- Tokens fallback manuel Cursor/Claude Code (coût € + temps humain)

## Conséquences

- Les runs sont plus coûteux en tokens API qu'une architecture tout-local : Opus au
  démarrage (phase 1), puis Sonnet à chaque run pour Architecte et Sécu (correction de
  2026-07). Choix délibéré : qualité d'audit > économie maximale de tokens.
- Si Ollama OOM pendant un run, l'agent marque sa fiche en échec et notifie via Telegram.
- Benchmarking des modèles Ollama à faire après stabilisation du pipeline LangGraph.

## Alternatives rejetées

- **Tout Opus** : coût prohibitif pour l'exécution et l'audit.
- **Tout local (Qwen pour tout)** : viole le principe auditeur/producteur. Un Qwen
  ne peut pas auditer correctement la dette d'un autre Qwen. Rejeté.
- **Fallback API automatique** : perte de contrôle sur les coûts. Rejeté.
- **Deux modèles Ollama simultanés** : RAM insuffisante sur 24 Go avec Podman actif.
- **Qwen en auditeur sécu** : rejeté explicitement. L'audit sécu par le même modèle
  que le producteur ne détecte pas ses propres angles morts.
