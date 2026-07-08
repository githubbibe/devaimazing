# Skill - Confidentialité des données (pseudonymisation by design)

## Objectif

Ce skill s'applique dès qu'un projet produit par devaimazing collecte des données
comportementales utilisateur (tracking de visites, parcours, événements, logs
applicatifs contenant du contexte utilisateur). Il encode le principe de
pseudonymisation by design établi dans l'ADR 0009.

## Principe

Aucune donnée exportée vers un système d'observabilité externe (Loki ou équivalent)
ne doit contenir d'identifiant direct (nom, email, ID utilisateur clair). Un
identifiant pseudonyme fait office de clé de corrélation dans les flux exportés.

## Pattern d'implémentation attendu

### 1. Séparation des tables

```
table users (identité réelle)
  - id (clé primaire interne, JAMAIS exportée)
  - email, nom, etc.

table user_pseudonyms
  - pseudonym_id (UUID, généré, à sens unique dans l'usage courant)
  - user_id (FK vers users, cette table est LA seule jointure possible)
  - created_at

table behavioral_events (exportable vers Loki)
  - pseudonym_id (jamais user_id direct)
  - event_type, timestamp, contexte métier
```

La table `user_pseudonyms` est la seule jointure entre identité et pseudonyme.
Elle doit être :
- Non accessible depuis le réseau où tourne Loki/Grafana/Alloy
- Accessible uniquement via des credentials admin distincts des credentials
  applicatifs courants

### 2. Génération du pseudonyme

Le pseudonyme est un UUID généré à la création de l'utilisateur (ou à la première
collecte d'événement), stocké une seule fois. Ne jamais dériver le pseudonyme par
un hash déterministe de l'identifiant réel sans sel (un hash non salé est
recalculable et donc pas réellement protecteur).

### 3. Configuration `analytics_mode`

Le module de collecte comportementale expose une configuration :

```yaml
analytics:
  mode: local  # local | shared
  loki_endpoint: null  # requis si mode = shared
```

- `local` : les événements restent dans `behavioral_events` en base locale,
  aucun export.
- `shared` : les événements sont poussés vers Loki (endpoint configuré),
  toujours via `pseudonym_id`, jamais `user_id`.

Implémenter cette bascule via une interface d'abstraction (ex: classe
`EventSink` avec deux implémentations `LocalEventSink` et `LokiEventSink`),
pas via des conditions dispersées dans le code métier.

### 4. Traçabilité de la ré-identification

Toute fonction qui résout `pseudonym_id → user_id` doit :
- Nécessiter des credentials admin explicites
- Logger l'opération elle-même dans un audit log dédié : qui a demandé, quand,
  pour quelle raison déclarée
- Ne jamais être appelée depuis un contexte applicatif courant (uniquement
  depuis un outil d'administration dédié, avec authentification forte)

## Ce que ce skill NE couvre PAS

- Les décisions de base légale RGPD précises pour un projet donné (à documenter
  dans la politique de confidentialité du projet livré, hors périmètre de ce skill)
- Le chiffrement au repos (recommandé, mais dépend de l'infrastructure du projet)
- Les données strictement nécessaires au fonctionnement (authentification,
  facturation) qui suivent leurs propres règles, distinctes du tracking comportemental

## Checklist pour l'Architecte

Quand une fiche de run touche à une fonctionnalité de tracking ou de collecte
d'événements utilisateur, vérifier :

- [ ] Une table de pseudonymes séparée est prévue dans les stubs
- [ ] Aucun `user_id` direct n'apparaît dans les événements exportables
- [ ] La configuration `analytics_mode` est exposée et documentée
- [ ] Une fonction de ré-identification, si elle existe, est tracée dans un audit log
- [ ] Le README du projet mentionne ce choix de gouvernance pour le cloneur
