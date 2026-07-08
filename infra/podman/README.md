# infra/podman

Compose files Podman pour l'environnement de développement de devaimazing
(`devaimazing-prod-network`, voir `docs/infra-topology.md`).

## Statut

Stub — dossier créé pour que la structure annoncée dans le README corresponde au
filesystem. Les compose files ne sont pas encore écrits.

## Ce qui est attendu ici (à terme)

- Compose file Prometheus dev (métriques du daemon LangGraph, voir `docs/metrics.md`
  et `config/studio.yml` → `metrics.prometheus_port`).
- Éventuellement Grafana/Loki si mutualisés localement en dev (voir
  `docs/infra-topology.md`, section « Services mutualisés »).

Ne pas y mettre de PostgreSQL permanent : `docs/infra-topology.md` précise
explicitement qu'il n'y a pas de PostgreSQL partagé dans `devaimazing-prod-network` —
chaque projet cible embarque son propre PostgreSQL de test.
