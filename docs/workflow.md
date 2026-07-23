# Workflow devaimazing - Les 10 phases

## Vue d'ensemble

Un run devaimazing prend un objectif (feature, bugfix, refactor) et produit du code
commité dans le repo projet, documenté, testé et audité, avec traçabilité Git par agent.

Le workflow est **séquentiel**. Un seul run à la fois. Les agents interviennent chacun
leur tour selon la séquence définie par le PM en phase 3.

---

## Phase 0 - Réception

**Qui** : interface + PM  
**Input** : message libre de l'utilisateur  
**Output** : dialogue de cadrage engagé avec le PM  

L'utilisateur envoie un objectif en langage libre. Le PM engage le dialogue de cadrage
(phase 1). Aucune branche Git n'est créée à ce stade.

---

## Phase 1 - Cadrage (itératif + checklist d'intention)

**Qui** : PM (Claude Code CLI, Opus)  
**Input** : message libre de l'utilisateur  
**Output** : `specs/run-NNN/card-root.md` (une fois validée)  
**Checkpoint** : validation humaine obligatoire  

La phase 1 est un **dialogue de raffinement**, pas une génération one-shot. Le PM pose
des questions successives pour affiner l'objectif jusqu'à ce que la fiche racine soit
complète et validée par l'utilisateur.

**Nommage de la feature** : le PM demande explicitement un nom de feature à l'utilisateur
s'il n'en a pas été fourni. C'est l'utilisateur qui choisit le nom (même s'il est peu
soigné) ; le PM ne fabrique un nom que si l'utilisateur ne répond pas ou refuse d'en donner un.

**Checklist d'intention produit (casquette product owner)** : en plus du raffinement de
l'objectif, le PM anime une checklist d'intention. Pour chaque dimension du produit
cible touchée par la feature, il force trois questions :

1. Cette dimension existe-t-elle comme axe de contrôle distinct ?
2. L'utilisateur final (le client qui paie) peut-il prendre ou déléguer le contrôle
   sur cette dimension, indépendamment des autres ?
3. Ce choix est-il explicite (l'utilisateur décide) ou implicite (le système décide
   par défaut) ?

**Toute dimension où le système déciderait par défaut sans choix explicite est
marquée comme dette d'intention en puissance et remonte au checkpoint humain.**

**Interdiction absolue : le PM ne comble jamais un trou d'intention par une valeur
par défaut « raisonnable ». Un trou remonte à l'humain, il n'est pas rempli par
l'agent.** Cette règle existe parce que cette classe d'erreur (une intention mal
posée, pas un bug de code) n'est attrapée ni par les tests ni par un audit de modèle
en aval, et sa cascade est totale puisqu'elle se situe à la racine du run. Voir
ADR 0008 pour le raisonnement complet.

Exemple de dialogue :
```
Utilisateur : je voudrais une nouvelle feature qui ferait ...
PM : ok, donnons-lui un nom, une idée ?
Utilisateur : features-qui-fait-tout
PM : ok, je pars sur ce nom. [poursuite du cadrage : critères d'acceptation, périmètre, contraintes...]
PM : sur la dimension X, qui décide : l'utilisateur final ou le système par défaut ?
Utilisateur : je n'y avais pas pensé.
PM : je le note comme point en suspens, ça remonte à la validation humaine.
```

