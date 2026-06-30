# Métriques devaimazing

## Niveaux d'agrégation

### Par tâche (atomique)

Chaque activation d'un agent sur une fiche produit une ligne dans `metrics.db`.

| Champ | Type | Description |
|---|---|---|
| `task_id` | UUID | Identifiant unique de la tâche |
| `run_id` | UUID | Run parent |
| `card_id` | string | Fiche associée |
| `agent` | string | Nom de l'agent |
| `phase` | int | Phase du workflow (1-10) |
| `model` | string | Modèle LLM utilisé |
| `tokens_prompt` | int | Tokens d'input |
| `tokens_completion` | int | Tokens d'output |
| `tokens_total` | int | Total tokens |
| `llm_duration_ms` | int | Temps de calcul LLM pur |
| `total_duration_ms` | int | Temps total (LLM + outils + I/O) |
| `claude_code_calls` | int | Nombre d'appels Claude Code CLI |
| `status` | enum | `success`, `error`, `feedback_sent`, `feedback_received` |
| `iteration` | int | Numéro d'itération (1 = première, 2+ = après renvoi) |
| `created_at` | datetime | Timestamp |

### Par fiche

Agrégation sur toutes les tâches liées à une fiche.

| Champ | Description |
|---|---|
| `tokens_total` | Somme tokens toutes itérations |
| `duration_total_ms` | Somme temps toutes itérations |
| `iterations_count` | Nombre de renvois (1 = succès du premier coup) |
| `agents_involved` | Liste des agents ayant traité cette fiche |
| `api_cost_equivalent_eur` | Estimation coût si tout avait été en API |

### Par run

Agrégation de toutes les fiches du run.

| Champ | Description |
|---|---|
| `tokens_opus` | Total tokens Opus (coût € à surveiller) |
| `tokens_sonnet` | Total tokens Sonnet (coût €) |
| `tokens_ollama` | Total tokens Ollama local |
| `tokens_fallback` | Total tokens fallback manuel (Cursor/Claude Code) |
| `tokens_by_agent` | Répartition par agent |
| `tokens_by_phase` | Répartition par phase |
| `human_checkpoints` | Nombre de checkpoints humains déclenchés |
| `wall_clock_ms` | Durée totale depuis ouverture jusqu'au commit |
| `cards_count` | Nombre de fiches traitées |
| `cards_failed` | Nombre de fiches en échec (fallback manuel) |

---

## Métriques système

Collectées en continu pendant un run par `studio/metrics.py`.

| Métrique | Source | Description |
|---|---|---|
| `ram_used_gb` | `psutil` | RAM utilisée sur le Mac mini |
| `ram_available_gb` | `psutil` | RAM disponible |
| `cpu_percent` | `psutil` | CPU global |
| `gpu_percent` | `subprocess apple_gpu_top` | GPU M4 Pro |
| `ollama_latency_ms` | ping Ollama API | Latence de réponse Ollama |
| `ollama_model_loaded` | Ollama API | Modèle actuellement chargé |
| `langgraph_errors` | runtime | Erreurs runtime LangGraph |
| `queue_depth` | runtime | Fiches en attente (toujours 0 ou 1 en mono-run) |

---

## Stockage

Deux fichiers SQLite séparés :

```
~/.devaimazing/
├── state.db      # checkpointer LangGraph (état PM)
└── metrics.db    # métriques (tâches, fiches, runs, système)
```

Séparés pour que les métriques n'interfèrent jamais avec le state LangGraph.

---

## Observabilité

Les métriques sont exportées vers **Prometheus dev** (container Podman dédié)
et visualisées dans le **Grafana existant** (nouveau datasource `devaimazing-dev`).

```
metrics.db
    |
studio/metrics.py (prometheus_client)
    |
Prometheus dev (Podman, port 9091)
    |
Grafana (datasource dev, dashboards dédiés devaimazing)
```

Dashboards Grafana à créer :
- Vue run en cours (tokens temps réel, phase courante, agent actif)
- Historique des runs (évolution qualité/coût)
- Comparaison modèles Ollama (après benchmarking)
- Métriques système pendant les runs
