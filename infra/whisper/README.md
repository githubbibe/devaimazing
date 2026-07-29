# infra/whisper

Transcription vocale (Whisper local) pour l'agent Devaimazing — voir
`docs/adr/0014-whisper-transcription-vocale.md`.

## Choix technique (vérifié empiriquement le 2026-07-29)

**Ollama ne supporte pas Whisper** (`ollama pull whisper` échoue : « pull model
manifest: file does not exist »). Comme prévu par l'ADR 0014, le fallback
retenu est `whisper.cpp` en sous-processus — concrètement, l'image officielle
`ghcr.io/ggerganov/whisper.cpp` (mainteneur du projet), qui embarque
`whisper-server` (serveur HTTP, `/inference`) et `ffmpeg` (conversion audio
côté serveur via `--convert`, donc l'OGG/Opus des messages vocaux Telegram
n'a pas besoin d'être reconverti côté client — vérifié avec un fichier OGG
mono 16kHz, transcription correcte).

Mêmes principes que `infra/ollama/` : conteneurisé (Podman), portable
d'une machine à l'autre, modèles sur une partition à gros quota.

## Compose local (`compose.yml`)

```
cp infra/whisper/.env.example infra/whisper/.env
# éditer WHISPER_DATA_DIR dans .env (partition à gros quota, ex. /goinfre/<user>/Whisper)

mkdir -p <WHISPER_DATA_DIR>
podman run --rm -v <WHISPER_DATA_DIR>:/models \
  --entrypoint bash ghcr.io/ggerganov/whisper.cpp:main \
  -c "cd /app/models && ./download-ggml-model.sh small /models"

podman-compose -f infra/whisper/compose.yml up -d
```

Le serveur écoute sur `localhost:8090` (port hôte — `8080` en interne au
conteneur, mappé différemment pour éviter les collisions avec d'autres
services de dev). Test rapide :

```
curl http://localhost:8090/inference -F file=@<fichier.ogg> -F response_format="json"
```

`.env` est ignoré par git (déjà couvert par le `.gitignore` racine) — chaque
machine a le sien. `WHISPER_MODEL` (dans `.env`) doit correspondre à un
fichier `.bin` présent dans `WHISPER_DATA_DIR` (`ggml-small.bin` par défaut,
voir ADR 0014 pour le choix de taille de modèle).

**Machines école 42 (`/goinfre`)** : mêmes remarques que `infra/ollama/README.md`
— la partition peut être vidée entre deux sessions, il faut re-télécharger le
modèle (~465 Mo pour `small`, ~40s en pratique) si le conteneur redémarre à
vide.

## Statut

Infra vérifiée et fonctionnelle (2026-07-29) : serveur démarré, transcription
testée sur un fichier OGG mono 16kHz (format proche des messages vocaux
Telegram), langue forcée à `fr` (voir `compose.yml`, cohérent avec un usage
mono-utilisateur francophone — voir ADR 0014 si un usage multilingue devient
nécessaire). Reste à faire : `runtime/studio/tools/whisper.py` (wrapper HTTP
côté devaimazing) et le câblage détection voice/audio dans le bot Telegram
(voir `docs/roadmap.md`).
