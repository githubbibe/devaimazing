# Workflow devaimazing - Les 10 phases

## Vue d'ensemble

Un run devaimazing prend un objectif (feature, bugfix, refactor) et produit du code
commité dans le repo projet, documenté, testé et audité, avec traçabilité Git par agent.

Le workflow est **séquentiel**. Un seul run à la fois. Les agents interviennent chacun
leur tour selon la séquence définie par le PM en phase 3.

---

## Phase 0 - Réception

**Qui** : OpenClaw + PM  
**Input** : Message Telegram de l'utilisateur  
**Output** : Run créé, dossier `specs/run-NNN/` initialisé dans le repo projet  

L'utilisateur envoie un objectif via Telegram. OpenClaw transmet au runtime LangGraph.
Le PM crée le dossier du run et enregistre l'objectif brut.

```
specs/
└── run-001/
    └── objective.md    # objectif brut tel que reçu
```

---

## Phase 1 - Cadrage

**Qui** : PM (Claude Code CLI, Opus)  
**Input** : `objective.md`  
**Output** : `specs/run-NNN/card-root.md`  
**Checkpoint** : validation humaine obligatoire  

Le PM (Opus) produit la fiche racine du run. C'est la phase la plus coûteuse en tokens.
Elle est aussi la plus structurante : une fiche racine floue produit des fiches dépendantes
floues et du code erroné.

**Contenu de la fiche racine** :
- Objectif reformulé et précisé
- Critères d'acceptation mesurables
- Périmètre (ce qui est inclus et ce qui est explicitement exclu)
- Contraintes non-fonctionnelles connues (performance, sécurité, compatibilité)
- Risques identifiés
- Questions en suspens (si besoin de validation humaine sur un point précis)

---

## Phase 2 - Audit amont

**Qui** : Architecte (Ollama, Qwen 2.5 7B)  
**Input** : `card-root.md` + `project-map.md` + `architect-map.md`  
**Output** : `specs/run-NNN/architect-brief.md`  
**Checkpoint** : validation humaine obligatoire  

L'Architecte lit la fiche racine et le contexte projet pour produire le brief architectural.

**Contenu du brief architectural** :
- Liste exhaustive des fichiers à créer ou modifier (chemin + rôle + raison)
- Doublons potentiels avec le code existant (comparaison avec `project-map.md`)
- Contraintes non-fonctionnelles à imposer aux agents codants (patterns de retry,
  format des logs, schéma des métriques, règles de résilience)
- Zones d'impact pour les tests de non-régression
- Dépendances entre fichiers (ordre de création recommandé)

---

## Phase 3 - Fiches dépendantes

**Qui** : PM (Claude Code CLI, Sonnet)  
**Input** : `card-root.md` + `architect-brief.md`  
**Output** : une fiche par agent dans `specs/run-NNN/`  
**Checkpoint** : validation humaine obligatoire  

Le PM définit la séquence d'exécution et écrit une fiche par agent.

**Séquence type** (adaptée par le PM selon la nature du run) :
```
back → back-tu → front → front-tu → test → secu
```

