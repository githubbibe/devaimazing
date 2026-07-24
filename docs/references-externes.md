# Références externes

Sources externes jugées fiables, conservées en mémoire du projet pour une
implémentation future si un besoin correspondant se présente (ex : évolution du
parc machine autorisant plus de RAM/GPU). Les notebooks eux-mêmes sont copiés dans
`docs/references-externes/` (licence Apache 2.0 d'origine, Google LLC, conservée en
en-tête de chaque fichier), en plus des liens vers la source publique ci-dessous.

## Notebooks Google Cloud `generative-ai` (API Gemini)

Trois notebooks de la collection publique
[`GoogleCloudPlatform/generative-ai`](https://github.com/GoogleCloudPlatform/generative-ai),
tous construits sur l'API Gemini payante (pas d'équivalent direct en local
aujourd'hui) :

- **[Building Knowledge Graphs with Gemini](docs/references-externes/knowledge_graph_generation.ipynb)**
  ([source GitHub](https://github.com/GoogleCloudPlatform/generative-ai/blob/main/gemini/use-cases/knowledge-graph/knowledge_graph_generation.ipynb),
  [ouvrir dans Colab](https://colab.research.google.com/github/GoogleCloudPlatform/generative-ai/blob/main/gemini/use-cases/knowledge-graph/knowledge_graph_generation.ipynb))
  — extraction d'entités/relations depuis du texte non structuré, sortie
  structurée JSON/TSV, passage à l'échelle (livres, contrats), visualisation en
  graphe réseau.
- **[Generating Consistent Imagery with Gemini](docs/references-externes/consistent_imagery_generation.ipynb)**
  ([source GitHub](https://github.com/GoogleCloudPlatform/generative-ai/blob/main/gemini/use-cases/media-generation/consistent_imagery_generation.ipynb),
  [ouvrir dans Colab](https://colab.research.google.com/github/GoogleCloudPlatform/generative-ai/blob/main/gemini/use-cases/media-generation/consistent_imagery_generation.ipynb))
  — génération d'images cohérentes (personnages/scènes récurrents), feuille de
  personnage, graphe de dépendances entre assets générés.
- **[Unlocking Multimodal Video Transcription with Gemini](docs/references-externes/multimodal_video_transcription.ipynb)**
  ([source GitHub](https://github.com/GoogleCloudPlatform/generative-ai/blob/main/gemini/use-cases/video-analysis/multimodal_video_transcription.ipynb),
  [ouvrir dans Colab](https://colab.research.google.com/github/GoogleCloudPlatform/generative-ai/blob/main/gemini/use-cases/video-analysis/multimodal_video_transcription.ipynb))
  — transcription vidéo multimodale (qui a dit quoi et quand, identification des
  locuteurs) directement depuis la vidéo, sans pipeline ASR + diarisation classique.

## Faisabilité locale (état à la date de cette note, 2026-07)

| Besoin | Faisable en local aujourd'hui | Modèles candidats |
|---|---|---|
| Knowledge graph (texte → entités/relations) | Oui, sur documents courts/moyens | Qwen 2.5 7B, Gemma 2 9B — dégrade sur documents longs/complexes (livres entiers, contrats denses) où Gemini garde l'avantage (grand contexte, raisonnement) |
| Compréhension d'image | Oui, tient dans la contrainte RAM actuelle (6-10 Go, voir ADR 0006) | Qwen2.5-VL 7B, Llama 3.2 Vision 11B, Moondream 2 (léger), Gemma 3 (variante multimodale) |
| Génération d'image | Non réaliste sur la config actuelle | Stable Diffusion (SDXL/SD 1.5, via ComfyUI ou Automatic1111) — stack indépendante d'Ollama, généralement GPU dédié |
| Compréhension vidéo native | Non — pas d'équivalent local mature identifié à ce jour | — |

Cette table est à revérifier à l'implémentation : l'état de l'art évolue vite, et
la contrainte RAM (ADR 0006) est spécifique au Mac mini M4 Pro actuel — un
changement de parc machine change ce qui devient faisable.