**Checklist sécurité et gestion des secrets (mécanisme distinct de la checklist
d'intention)** : toujours en phase 1, le PM anime une seconde checklist, sur les
secrets du projet cible (mots de passe admin, certificats, clés API). Les deux
checklists tournent au même moment, sous la responsabilité du même agent, mais ne
fusionnent pas : la checklist d'intention porte sur le contrôle utilisateur par
dimension produit, la checklist sécurité porte sur des contraintes légales et des
exigences de sponsor — un type de question différent. Voir ADR 0012 pour le
raisonnement complet, notamment pourquoi ce sujet est traité au cadrage et non en audit
Sécu après coup (phase 8) : une fois le brief Architecte produit (phase 2), le niveau de
sécurité est déjà implicitement contraint par les choix faits, il est trop tard pour le
poser.

Le PM force quatre questions : une contrainte légale s'applique-t-elle (RGPD, secteur
réglementé, contractuelle) ; le sponsor a-t-il une exigence au-delà du minimum légal ;
cette exigence implique-t-elle un niveau de gestion des secrets particulier (rotation,
chiffrement au repos, séparation des environnements, audit d'accès) ; et à défaut de
toute contrainte identifiée, le niveau par défaut de l'ADR 0012 s'applique (secrets
jamais en clair dans le repo, gérés via un outil tiers de gestion de secrets). Le choix
de l'outil tiers concret n'est pas tranché en phase 1 : c'est une décision de
l'Architecte, projet par projet (voir phase 2 ci-dessous).

**Contenu de la fiche racine, une fois validée** :
- Nom de la feature (fourni par l'utilisateur)
- Objectif reformulé et précisé
- Critères d'acceptation mesurables
- Périmètre (ce qui est inclus et ce qui est explicitement exclu)
- Dimensions de contrôle identifiées (checklist d'intention) et pour chacune :
  explicite/implicite, qui décide
- Contraintes non-fonctionnelles connues (performance, compatibilité), avec une
  sous-section dédiée Sécurité et gestion des secrets (checklist sécurité, ADR 0012)
- Risques identifiés
- Questions en suspens (raffinement, dette d'intention potentielle ET contrainte de
  sécurité non tranchée)

**Le run ne démarre pas et aucune branche n'est créée tant que la fiche racine n'est
pas validée.** Les échanges de cadrage n'ont pas de trace Git.

---

## Phase 2 - Audit amont

**Qui** : Architecte (Claude Sonnet 4.6)  
**Input** : `card-root.md` + `project-map.md` + `architect-map.md`  
**Output** : `specs/run-NNN/architect-brief.md`  
**Checkpoint** : validation humaine obligatoire  

L'Architecte lit la fiche racine et le contexte projet pour produire le brief architectural.

**Contenu du brief architectural** :
- Liste exhaustive des fichiers à créer ou modifier (chemin + rôle + raison)
- Doublons potentiels avec le code existant (comparaison avec `project-map.md`)
- Contraintes non-fonctionnelles à imposer aux agents codants, dont la contrainte de
  sécurité et gestion des secrets posée en phase 1 (`card-root.md`) — reprise telle
  quelle, jamais redéfinie par l'Architecte (voir ADR 0012)
- Zones d'impact pour les tests de non-régression
- Dépendances entre fichiers (ordre de création recommandé)

---

## Phase 3 - Fiches dépendantes

**Qui** : PM (Claude Code CLI, Sonnet)  
**Input** : `card-root.md` + `architect-brief.md`  
**Output** : une fiche par agent dans `specs/run-NNN/`  
**Checkpoint** : validation humaine obligatoire  

Le PM définit la séquence d'exécution et écrit une fiche par agent.

**À la validation de cette phase, la branche du run est créée** :
`studio/<slug-feature>-<hash5>` (voir ADR 0007 pour le détail du nommage).
C'est le premier commit-point du run.

**Séquence type** (adaptée par le PM selon la nature du run) :
```
back → back-tu → front → front-tu → test → secu
```

---

## Phase 4 - Stub-first

**Qui** : Back, Front (Ollama, Qwen 2.5 7B)  
**Input** : fiche agent + skills  
**Output** : fichiers stub dans le repo projet + **commit à la fin de la tâche**  

Chaque agent codant crée les fichiers dans son périmètre avec uniquement signatures,
types, docstrings, contrats. Aucune logique métier.

**Commit** : dès que l'agent termine sa tâche de stub (avant même l'audit de l'Architecte
en phase 5), un commit est réalisé sous son identité Git. C'est un point de restauration :
si la phase 5 déclenche un renvoi, on peut revenir à ce commit.

---

## Phase 5 - Audit des stubs

**Qui** : Architecte (Claude Sonnet 4.6)  
**Input** : tous les stubs produits + `architect-brief.md`  
**Output** : annotations sur les fiches si écart détecté  
**Checkpoint** : validation humaine obligatoire (au début, automatique ensuite)  

**Si écart détecté** : l'Architecte annote la fiche de l'agent fautif. L'agent est
relancé avec sa fiche annotée + ses stubs précédents en input (pas de reprise from scratch).
Il corrige et un nouveau commit est réalisé. L'Architecte re-valide.

---

## Phase 6 - Implémentation

**Qui** : Back, Front, Back-tu, Front-tu (Ollama, Qwen 2.5 7B)  
**Input** : stubs validés + fiche agent + skills  
**Output** : code implémenté + tests unitaires + **commit à la fin de chaque tâche**  

Chaque agent remplit les corps de fonctions selon les stubs validés. Chaque agent
commit dès sa tâche terminée, sous sa propre identité Git.

---

## Phase 7 - Tests transverses

**Qui** : Test (Ollama, Qwen 2.5 7B)  
**Input** : code complet + fiche test + zones d'impact  
**Output** : tests d'intégration + tests de non-régression + **commit**  

**Si un test de non-régression échoue** : l'agent Test annote sa fiche avec le détail
de l'échec (nom du test, output d'erreur). Il **ne corrige ni le test ni le code**.
Le run s'arrête à ce point, une notification est envoyée (voir section notifications
ci-dessous), et une validation humaine est requise avant de reprendre.