**Contenu d'une fiche agent** :
- ID de fiche et run parent
- Agent destinataire
- Position dans la séquence
- Objectif (extrait et précisé depuis la fiche racine)
- Périmètre fichiers (fichiers à créer, modifier, interdiction de toucher)
- Contraintes non-fonctionnelles applicables (référence aux skills)
- Critères de validation (ce que l'agent doit produire pour passer au suivant)
- Section `feedback` (vide au départ, annotée si renvoi en arrière)

---

## Phase 4 - Stub-first

**Qui** : Back, Front (Ollama, Qwen 2.5 7B)  
**Input** : fiche agent + skills  
**Output** : fichiers stub dans le repo projet  

Chaque agent codant crée les fichiers dans son périmètre avec uniquement :
- Signatures de fonctions/méthodes avec types complets
- Docstrings détaillées (objectif, paramètres, retours, exceptions, side effects)
- Exemples d'usage
- Schémas de données
- Contrats d'erreur (codes et messages)
- Imports et dépendances

**Aucune logique métier.** Les corps de fonctions contiennent uniquement `...` ou `pass`.

---

## Phase 5 - Audit des stubs

**Qui** : Architecte (Ollama, Qwen 2.5 7B)  
**Input** : tous les stubs produits + `architect-brief.md`  
**Output** : annotations sur les fiches si écart détecté  
**Checkpoint** : validation humaine obligatoire (au début, automatique ensuite)  

L'Architecte vérifie :
- Cohérence des interfaces entre Back et Front (les API exposées par Back correspondent
  à ce que Front attend)
- Respect du périmètre (pas de fichier créé hors périmètre déclaré)
- Absence de doublons avec le code existant
- Respect des contraintes non-fonctionnelles dans les docstrings
- Complétude des stubs (docstrings suffisamment détaillées pour guider l'implémentation)

**Si écart détecté** : l'Architecte annote la section `feedback` de la fiche de l'agent
fautif. L'agent est relancé avec sa fiche annotée + ses stubs en input. Il corrige
et repropose. L'Architecte re-valide.

---

## Phase 6 - Implémentation

**Qui** : Back, Front, Back-tu, Front-tu (Ollama, Qwen 2.5 7B)  
**Input** : stubs validés + fiche agent + skills  
**Output** : code implémenté + tests unitaires  

Chaque agent remplit les corps de fonctions selon les stubs validés.
Les agents `-tu` (test unitaire) écrivent les tests unitaires en parallèle
(même séquence, après leur agent codant respectif).

L'agent Back-tu a accès aux stubs et à l'implémentation de Back.
L'agent Front-tu a accès aux stubs et à l'implémentation de Front.

---

## Phase 7 - Tests transverses

**Qui** : Test (Ollama, Qwen 2.5 7B)  
**Input** : code complet + fiche test + zones d'impact (architect-brief)  
**Output** : tests d'intégration + tests de non-régression  

L'agent Test écrit et exécute :
- Tests d'intégration (interactions entre Back et Front)
- Tests de non-régression sur les zones identifiées par l'Architecte en phase 2

Si un test de non-régression échoue, l'agent Test annote sa fiche avec le détail
de l'échec et remonte via le PM.

---

## Phase 8 - Audit sécurité

**Qui** : Sécu (Ollama, Qwen 2.5 7B)  
**Input** : code complet + fiche sécu  
**Output** : rapport d'audit dans `specs/run-NNN/security-report.md`  

L'agent Sécu audite le code produit selon le skill `security.md` :
- Injections (SQL, commandes, templates)
- Gestion des secrets et variables d'environnement
- Validation des inputs
- Gestion des erreurs (pas de stack traces exposées)
- Dépendances et versions

---

## Phase 9 - Audit aval

**Qui** : Architecte (Ollama, Qwen 2.5 7B)  
**Input** : code complet + tous les rapports  
**Output** : documentation complète dans le repo projet  
**Checkpoint** : validation humaine obligatoire (au début, automatique ensuite)  

L'Architecte produit :
- Vérification conformité non-fonctionnelle sur le code final
- Détection de factorisation (doublons créés pendant l'implémentation)
- ADR du run (décisions prises, alternatives rejetées)
- Mise à jour de l'OpenAPI si endpoints modifiés
- Mise à jour du README si comportement visible modifié
- CHANGELOG entrée pour ce run
- Runbook si comportement opérationnel modifié

---

## Phase 10 - Clôture

**Qui** : Python pur (0 token)  
**Input** : état du run, tous les artefacts produits  
**Output** : commits Git + notification Telegram  

```python
# Pseudo-code de la phase 10
for agent in run.agents_involved:
    git.commit(
        author=agent.git_identity,
        message=f"{agent.conventional_commit_prefix}: {agent.summary}",
        files=agent.files_modified
    )

pm.update_project_map(run)
pm.update_run_history(run)
telegram.notify(f"Run {run.id} terminé. {run.summary}")
metrics.finalize(run)
```

La phase 10 ne fait jamais appel à un LLM. Elle est déterministe et rapide.

---

## Boucle de feedback erreur

Si l'agent N+1 détecte une erreur produite par l'agent N :

1. N+1 annote la section `feedback` de la fiche de N avec le détail de l'erreur.
2. Le runtime LangGraph relance l'agent N avec sa fiche annotée + ses artefacts en input.
3. L'agent N corrige et repropose.
4. N+1 reprend sa tâche avec les artefacts corrigés.

Si N échoue après 3 itérations, la fiche est marquée `status: failed` et une
notification Telegram est envoyée. Reprise manuelle avec Cursor ou Claude Code.

---

## Validation humaine progressive

**Phase de démarrage** : checkpoints obligatoires en phases 1, 2, 3, 5, 9.  
**Phase de croisière** : au fur et à mesure que le système est maîtrisé, les checkpoints
peuvent être désactivés un par un via `config/studio.yml`.

```yaml
# config/studio.yml
checkpoints:
  phase_1: true   # cadrage - toujours valider au début
  phase_2: true   # audit amont
  phase_3: true   # fiches dépendantes
  phase_5: true   # audit stubs
  phase_9: false  # audit aval - peut passer en auto
```
