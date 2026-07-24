# ADR 0013 - Interface Telegram native, agent Devaimazing, modèle d'outils à confirmation universelle

**Date** : 2026-07
**Statut** : Accepté (décision de conception — implémentation non commencée, voir
« Conséquences »)

## Contexte et retournement assumé

Le 2026-07-22 (commit `bf8e0ab`), ce dépôt a supprimé la vision produit
OpenClaw/Telegram/PWA mobile, confirmée abandonnée à ce moment-là : le dossier stub
`interfaces/telegram-bridge/` a été retiré, `ARCHITECTURE.md` reformulé pour ne
documenter que la CLI comme interface de pilotage réelle, et toute mention
OpenClaw/Telegram retirée de la documentation.

**Cet ADR révise ce choix**, un jour plus tard, sur la base d'une nouvelle réflexion
de cadrage. Ce n'est pas un aller-retour arbitraire : la suppression du 2026-07-22
retirait une vision **non tranchée et non backée** (un stub sans mécanisme précis,
notifications ntfy en solution transitoire faute de mieux). La présente réflexion
tranche explicitement l'architecture cible (groupe Telegram unique à topics, agent
dédié, modèle d'outils) là où la version abandonnée restait à l'état d'intention
vague. La décision d'abandonner la PWA comme prochaine étape est confirmée et
renforcée ici, pour une raison de fond : Telegram fournit gratuitement ce qu'une PWA
demanderait de coder à la main (persistance des fils de conversation, historique,
notifications push natives, multi-device, authentification), alors que le coût
principal de la PWA (HTTPS obligatoire, service worker, VAPID keys, contraintes iOS
sur le push) est disproportionné face à ce gain. C'est un critère de minimisation du
code pour un niveau de fiabilité acceptable, cohérent avec la philosophie du projet
(agents locaux plutôt que services managés coûteux, voir ADR 0006).

**Portée de cet ADR : c'est une décision de conception, pas une implémentation.**
Aucun bot Telegram, aucun node LangGraph, aucun registre d'outils n'existe à ce jour
dans `runtime/`. Cet ADR documente l'architecture cible et le rôle d'agent qui la
porte, pour qu'une implémentation future s'y conforme sans redécouvrir ces choix —
exactement le rôle qu'un ADR joue déjà pour d'autres décisions de ce dépôt en avance
sur leur implémentation complète.

## Décision 1 — Interface Telegram native, PWA abandonnée comme prochaine étape

Telegram redevient l'interface principale de pilotage à part entière (pas seulement
un canal de notification comme envisagé transitoirement avant le 2026-07-22).
L'application web (PWA) n'est plus la prochaine étape planifiée ; elle reste une
possibilité lointaine, non planifiée.

**Conséquence sur `ntfy`** : `ntfy` (canal de notification actuellement actif,
consommé par `nodes/closer.py`) n'est **pas retiré** par cet ADR. Une fois
l'interface Telegram implémentée, elle couvrira nativement le besoin de notification
(push natif du groupe/topics), ce qui rend `ntfy` obsolète en tant que canal
principal. Mais retirer `ntfy` aujourd'hui casserait une fonctionnalité réellement
utilisée par du code réel, pour la remplacer par rien (aucune implémentation
Telegram n'existe encore). Décision : `ntfy` reste le canal actif jusqu'à ce que
l'implémentation Telegram absorbe ce besoin, puis devient un secours documenté (si
Telegram est indisponible) plutôt qu'être supprimé sèchement. Ce séquencement évite
de reproduire, en sens inverse, le problème que la suppression du 2026-07-22 a
justement corrigé (documentation en avance sur une implémentation qui ne suit pas).

## Décision 2 — Un groupe Telegram unique avec topics

**Retenu** : un seul groupe Telegram, topics activés, un topic = un projet piloté.
Un topic spécial « General » fait office de superviseur transverse (voir Décision 3).
Un seul bot Telegram (un seul token), membre du groupe, actif dans tous les topics.

**Rejeté — plusieurs groupes distincts** (un par famille de projet) : complexifie
inutilement le routage inter-groupes (`forwardMessage`/`copyMessage` entre groupes
distincts, plusieurs tokens ou un bot multi-groupes) pour un bénéfice d'isolation
qu'un seul groupe à topics offre déjà nativement.

