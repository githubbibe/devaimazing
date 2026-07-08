# Stratégie LLM

Voir ADR 0006 pour le raisonnement complet.

## Résumé opérationnel

| Agent | Phase | Modèle | Justification |
|---|---|---|---|
| PM | 1 - Cadrage | Claude Opus 4.x (API) | Raisonnement architectural, 1 seul appel par run |
| PM | 3 - Fiches | Claude Sonnet 4.6 (API) | Mode croisière, structuré |
| PM | 10 - Clôture | Python pur | 0 token |
| Architecte | 2, 5, 9 | Claude Sonnet 4.6 (API) | Auditeur, doit dominer le producteur |
| Back | 4, 6 | Qwen 2.5 7B (Ollama) | Local, guidé par stubs validés |
| Front | 4, 6 | Qwen 2.5 7B (Ollama) | Local, guidé par stubs validés |
| Test | 7 | Qwen 2.5 7B (Ollama) | Local |
| Sécu | 8 | SAST (0 token) puis Claude Sonnet 4.6 (API) | Auditeur, doit dominer le producteur |
| Transitions | inter-phases | Python pur | 0 token |

## Configuration

Les modèles sont déclarés dans `config/studio.yml` :

```yaml
models:
  pm_opus: claude-opus-4-8
  pm_sonnet: claude-sonnet-4-6
  agent_auditor: claude-sonnet-4-6 # Architecte + Sécu
  agents_local: qwen2.5:7b-instruct # Back, Front, Test
```

Changer de modèle ne nécessite pas de modifier le code.

## Benchmarking Ollama

Une fois le pipeline LangGraph stable, benchmarker sur une même fiche example :
- Qwen 2.5 7B Instruct (baseline)
- Qwen 2.5 Coder 7B Instruct (meilleur en code ?)
- Qwen 2.5 14B Instruct (si RAM disponible)

Critères : qualité stubs, qualité implémentation, qualité audit, latence, RAM peak.

## Escalade Opus

Opus est invoqué hors phase 1 uniquement si :
- Un agent Ollama échoue 3 fois sur la même fiche
- L'utilisateur demande explicitement une révision architecturale
- Une contradiction majeure est détectée entre fiches

La décision d'escalader est prise par l'utilisateur (notification Telegram), pas automatiquement.
