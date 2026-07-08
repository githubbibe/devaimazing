# infra/ollama

Configuration des modèles Ollama à pull pour les agents locaux (Back, Front, Test),
voir `docs/llm-strategy.md`.

## Statut

Stub — dossier créé pour que la structure annoncée dans le README corresponde au
filesystem. La configuration n'est pas encore écrite.

## Ce qui est attendu ici (à terme)

- Liste des modèles à pull, alignée sur `config/studio.yml` → `models.agents_local`
  (`qwen2.5:7b-instruct` actuellement).
- Éventuellement un script de pull/vérification des modèles avant démarrage du daemon.
- Notes de benchmark (Qwen 2.5 7B vs Qwen 2.5 Coder 7B vs Qwen 2.5 14B) une fois
  disponibles — voir `docs/llm-strategy.md`, section « Benchmarking Ollama ».

Ollama est un service mutualisé multi-network (voir `docs/infra-topology.md`), pas
propre à devaimazing seul : cette config ne doit documenter que ce que devaimazing
attend d'Ollama, pas le déploiement du service lui-même.