**Rejeté — un seul chat privé avec commande `/switch`** : mélange tout dans un seul
fil visuel, sans étanchéité entre projets — contraire à l'exigence de ne pas mélanger
les conversations entre projets.

**Sécurité** : le bot ne répond qu'au `chat_id` de l'utilisateur du studio
(mono-utilisateur à ce stade, cohérent avec le reste de l'architecture devaimazing).
Tout message d'un autre `chat_id` est ignoré.

**Droits admin du bot** : `can_manage_topics` uniquement (nécessaire pour
`createForumTopic`/`closeForumTopic`). Principe du droit minimal strictement
nécessaire — pas de suppression de messages, pas de bannissement, pas d'épinglage.

## Décision 3 — Le topic « General » et l'agent Devaimazing

**Nouveau rôle** : Devaimazing, distinct des 8 rôles du pipeline de run existant (PM,
Architecte, Back, Back-tu, Front, Front-tu, Test, Sécu). Nom retenu volontairement
identique à celui du studio — assumé, pas une coïncidence à corriger. C'est la
première interface conversationnelle du studio, pensée comme préfiguration de
l'interface d'un futur produit SaaS : un rôle d'orientation et de secrétariat, pas un
9ᵉ agent de production ou d'audit.

**Ce qu'il fait** (voir `prompts/devaimazing.md` pour la spécification complète) :
1. Crée un topic-projet à la demande (`createForumTopic`), écrit le `thread_id`
   obtenu dans le fichier de config du projet correspondant sous `config/projects/`.
2. Ferme un topic-projet à l'archivage (`closeForumTopic` — fermeture réversible,
   jamais de suppression définitive du topic ni de son historique).
3. Répond aux questions de mode d'emploi du studio en s'appuyant sur la
   documentation existante (README, docs).
4. Répond directement aux questions factuelles sur l'état d'un projet quand la
   réponse est déjà disponible dans les fichiers existants (`project-map.md`, état
   du run courant) — zéro token payant pour ce cas, sans solliciter le PM.
5. Transfère au PM du projet concerné toute question nécessitant un jugement. Le PM
   répond alors **dans le topic du projet concerné**, jamais dans General.
6. Détecte qu'un message tapé dans General concerne probablement un projet précis
   (mécanisme retenu : correspondance textuelle simple sur le nom ou un alias connu
   du projet — jugé suffisant pour ce volume, pas de classification sophistiquée) et
   propose de le transférer (`copyMessage` vers le topic-projet, accusé de réception
   court dans General sans dupliquer le contenu). L'utilisateur confirme ou refuse.
7. Comprend des demandes d'action en langage naturel (pas seulement des commandes
   slash) et déclenche les outils correspondants (voir Décision 4) — c'est ainsi que
   Devaimazing compense l'absence de boutons d'interface graphique qu'une PWA aurait
   offerts : la compréhension du langage remplace le clic.
8. Prend note des idées d'amélioration exprimées en vrac dans `IMPROVEMENTS.md` à la
   racine du dépôt.

**Ce qu'il ne fait pas** : aucune réflexion de run (cadrage, implémentation, audit) ;
ne répond jamais lui-même à une question de jugement (transfert systématique au PM) ;
n'exécute jamais d'action destructrice sans passer par le mécanisme de confirmation
(Décision 4).

**LLM retenu : Gemma local** (Ollama), pas Qwen, ni Sonnet, ni Opus. Contrairement
aux agents producteurs (Back/Front/Test, Qwen 2.5 7B, orienté code), Devaimazing n'écrit
et n'audite jamais de code : son cœur de métier est la conversation naturelle
(compréhension d'intention, gestion de l'ambiguïté, ton). Un modèle généraliste comme
Gemma est jugé mieux adapté à cet usage qu'un modèle orienté code comme Qwen, dont la
force est ailleurs. Le principe « l'auditeur doit dominer le producteur » (principe 4,
`ARCHITECTURE.md`) ne s'applique pas à Devaimazing, puisqu'il n'audite ni ne produit
de code. L'objectif de minimisation des tokens payants s'applique pleinement :
Devaimazing fait lui-même tout ce qu'il peut faire sans monter en compétence coûteuse,
et ne sollicite le PM (Sonnet ou Opus selon la phase) que lorsque c'est réellement
nécessaire.

