# Stratégie LLM

Voir ADR 0006 pour le raisonnement complet.

## Résumé opérationnel

| Agent | Phase | Modèle | Justification |
|---|---|---|---|
| PM | 1 - Cadrage | Claude Opus 4.x (API) | Raisonnement architectural, 1 seul appel par run |
| PM | 3 - Fiches | Claude Sonnet 4.6 (API) | Mode croisière, structuré |
| PM | 10 - Clôture | Python pur | 0 token |
| Architecte | 2, 5, 9 | Claude Sonnet 4.6 (API) | Auditeur doit dominer le producteur (Qwen) |
| Back | 4, 6 | Qwen 2.5 7B (Ollama) | Local, guidé par stubs validés |
| Front | 4, 6 | Qwen 2.5 7B (Ollama) | Local, guidé par stubs validés |
| Test | 7 | Qwen 2.5 7B (Ollama) | Local |
| Sécu | 8 | Sonnet + SAST (Semgrep, Bandit) | Auditeur doit dominer le producteur + détection déterministe |
| Transitions | inter-phases | Python pur | 0 token |

## Configuration

Les modèles sont déclarés dans `config/studio.yml` :

```yaml
models:
  pm_opus: claude-opus-4-8
  pm_sonnet: claude-sonnet-4-6
  agent_auditor: claude-sonnet-4-6
  agents_local: qwen2.5:7b-instruct
```

Changer de modèle ne nécessite pas de modifier le code.

## Principe : l'auditeur doit dominer le producteur

Un modèle ne peut pas auditer la dette qu'il a lui-même produite. S'il pouvait
la voir, il ne l'aurait pas produite. La dette résiduelle d'un modèle est exactement
l'ensemble de ses angles morts. Détecter cette dette exige une capacité strictement
supérieure au producteur.

Dans devaimazing, les agents producteurs sont Qwen 2.5 7B. L'auditeur doit donc
dominer Qwen, pas nécessairement Opus. Sonnet suffit et respecte l'objectif de
minimisation des tokens API.

## Phase 8 - Sécu : deux couches complémentaires

1. **SAST déterministe** (Semgrep, Bandit) : premier passage, zéro token, attrape
   le volume connu (injections, secrets, patterns de vulnérabilité classiques).
2. **Agent Sécu (Sonnet)** : second passage sur ce que le SAST ne couvre pas
   (failles logiques métier, subtilités d'autorisation, cohérence globale).

## Benchmarking Ollama

Une fois le pipeline LangGraph stable, benchmarker sur une même fiche exemple :
- Qwen 2.5 7B Instruct / Qwen 2.5 Coder 7B Instruct (baseline actuelle, orienté code)
- Codestral / Codestral Mamba (Mistral, orienté code)
- DeepSeek Coder V2 Lite 16B (tendu en RAM mais existe en quant serrée)
- Gemma 2 9B (généraliste, moins orienté code)
- Llama 3.1 8B Instruct (généraliste)
- StarCoder2 7B/15B (orienté code)

**Précision** : un LLM orienté code n'a pas une performance uniforme entre langages —
elle dépend des proportions de langages dans son corpus d'entraînement (ex. StarCoder2
couvre large via The Stack, DeepSeek Coder est réputé fort en Python). Le benchmark
doit donc être mené sur le ou les langages réellement utilisés par les projets cibles
pilotés par devaimazing, pas sur un score générique multi-langages.

Critères : qualité stubs, qualité implémentation, qualité audit, latence, RAM peak.

## Escalade Opus

Opus est invoqué hors phase 1 uniquement si :
- Un agent échoue 3 fois sur la même fiche
- L'utilisateur demande explicitement une révision architecturale
- Une contradiction majeure est détectée entre fiches

La décision d'escalader est prise par l'utilisateur (notification ntfy), pas automatiquement.
