# Skill - Documentation (Architecte)

## Artefacts à produire en phase 9

### 1. ADR du run (obligatoire)

Fichier : `docs/adr/NNNN-<titre-kebab-case>.md`

```markdown
# ADR NNNN - Titre de la décision

**Date** : YYYY-MM-DD  
**Run** : run-NNN  
**Statut** : Accepté

## Contexte
Pourquoi cette décision a été nécessaire.

## Décision
Ce qui a été décidé.

## Raisons
Pourquoi cette décision plutôt qu'une autre.

## Conséquences
Ce que cette décision implique (positif et négatif).

## Alternatives rejetées
Ce qui a été considéré et écarté.
```

### 2. OpenAPI (si endpoints modifiés)

Mise à jour de `docs/api/openapi.yaml` avec les nouveaux endpoints ou les modifications.
Format OpenAPI 3.1.

### 3. CHANGELOG (obligatoire)

Ajoute une entrée en haut de `CHANGELOG.md` :

```markdown
## [Unreleased] - run-NNN - YYYY-MM-DD

### Ajouté
- Description de ce qui a été ajouté

### Modifié
- Description de ce qui a changé

### Corrigé
- Description des bugs corrigés
```

### 4. README (si comportement visible modifié)

Mise à jour des sections concernées uniquement. Pas de réécriture complète.

### 5. Runbook (si comportement opérationnel modifié)

Fichier : `docs/runbooks/<nom-fonctionnalite>.md`

```markdown
# Runbook - <Nom de la fonctionnalité>

## Description
Ce que fait cette fonctionnalité en production.

## Configuration requise
Variables d'environnement, dépendances externes.

## Démarrage
Comment démarrer/activer.

## Monitoring
Métriques à surveiller, seuils d'alerte.

## Troubleshooting
Erreurs fréquentes et comment les résoudre.
```
