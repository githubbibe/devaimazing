# Métriques devaimazing

## Niveaux d'agrégation

### Par tâche (atomique)

Chaque activation d'un agent sur une fiche produit une ligne dans `metrics.db`
(table `tasks`, voir `runtime/studio/metrics.py`).

| Champ | Type | Description |
|---|---|---|
| `task_id` | string | `<run_id>-<agent>-<phase>-<iteration>`, idempotent (upsert si rejoué) |
| `run_id` | string | Run parent |
| `card_id` | string | Fiche associée |
| `agent` | string | Nom de l'agent |
| `phase` | int | Phase du workflow (1-10) |
| `model` | string | Modèle LLM utilisé |
| `tokens_prompt` | int | Tokens d'input |
| `tokens_completion` | int | Tokens d'output |
| `llm_duration_ms` | int | Temps de calcul LLM pur |
| `total_duration_ms` | int | Temps total (LLM + outils + I/O) |
| `claude_code_calls` | int | Nombre d'appels Claude Code CLI (0 pour les agents Ollama) |
| `status` | enum | `success`, `error`, `feedback_sent` |
| `iteration` | int | Numéro d'itération (1 = première, 2+ = après renvoi) |
| `created_at` | datetime | Timestamp (UTC) |

Il n'y a pas de colonne `tokens_total` stockée : c'est `tokens_prompt + tokens_completion`,
recalculable à la lecture.

### Par run

Agrégation servie par `MetricsCollector.get_run_summary(run_id)` — somme les tâches
du run, ventilées par agent et par phase.

| Champ | Description |
|---|---|
| `task_count` | Nombre de tâches (toutes itérations confondues) |
| `tokens_prompt` | Somme tokens d'input, toutes tâches |
| `tokens_completion` | Somme tokens d'output, toutes tâches |
| `total_duration_ms` | Somme des durées totales |
| `by_agent` | Mêmes 4 champs, ventilés par agent |
| `by_phase` | Mêmes 4 champs, ventilés par phase |

Pas d'agrégation "par fiche" séparée ni de champ `api_cost_equivalent_eur` — ni l'un
ni l'autre n'existent dans le code actuel (aspirationnel, à construire si besoin,
calculable à partir des lignes `tasks` groupées par `card_id`).

---

## Instrumentation Prometheus (in-memory, non exposée)

`runtime/studio/metrics.py` déclare des objets `prometheus_client` mis à jour en
mémoire process à chaque tâche/collecte :

| Métrique | Mise à jour par | Description |
|---|---|---|
| `devaimazing_tokens_total` (Counter) | `record_task` | Tokens consommés, labels `agent`/`model`/`type` (prompt\|completion) |
| `devaimazing_task_duration_seconds` (Histogram) | `record_task` | Durée des tâches, labels `agent`/`phase` |
| `devaimazing_agent_iterations_total` (Counter) | `record_task` | Itérations par agent, label `status` |
| `devaimazing_ram_usage_gb` (Gauge) | `record_system_metrics` | RSS du process courant (voir ci-dessous) |
| `devaimazing_ollama_latency_seconds` (Histogram) | — | Déclarée mais **jamais mise à jour** : aucun appelant dans `tools/ollama.py` actuellement |

**Aucun serveur HTTP Prometheus n'est démarré** (pas d'appel `start_http_server` ni
équivalent dans le runtime) : ces objets vivent en mémoire pour la durée du process
mais ne sont pas scrapables. `config/studio.yml` a une clé `metrics.prometheus_port`
(9091) qui n'est lue par aucun code — à câbler explicitement si l'export Prometheus
devient un besoin réel.

`record_system_metrics` (RAM uniquement, voir ci-dessous) n'est par ailleurs appelée
nulle part dans le pipeline — seulement exercée par `runtime/tests/test_metrics.py`.
Il n'y a donc pas de collecte "en continu pendant un run" aujourd'hui, malgré la
docstring qui la décrit comme "appelée périodiquement".

### Métrique système

| Métrique | Source | Réalité |
|---|---|---|
| RAM (RSS process) | stdlib `resource` (`RUSAGE_SELF.ru_maxrss`) | RAM du process devaimazing courant, pas la RAM système globale de la machine |

Pas de métrique CPU ni GPU : `psutil` n'est pas une dépendance du projet, et rien
n'appelle un outil externe type `apple_gpu_top`. À ajouter explicitement si un
monitoring système complet devient nécessaire (voir `docs/roadmap.md`).

---

## Stockage

Deux fichiers SQLite séparés (`runtime/studio/config.py` — `metrics_db_path`,
`state_db_path`) :

```
~/.devaimazing/
├── state.db      # checkpointer LangGraph (état PM)
└── metrics.db    # métriques (table tasks)
```

Séparés pour que les métriques n'interfèrent jamais avec le state LangGraph.

---

## Observabilité — état réel vs cible

Consultable aujourd'hui via `devaimazing metrics <run-id>` (CLI, lit directement
`metrics.db` via `get_run_summary`). Pas de dashboard Grafana ni d'export Prometheus
scrapable actuellement — voir `docs/infra-topology.md` pour ce qui relève d'une
cible d'infrastructure future (Podman, Loki, Grafana Alloy) et non de l'état livré.
