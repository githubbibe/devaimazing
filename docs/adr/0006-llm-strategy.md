# ADR 0006 - Stratégie LLM Opus/Sonnet/Qwen

**Date** : 2026-06  
**Mis à jour** : 2026-07 (correction auditeurs), 2026-07-22 (canal de notification ntfy)  
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

**Révision (2026-08-05) — cascade de modèles locaux plutôt qu'un modèle unique** :
`models.agents_local` (config/studio.yml) passe d'une chaîne unique à une **liste
ordonnée** : `qwen2.5:7b-instruct → qwen2.5:14b-instruct → devstral:24b → gpt-oss:20b`.
Gap constaté en run réel (todolist3, run `gestion-taches`) : trois activations
complètes de l'agent Back avec le même modèle (7b puis 14b) ont produit **trois fois
le même bug de syntaxe périmée** (`from pydantic import BaseSettings` — API pydantic
v1, incompatible avec pydantic v2 pourtant demandé ; SQLAlchemy synchrone au lieu
d'async) — zéro convergence sur 6 activations cumulées. Un modèle qui régénère
depuis les mêmes poids ne « réessaie » pas différemment de lui-même à chaque
itération : la boucle `max_iterations` (3 tentatives identiques) n'apportait donc
rien pour cette classe d'échec (connaissance d'API périmée), à la différence d'un
bug ponctuel qu'un nouveau tirage aléatoire du même modèle peut corriger.

`studio.routing.model_for_attempt` sélectionne le modèle selon
`agent_iteration_count(state, agent)` (même indexation que le compteur
`max_iterations` déjà existant) — **entre chaque activation complète**, jamais au
sein de la boucle de correction ciblée interne à une activation
(`inner_retry_limit`, studio.nodes.backend/frontend.py) : une correction ciblée
répond à une erreur précise déjà montrée au modèle, changer de modèle en cours de
correction lui ferait perdre ce contexte. `agents.max_iterations` passe de 3 à 4
pour couvrir toute la cascade (sinon `gpt-oss:20b`, dernier maillon, ne serait
jamais atteint). Compatibilité ascendante : une chaîne unique reste valide
(normalisée en cascade à un seul élément), aucun projet existant n'a besoin d'être
migré pour continuer à fonctionner comme avant.

Ordre du moins cher (7B, le plus rapide) au plus capable, cohérent avec le principe
coût-d'abord de cet ADR — la plupart des fichiers simples réussissent dès le premier
modèle, la cascade n'entre en jeu que sur les échecs réels. `devstral:24b` est
spécifiquement entraîné pour du coding agentique (SWE-bench) — candidat le plus
pertinent pour ce type d'échec, positionné avant `gpt-oss:20b` (généraliste) plutôt
qu'après.

**Distinct du « fallback API automatique » explicitement rejeté plus bas** : la
cascade reste entièrement Ollama (coût électricité, pas de tokens API) — elle ne
remet pas en cause l'arbitrage de contrôle des coûts, juste le choix de répéter
aveuglément un modèle qui a déjà montré ses limites de connaissance plutôt que d'en
essayer un autre du même budget (local, gratuit).

**Non vérifié à ce stade** : la fenêtre de contexte `ollama.num_ctx` (32768,
calibrée sur qwen2.5:7b-instruct) n'a pas été revalidée pour les trois autres
modèles de la cascade — à surveiller si des troncatures de prompt apparaissent en
usage réel avec devstral/gpt-oss.

**Révision (2026-08-06) — ordre de la cascade inversé (le plus capable en
premier), et retour au contrat texte par délimiteurs pour Back/Front/Test** :

`models.agents_local` passe de `qwen2.5:7b-instruct → qwen2.5:14b-instruct →
devstral:24b → gpt-oss:20b` à `devstral:24b → gpt-oss:20b →
qwen2.5:14b-instruct → qwen2.5:7b-instruct`. Raison : `studio.routing`
réinitialise `agent_iteration_count` (donc l'index de cascade) à 0 à chaque
nouvel essai (retry après FAILED, ou reprise après un blocage
`WAITING_HUMAN`) — l'ordre "du moins cher au plus capable" retenu le
2026-08-05 signifiait donc que chaque *retry* recommençait sur le modèle le
plus faible, pas seulement la première activation d'un run. Sur le run réel
todolist3/gestion-taches, ça s'est traduit par des dizaines de cycles perdus
sur `qwen2.5:7b-instruct` avant d'atteindre `devstral:24b`/`gpt-oss:20b`, qui
convergeaient nettement plus souvent. Le principe coût-d'abord de cet ADR
reste valable pour le choix des modèles retenus (tous locaux, aucun coût
API) ; ce qui change est l'ordre à l'intérieur de la cascade, optimisé pour
le taux de convergence par essai plutôt que pour épuiser le moins cher
avant le plus cher.

Distinct de la cascade : abandon de `tools.ollama.FILE_OUTPUT_SCHEMA` (sortie
JSON contrainte par grammaire, introduite le 2026-07-11) pour Back/Front/Test,
retour au contrat texte par délimiteurs `<<<DEVAIMAZING_FILE>>>`. Expérience
A/B en conditions réelles (même modèle, même prompt, même fiche réelle) :
0/4 générations propres en JSON contraint (2 blocages, 1 réponse vide, 1
essai avec `SyntaxError` dès la ligne 1 sur les deux fichiers produits)
contre 1/1 en texte délimité. Contraindre le contenu d'un fichier à être une
valeur de chaîne JSON échappée dégradait la fidélité de génération du code
(syntaxe des commentaires, indentation) au-delà du seul risque d'échappement
de guillemets envisagé à l'introduction du schéma — voir
`docs/roadmap.md` (2026-08-06) pour le détail de l'expérience.

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
- Si Ollama OOM pendant un run, l'agent marque sa fiche en échec et notifie via ntfy.
- Benchmarking des modèles Ollama à faire après stabilisation du pipeline LangGraph.

## Alternatives rejetées

- **Tout Opus** : coût prohibitif pour l'exécution et l'audit.
- **Tout local (Qwen pour tout)** : viole le principe auditeur/producteur. Un Qwen
  ne peut pas auditer correctement la dette d'un autre Qwen. Rejeté.
- **Fallback API automatique** : perte de contrôle sur les coûts. Rejeté.
- **Deux modèles Ollama simultanés** : RAM insuffisante sur 24 Go avec Podman actif.
- **Qwen en auditeur sécu** : rejeté explicitement. L'audit sécu par le même modèle
  que le producteur ne détecte pas ses propres angles morts.
