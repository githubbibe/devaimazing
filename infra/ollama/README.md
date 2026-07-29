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

**Machines école 42 (`/goinfre`) : les modèles ne survivent pas forcément
d'une session à l'autre.** Constaté le 2026-07-29 : `/goinfre/<user>/` était
entièrement vide (conteneur, cache, modèles) au début d'une nouvelle session,
alors que ~9 Go de modèles y avaient été pull la session précédente — pas un
problème de disque (le disque avait toujours son quota, 376G disponibles),
plutôt un comportement probable des partitions goinfre 42 (scratch, purgée
entre sessions/redémarrages). Si `podman exec ollama ollama pull <modèle>`
échoue avec une erreur de connexion, ou si `curl localhost:11434/api/tags`
renvoie une liste vide de modèles alors qu'ils avaient été pull avant : ne pas
chercher un bug, relancer simplement `podman-compose -f infra/ollama/compose.yml
up -d` puis re-pull les modèles nécessaires.

## Statut

`compose.yml` couvre le cas dev/test local. Reste à faire (pas encore écrit) :

## Ce qui est attendu ici (à terme)

- Liste des modèles à pull, alignée sur `config/studio.yml` → `models.agents_local`
  (`qwen2.5:7b-instruct` actuellement).
- Éventuellement un script de pull/vérification des modèles avant démarrage du daemon.
- Notes de benchmark (Qwen 2.5 7B vs Qwen 2.5 Coder 7B vs Qwen 2.5 14B) une fois
  disponibles — voir `docs/llm-strategy.md`, section « Benchmarking Ollama ».