**Mémoire** : pas de checkpointer dédié séparé (contrairement au PM). Tient en deux
choses : (1) les fichiers de config existants `config/projects/*.yml`, qui porteront
le `thread_id` du topic Telegram associé à chaque projet ; (2) sa présence dans la
conversation elle-même — membre du groupe, donc « présent » dans tous les topics
(y compris topics-projets), même s'il n'y écrit que ponctuellement (transfert).

**Explicitement écarté** : un système RAG complet (base vectorielle, embeddings,
recherche sémantique) — disproportionné par rapport au volume de documentation réel
(quelques README, quelques fiches par projet) et contraire au critère de
minimisation du code. À la place : accès direct en lecture aux fichiers pertinents,
injectés en contexte à la demande, sans étage d'indexation vectorielle.

## Décision 4 — Modèle d'outils et règle de confirmation universelle

**Le point le plus structurant de cette réflexion.**

Devaimazing et les commandes slash Telegram partagent le **même registre
d'outils** sous-jacent. La confirmation avant exécution n'est pas une propriété du
canal d'appel (commande slash tapée vs intention comprise en langage naturel par
Devaimazing) — c'est une **propriété de l'outil lui-même**.

Chaque outil déclare :
- `destructif: bool` — l'action est-elle destructrice ou irréversible dans ses
  conséquences (fermer un topic sans le supprimer, ou arrêter un run en cours,
  comptent comme destructifs au sens de cette règle) ?
- `requiert_confirmation: bool` — généralement égal à `destructif`, laissé comme
  propriété distincte au cas où un outil non destructeur mériterait quand même une
  confirmation pour une autre raison
- `sauvegarde_avant: bool` — si vrai, un `git commit` (« sauvegarde avant
  destruction ») suivi d'un `git push` est exécuté sur l'état courant du run avant
  que l'action destructrice ne s'exécute

