# Architecture de devaimazing

Ce document décrit les décisions d'architecture structurantes du studio.
Les ADR détaillés sont dans `docs/adr/`. La topologie réseau complète est décrite
dans `docs/infra-topology.md`.

## Vue d'ensemble

devaimazing est un graphe LangGraph de 6 nodes orchestrant 8 rôles d'agent spécialisés
séquentiellement (Back-tu et Front-tu partagent le node et l'identité Git de Back/Front,
mais interviennent comme des activations distinctes avec leur propre périmètre — voir
`docs/agents.md`). Le runtime Python est le seul chef d'orchestre. Aucun agent ne pilote
un autre agent. Choix d'un orchestrateur custom plutôt que Claude Code remote/subagents :
voir ADR 0011.

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

**4. Auditeur doit dominer le producteur**
Les agents producteurs (Back, Front, Test) tournent sur Qwen 2.5 7B local. Les agents
auditeurs (Architecte, Sécu) tournent sur Claude Sonnet, qui domine Qwen en capacité.
Un modèle ne peut pas auditer correctement la dette qu'il a lui-même produite : s'il
la voyait, il ne l'aurait pas laissée passer à la génération. Ce principe s'applique
aussi à l'intention (voir principe 8) : le cadreur (PM) doit dominer la dette
d'intention qu'il pourrait lui-même introduire au cadrage.

**5. Cadrage itératif, exécution rigide**
La phase 1 (cadrage par le PM) est un dialogue de raffinement successif avec
l'utilisateur. Une fois la fiche racine validée, le run suit une topologie de graphe
fixe et testée (voir ADR 0005). Aucune improvisation de flux pendant l'exécution :
la souplesse se joue dans le cadrage, pas dans l'orchestration.

**6. Commits incrémentaux, points de restauration**
Un commit est réalisé à la fin de chaque tâche d'agent (pas seulement en phase 10).
Chaque commit est signé sous l'identité Git de l'agent. Voir ADR 0007.

**7. Validation humaine progressive**
Les checkpoints humains sont obligatoires au démarrage (phases 1, 2, 3, 5, 9).
Ils passent en automatique au fur et à mesure que le système est maîtrisé.

**8. Checklist d'intention produit en phase 1, aucun trou comblé par défaut**
Le PM, en casquette product owner, anime en phase 1 une checklist qui force, pour
chaque dimension du produit cible, trois questions : la dimension existe-t-elle
comme axe de contrôle distinct ? L'utilisateur final peut-il en garder ou en déléguer
le contrôle ? Le choix est-il explicite ou implicite (le système décide par défaut) ?
Toute dimension où le système déciderait par défaut sans choix explicite est marquée
comme dette d'intention en puissance et remonte au checkpoint humain. **Le PM ne
comble jamais un trou d'intention par une valeur par défaut « raisonnable » : un trou
remonte à l'humain, il n'est pas rempli par l'agent.** Voir ADR 0008.

