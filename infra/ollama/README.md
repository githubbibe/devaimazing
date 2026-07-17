# infra/ollama

Configuration des modèles Ollama à pull pour les agents locaux (Back, Front, Test),
voir `docs/llm-strategy.md`.

## Deux cas distincts

**Service Ollama de prod (Mac mini, mutualisé multi-network)** — voir
`docs/infra-topology.md`. Son déploiement n'est **pas** documenté ici : ce
dossier ne doit décrire que ce que devaimazing attend d'Ollama (modèles,
config), pas le déploiement du service partagé lui-même.

**Instance Ollama de dev/test local (machine ad hoc, ex. laptop école 42)** —
c'est le cas couvert par `compose.yml` ci-dessous. Utile quand on travaille sur
une machine sans accès au service mutualisé de prod (pas de VPN/réseau vers le
Mac mini), typiquement pour lancer `run-agent` en local. Une seule instance à
la fois, pas de haute dispo, pas de partage entre machines.

## Compose local (`compose.yml`)

```
cp infra/ollama/.env.example infra/ollama/.env
# éditer OLLAMA_DATA_DIR dans .env (partition à gros quota, ex. /goinfre/<user>/Ollama —
# jamais le home s'il est soumis à quota, voir vérif `df -h ~` avant de choisir le chemin)

podman-compose -f infra/ollama/compose.yml up -d
# puis pull du modèle configuré dans config/studio.yml → models.agents_local :
podman exec ollama ollama pull qwen2.5:14b-instruct
```

`.env` est ignoré par git (déjà couvert par le `.gitignore` racine) — chaque
machine a le sien.

## Statut

`compose.yml` couvre le cas dev/test local. Reste à faire (pas encore écrit) :

## Ce qui est attendu ici (à terme)

- Liste des modèles à pull, alignée sur `config/studio.yml` → `models.agents_local`
  (`qwen2.5:7b-instruct` actuellement).
- Éventuellement un script de pull/vérification des modèles avant démarrage du daemon.
- Notes de benchmark (Qwen 2.5 7B vs Qwen 2.5 Coder 7B vs Qwen 2.5 14B) une fois
  disponibles — voir `docs/llm-strategy.md`, section « Benchmarking Ollama ».