---

## Phase 8 - Audit sécurité

**Qui** : SAST déterministe (Semgrep, Bandit) puis Sécu (Claude Sonnet 4.6)  
**Input** : code complet + fiche sécu  
**Output** : rapport dans `specs/run-NNN/security-report.md` + **commit**  

Deux passes : le SAST déterministe tourne en premier (zéro token), puis l'agent Sécu
audite ce que le SAST ne couvre pas (logique métier, cohérence globale, autorisation).
Sur la gestion des secrets, l'agent Sécu audite la **conformité** à la contrainte posée
en phase 1 et déclinée en phase 2 — il ne définit, ne choisit et ne propose aucune
politique de sécurité lui-même (voir ADR 0012).

---

## Phase 9 - Audit aval

**Qui** : Architecte (Claude Sonnet 4.6)  
**Input** : code complet + tous les rapports  
**Output** : documentation complète + **commit**  
**Checkpoint** : validation humaine obligatoire (au début, automatique ensuite)  

L'Architecte produit ADR, mise à jour OpenAPI/README/CHANGELOG/runbooks, et détecte
la factorisation à planifier (sans la faire lui-même).

---

## Phase 10 - Clôture

**Qui** : Python pur (0 token)  
**Input** : état du run, tous les artefacts produits  
**Output** : mise à jour project-map, merge de branche, notification  

Les commits ont déjà été réalisés au fil des phases 4 à 9 (un commit par tâche
d'agent terminée). La phase 10 ne committe plus en bloc. Elle se limite à :

```python
# Pseudo-code de la phase 10 (nodes/closer.py, distinct du node PM)
closer.update_project_map(run)
closer.update_run_history(run)
git.merge_branch_to_develop(run.branch_name)  # après validation finale
notify_success(f"✅ {run.feature_name} terminé")
metrics.finalize(run)
```

---

## Boucle de feedback erreur

Si l'agent N+1 détecte une erreur produite par l'agent N :

1. N+1 annote la section `feedback` de la fiche de N avec le détail de l'erreur.
2. Le runtime LangGraph relance l'agent N avec sa fiche annotée + ses artefacts précédents.
3. L'agent N corrige et commit à nouveau.
4. N+1 reprend sa tâche avec les artefacts corrigés.

Si N échoue après 3 itérations, la fiche est marquée `status: failed`, une notification
est envoyée, et une reprise manuelle (Cursor ou Claude Code) est nécessaire.

---

## Notifications (ntfy)

Chaque point de sortie du flux (échec ou checkpoint) envoie une notification ntfy,
sans lien, message auto-suffisant :

| Point de sortie | Format |
|---|---|
| Échec agent Ollama | `❌ [agent] échec après 3 tentatives — <constat brut>` |
| Échec agent Sonnet | `❌ [agent] échec appel Sonnet — <constat brut>` |
| Échec PM (Claude Code) | `❌ [PM] échec Claude Code CLI — <constat brut>` |
| Échec SAST | `❌ SAST échec — <constat brut>` |
| Non-régression échouée | `❌ [Test] non-régression échouée — <nom test> — <constat brut>` |
| Échec commit Git | `❌ Commit échoué — <agent> — <constat brut>` |
| Checkpoint humain | `⏸ Checkpoint phase <N> — validation requise` |
| Run terminé | `✅ <nom de la feature> terminé` |

Voir ARCHITECTURE.md pour le détail du canal de notification.

---

## Validation humaine progressive

**Phase de démarrage** : checkpoints obligatoires en phases 1, 2, 3, 5, 9.  
**Phase de croisière** : au fur et à mesure que le système est maîtrisé, les checkpoints
peuvent être désactivés un par un via `config/studio.yml`.

**Exception** : le checkpoint de phase 1 ne peut jamais être désactivé si des trous
d'intention ont été détectés par la checklist. Un trou d'intention remonte toujours
à validation humaine, même en mode automatique avancé.