**9. Pseudonymisation by design pour toute donnée comportementale**
Tout projet produit par devaimazing qui collecte des données comportementales
utilisateur pseudonymise ces données par construction avant tout export vers un
système d'observabilité : aucun identifiant direct ne transite dans le flux exporté.
La table de correspondance pseudonyme ↔ identité réelle est physiquement étanche
(accès réseau et applicatif restreints à l'admin). Toute ré-identification est
elle-même tracée. Le cloneur d'un projet choisit explicitement si ses données
restent locales (`analytics_mode: local`) ou sont partagées vers un système mutualisé
(`analytics_mode: shared`). Voir ADR 0009.

**10. Quatre piliers non-fonctionnels obligatoires, dette toujours justifiée**
Résilience, gestion d'erreurs, scalabilité, observabilité sont des dimensions
non-fonctionnelles obligatoires, vérifiées par l'Architecte en phase 2 et maintenues
en phase 9 (architect-map), pour devaimazing lui-même et pour tout projet produit.
Symétrique au principe 8 côté technique : toute dette assumée sur un pilier (ex.
scalabilité limitée par les ressources CPU disponibles) doit être explicitement
justifiée et documentée, jamais laissée en silence. Une section de contraintes
non-fonctionnelles vide sans justification remonte au checkpoint humain. Performance,
disponibilité et accessibilité sont des dimensions reconnues par les standards
industriels mais explicitement hors champ au stade POC/LVP : notées comme dette de
périmètre connue et différée, pas comme trou silencieux, à réintégrer au garde-fou
quand le projet dépasse ce stade. Voir ADR 0010.

**11. Checklist sécurité et gestion des secrets en phase 1, distincte de la checklist
d'intention**
Le PM anime en phase 1, en plus de la checklist d'intention (principe 8), une seconde
checklist portant sur les secrets du projet cible (mots de passe admin, certificats,
clés API) : contrainte légale applicable, exigence sponsor au-delà du minimum légal,
niveau de gestion des secrets requis — ou, à défaut de contrainte identifiée, le niveau
par défaut (secrets jamais en clair dans le repo, gérés via un outil tiers de gestion de
secrets). Les deux checklists ne fusionnent pas : l'une porte sur le contrôle
utilisateur par dimension produit, l'autre sur des contraintes légales/contractuelles
externes. La réponse est inscrite dans `card-root.md` et **respectée** par l'Architecte
en phase 2, jamais redécouverte ni redéfinie par lui. L'agent Sécu audite la conformité
à cette contrainte en phase 8, il ne définit aucune politique de sécurité lui-même.
Symétrique au principe 8 (l'erreur naît à la racine du run, pas en audit aval). Voir
ADR 0012.

## Composants externes

devaimazing core est strictement le runtime LangGraph + ses 6 nodes (8 rôles d'agent) +
ses outils locaux. Tout ce qui touche à l'interface utilisateur, aux notifications et à
l'observabilité est externe au core.

**Notifications (ntfy)**

Le node Closer (phase 10) envoie une notification via ntfy à chaque point de sortie
du flux : échec d'un agent, checkpoint humain en attente, fin de run. Canal retenu :
ntfy.sh (service public), pour une portabilité maximale sans setup serveur dédié.

Les notifications ne contiennent jamais de lien : le message est auto-suffisant
(constat brut de l'erreur, sans suggestion d'action). Voir `docs/workflow.md` pour
le détail des formats par point de sortie.

**Statut vis-à-vis de l'interface Telegram (ADR 0013)** : `ntfy` reste le canal actif
tant que l'interface Telegram n'est pas implémentée — elle couvrira nativement une
partie du besoin de notification une fois construite (push natif du groupe/topics),
mais `ntfy` n'est pas retiré pour autant : il deviendra un canal de secours
documenté (si Telegram est indisponible) plutôt que d'être supprimé avant qu'un
remplaçant fonctionnel existe.

**Interface Telegram (décidée le 2026-07-23, ADR 0013 — pas encore implémentée)**

Un groupe Telegram unique avec topics (un topic par projet piloté, plus un topic
« General » transverse) porte l'interface de pilotage à distance. Un seul bot
Telegram (un seul token), membre du groupe, actif dans tous les topics, ne répond
qu'au `chat_id` de l'utilisateur du studio (mono-utilisateur). Droit admin minimal :
`can_manage_topics` uniquement.

Le rôle **Devaimazing** (Gemma local, voir `docs/agents.md`) porte l'interface
conversationnelle : création/fermeture de topics-projet, orientation des demandes,
réponses factuelles sans solliciter le PM, transfert au PM pour tout ce qui nécessite
un jugement. Il tourne hors du graphe LangGraph à 6 nodes (ADR 0005) : ce n'est pas
un 7ᵉ node du pipeline de run, mais un rôle transverse indépendant.

**Modèle d'outils et confirmation universelle** : les commandes slash Telegram et les
intentions comprises en langage naturel par Devaimazing appellent le même registre
d'outils. Chaque outil déclare `destructif`, `requiert_confirmation`,
`sauvegarde_avant` (commit + push automatique avant toute action destructrice) — la
confirmation est une propriété de l'outil, jamais du canal d'appel. Voir ADR 0013
pour le détail complet et la classification des outils identifiés.

**Messages vocaux (décidé, ADR 0014 — pas encore implémenté)** : un message vocal
Telegram est transcrit par Whisper (ASR local, prétraitement pur, aucune
compréhension) avant d'atteindre Devaimazing, qui reçoit alors un texte traité de
façon strictement identique à un message tapé. Whisper tourne en local (Ollama si
suffisamment mature à l'implémentation, sinon `whisper.cpp`), jamais via l'API
OpenAI payante.

**Observabilité centralisée (Loki + Grafana Alloy)**

L'observabilité repose sur Grafana Alloy (agent unifié, successeur de Promtail —
EOL depuis mars 2026) qui collecte logs et métriques et les pousse vers Loki
(agrégation de logs) et Prometheus (métriques). Grafana reste le point unique de
visualisation, lisant à la fois Loki et les datasources Prometheus prod/dev.

Cette collecte centralisée remplace le besoin d'un outil de diagnostic actif branché
sur la prod : un incident se rejoue depuis les logs structurés (JSON) déjà collectés,
sans avoir besoin d'un environnement de test connecté à la production. Voir
`docs/infra-topology.md` pour le détail du déploiement d'Alloy.

**Interface de pilotage**

Le pilotage réel aujourd'hui se fait exclusivement via la CLI (`devaimazing run`,
`resume`, `retry`, `run-agent`, `runs`, `metrics`, `new-project`, `projects`,
`doctor`) — voir `README.md` section Usage. Pas d'interface graphique ni de canal de
contrôle à distance en exécution à ce jour.

**Décidé, pas encore implémenté (ADR 0013)** : une interface Telegram redevient
l'interface principale de pilotage à part entière, y compris à distance (AFK/mobile).
Ce choix révise la suppression du 2026-07-22 (commit `bf8e0ab`) qui avait retiré la
vision OpenClaw/Telegram/PWA comme non tranchée ; l'abandon de la PWA comme prochaine
étape est confirmé, pour la même raison de fond (minimisation du code pour un niveau
de fiabilité acceptable — Telegram fournit nativement ce qu'une PWA demanderait de
coder à la main). Voir « Interface Telegram » ci-dessous et `docs/agents.md` pour le
rôle Devaimazing qui la porte. Tant que cette interface n'est pas construite, la CLI
reste le seul canal de pilotage réel.

## Décisions clés

Voir `docs/adr/` pour le détail de chaque décision :

- [0001 - Agents stateless sauf PM](docs/adr/0001-stateless-agents.md)
- [0002 - Stub-first](docs/adr/0002-stub-first.md)
- [0003 - SQLite comme checkpointer](docs/adr/0003-sqlite-checkpointer.md)
- [0004 - AGPL-3.0](docs/adr/0004-agpl-licence.md)
- [0005 - LangGraph comme orchestrateur](docs/adr/0005-langgraph.md)
- [0006 - Stratégie LLM Opus/Sonnet/Qwen](docs/adr/0006-llm-strategy.md)
- [0007 - Nommage de branche et commits incrémentaux](docs/adr/0007-branch-naming-and-incremental-commits.md)
- [0008 - Checklist d'intention produit en Phase 1](docs/adr/0008-checklist-intention-phase1.md)
- [0009 - Pseudonymisation et traçabilité anti-fraude](docs/adr/0009-pseudonymisation-anti-fraude.md)
- [0010 - Quatre piliers non-fonctionnels obligatoires et dette justifiée](docs/adr/0010-quatre-piliers-non-fonctionnels-dette-justifiee.md)
- [0011 - Orchestrateur custom plutôt que Claude Code remote/subagents](docs/adr/0011-orchestrateur-custom-vs-claude-remote.md)
- [0012 - Checklist sécurité et gestion des secrets en Phase 1](docs/adr/0012-checklist-secrets-phase1.md)
- [0013 - Interface Telegram native, agent Devaimazing, modèle d'outils à confirmation universelle](docs/adr/0013-interface-telegram-agent-devaimazing.md)
- [0014 - Transcription vocale (Whisper) en amont de l'agent Devaimazing](docs/adr/0014-whisper-transcription-vocale.md)
