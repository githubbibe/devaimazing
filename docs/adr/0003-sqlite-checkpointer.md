# ADR 0003 - SQLite comme checkpointer LangGraph

**Date** : 2026-06  
**Statut** : Accepté

## Contexte

LangGraph supporte plusieurs backends de persistance : SQLite, PostgreSQL, Redis.
Le choix impacte la complexité de setup et les besoins en infrastructure.

## Décision

SQLite via `aiosqlite` comme checkpointer LangGraph pour l'état du PM.
Un second SQLite dédié `metrics.db` pour les métriques (tokens, temps, statuts).

## Raisons

1. **Seul le PM persiste** : les autres agents sont stateless. Le volume de données
   persistées est minimal (état PM + historique projet). SQLite est largement suffisant.

2. **Zéro infrastructure** : pas de service à démarrer, pas de container dédié.
   Le fichier SQLite vit dans le repo ou dans `~/.devaimazing/`.

3. **Portabilité** : le state peut être copié, sauvegardé, inspecté avec n'importe
   quel outil SQLite. Pas de dépendance à un service externe.

4. **Cohérence** : deux fichiers SQLite (state + metrics), pas de technologie mixte.

## Conséquences

- Pas de scalabilité multi-utilisateurs (pas l'objectif en phase 1).
- Migration vers PostgreSQL possible plus tard si nécessaire, LangGraph supporte les deux.
- Les métriques sont interrogeables via SQL standard pour les dashboards Grafana.

## Alternatives rejetées

- **PostgreSQL** : overkill pour un usage mono-user séquentiel. Ajoute un container.
- **Redis** : adapté pour du temps réel, pas pour de la persistance de state LangGraph.