**Classification retenue à ce stade** (à ajuster à l'implémentation si d'autres
outils s'avèrent nécessaires) :

| Outil | `destructif` | `requiert_confirmation` | `sauvegarde_avant` |
|---|---|---|---|
| `stop_run` | true | true | true |
| `archive_projet` (fermeture de topic) | true | true | true |
| `reject_checkpoint` | true (probable — annule une progression) | true | true |
| `lire_statut` | false | false | false |
| `lire_progression` | false | false | false |
| `creer_projet` (création de topic) | false (additif) | false | false |

**Deux voies d'appel du même outil** :
- **Commande slash directe** (ex : `/stop` tapé dans le topic-projet) : le run
  concerné est déjà déterminé par le topic (pas d'ambiguïté à lever). L'outil est
  appelé directement. Si l'outil `requiert_confirmation`, la confirmation est quand
  même demandée — la commande slash dispense seulement de l'étape d'identification
  du run, jamais de la confirmation elle-même.
- **Langage naturel via Devaimazing** (ex : « arrête le run » en texte libre) :
  Devaimazing identifie d'abord le run concerné (immédiat si le contexte du topic
  suffit ; sinon il liste les runs actifs et demande lequel est visé). Une fois
  identifié, la même règle de confirmation par propriété de l'outil s'applique, avec
  affichage du statut actuel du run pour une confirmation éclairée (ex : « Le run
  fait actuellement X, en phase Y. Confirmer son arrêt ? »).

**Implémentation attendue** : un registre d'outils unique, consommé de façon
identique que l'appel vienne du parsing des commandes slash Telegram ou du
function-calling de l'agent Devaimazing (Gemma). Pas de duplication de logique entre
les deux voies d'entrée. Ce registre n'existe pas encore dans
`runtime/studio/tools/` au moment de cet ADR — les fichiers actuels de ce dossier
(`claude_code.py`, `filesystem.py`, `git.py`, `ollama.py`, `pyenv.py`, `tracer.py`)
sont des wrappers utilitaires, pas un mécanisme de déclaration d'outils avec
métadonnées de confirmation.

**Commandes slash retenues à ce stade** (à consolider à l'implémentation) :
- Topic General : `/new`, `/projects`, `/archive`, `/status` (agrégé),
  `/progression` (agrégée), `/help`
- Topic-projet : `/status`, `/resume`, `/reject`, `/stop`, `/progression`, `/help`

## Conséquences

- `prompts/devaimazing.md` est créé : spécification complète du rôle (identité,
  responsabilités, ce qu'il ne fait pas, format de sortie), sur le modèle des
  prompts d'agent existants.
- `docs/agents.md` gagne une section Devaimazing, explicitement marquée « décidé,
  pas encore implémenté », distincte des 8 rôles du pipeline de run (elle ne
  modifie ni le compte ni la séquence de ce pipeline — Devaimazing n'est pas un
  node du graphe LangGraph à 6 nodes, voir ADR 0005).
- `ARCHITECTURE.md` : la section « Interface de pilotage » documente ce choix comme
  décidé mais non implémenté, sans remplacer la description de la CLI comme unique
  canal réel aujourd'hui. Un nouveau composant externe « Interface Telegram » est
  ajouté avec le même statut. La section Notifications (ntfy) est mise à jour pour
  refléter le statut transitoire de `ntfy` décrit en Décision 1. La liste des
  décisions clés gagne cette entrée.
- `docs/workflow.md` (Phase 0 - Réception) gagne une note sur ce canal d'entrée
  décidé, sans changer la mécanique de phase actuelle.
- `README.md` : la structure du dépôt liste `prompts/devaimazing.md` et cet ADR
  (fichiers réels sur disque). Aucune section aspirational (diagramme d'architecture,
  tableau stack technique) n'est modifiée : ces sections décrivent le système
  réellement en exécution aujourd'hui, pas les décisions non encore implémentées.
- **Non fait par cet ADR, laissé à une implémentation future distincte** : le bot
  Telegram lui-même, le node/mécanisme d'intégration de Devaimazing au runtime, le
  registre d'outils avec métadonnées de confirmation, la modification de
  `config/projects/*.yml` pour porter `thread_id`, toute modification de
  `runtime/studio/`. Ce découplage explicite évite de refaire l'erreur corrigée le
  2026-07-22 (documentation décrivant un système qui n'existe pas comme s'il
  tournait).

## Points laissés ouverts, tranchés ici par défaut faute d'être structurants

- **Détection « ce message concerne tel projet »** : correspondance textuelle
  simple sur le nom/alias du projet, retenue comme suffisante pour le volume actuel.
  Revisable si un problème de fiabilité apparaît en usage réel.
- **Nom du fichier de notes d'amélioration** : `IMPROVEMENTS.md`, à la racine du
  dépôt, retenu sans attachement particulier au nom.
- **Liste des commandes slash** : consolidée ci-dessus (Décision 4), révisable à
  l'implémentation sans nécessiter de nouvel ADR pour des ajustements mineurs.

## Alternatives rejetées

- **Plusieurs groupes Telegram séparés par famille de projet** : complexité de
  routage inter-groupes non justifiée par le bénéfice (voir Décision 2).
- **Chat privé unique avec commande `/switch`** : perd l'étanchéité entre projets
  (voir Décision 2).
- **RAG complet pour la mémoire de Devaimazing** : disproportionné par rapport au
  volume de documentation réel (voir Décision 3).
- **Retirer `ntfy` immédiatement** : casserait un canal de notification réellement
  utilisé par du code réel (`nodes/closer.py`) sans remplaçant fonctionnel disponible
  (voir Décision 1).
- **Confirmation dépendant du canal d'appel** (slash = pas de confirmation,
  langage naturel = confirmation systématique, ou l'inverse) : rejetée au profit
  d'une confirmation propriété de l'outil, uniforme quel que soit le canal (voir
  Décision 4) — la sécurité d'une action ne doit pas dépendre de la façon dont
  l'utilisateur l'a formulée.
- **Qwen comme LLM de Devaimazing** (cohérence avec les agents producteurs
  Back/Front/Test) : rejeté au profit de Gemma, plus généraliste — le cœur de métier
  de Devaimazing est la conversation naturelle, pas la production de code, terrain
  où Qwen n'a pas d'avantage particulier (voir Décision 3).
