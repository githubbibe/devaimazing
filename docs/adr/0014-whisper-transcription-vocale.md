# ADR 0014 - Transcription vocale (Whisper) en amont de l'agent Devaimazing

**Date** : 2026-07
**Statut** : Accepté (décision de conception — implémentation non commencée, voir
« Conséquences »). Complète l'ADR 0013 (interface Telegram, agent Devaimazing) d'une
capacité qui n'y figurait pas : le support des messages vocaux.

## Contexte

L'ADR 0013 documente une interface Telegram (groupe unique à topics) et un rôle
transverse Devaimazing pour le pilotage du studio, y compris à distance (AFK/mobile).
Cette réflexion complémentaire ajoute la capacité de piloter le studio par message
vocal Telegram, plutôt que texte tapé uniquement — pertinent pour l'usage mobile visé
par l'ADR 0013 (dicter une instruction courte est souvent plus rapide que la taper).

## Décision — Whisper comme prétraitement ASR pur, en amont de Devaimazing

**Principe** : Whisper n'est pas un LLM et ne remplace rien dans l'architecture
existante. C'est un modèle de transcription audio-vers-texte (ASR) pur : il ne
comprend pas les intentions, ne raisonne pas, ne décide de rien. Il sert uniquement
de prétraitement en amont de l'agent Devaimazing (Qwen local, ADR 0013), qui reste
seul responsable de la compréhension et de la décision, exactement comme pour un
message texte natif.

```
Message texte tapé  → Devaimazing (Qwen, via Ollama) → action/réponse

Message vocal        → Whisper (transcription pure)
                      → texte → Devaimazing (Qwen, via Ollama) → action/réponse
```

**Devaimazing ne voit aucune différence entre un texte tapé directement et un texte
transcrit depuis un vocal** — le traitement en aval de la transcription est
strictement identique dans les deux cas. Aucune branche de logique n'est introduite
dans Devaimazing lui-même pour distinguer les deux origines (voir mise à jour de
`prompts/devaimazing.md`, section « Origine des messages »).

**Ce que le bot Telegram doit faire** (couche Telegram, en amont de Devaimazing, pas
une responsabilité de l'agent) :
1. Détecter le type de message entrant (`voice`/`audio` vs `text`) via l'API
   Telegram Bot.
2. Si message vocal : télécharger le fichier audio, le transmettre à Whisper,
   récupérer le texte résultant.
3. Traiter ensuite ce texte exactement comme un message texte natif entrant dans le
   topic (mêmes règles de routage vers Devaimazing/PM, mêmes règles de commandes
   slash si applicable — une commande slash n'a probablement pas vocation à être
   dictée à l'oral, le cas d'usage principal du vocal est donc le message libre).

## Choix technique : Whisper local, pas API

Whisper tourne en local (via Ollama si le support y est suffisamment mature au
moment de l'implémentation, ou via `whisper.cpp` en sous-process en fallback),
cohérent avec l'objectif de minimisation des tokens API payants qui traverse tout le
projet (voir ADR 0006). L'API Whisper d'OpenAI (payante) est explicitement écartée.

**Vérification requise à l'implémentation, pas figée ici** : l'état du support audio
d'Ollama était incertain au moment de cette réflexion (intégration audio instable
pour d'autres modèles multimodaux type Gemma, avril 2026) ; Whisper est un modèle ASR
dédié, potentiellement à un niveau de maturité différent, mais ce n'est pas vérifié
par cet ADR. **Qui implémente doit vérifier l'état réel du support Whisper dans
Ollama à ce moment-là** plutôt que de se fier à cette note qui peut être obsolète. Si
Ollama n'est pas fiable pour Whisper à ce moment, `whisper.cpp` en sous-process est
l'alternative retenue par défaut (mature, éprouvée, indépendante d'Ollama).

**Contrainte RAM à vérifier à l'implémentation** : ADR 0006 documente une contrainte
de 24 Go de RAM unifiée (Mac mini M4 Pro) avec un seul modèle Ollama chargé à la fois
pour Qwen. Un Whisper local ajoute un second modèle potentiellement actif
simultanément (transcription pendant qu'un run Qwen tourne) — Whisper Small est léger
(quelques centaines de Mo à ~1 Go selon quantification) mais l'implémentation doit
vérifier l'absence de contention mémoire réelle plutôt que de le supposer.

## Choix de taille de modèle Whisper

**Whisper Small retenu par défaut** : suffisant pour l'usage de pilotage court et
impératif attendu (« lance le run X », « où en est Y ») — pas de dictée de documents
longs. **Whisper Large v3** reste une option de configuration si la précision
s'avère insuffisante en usage réel (accent, vocabulaire technique mal reconnu par le
modèle léger).

**Ce choix doit être configurable** (dans `config/studio.yml` ou l'équivalent prévu
pour la config du bot Telegram une fois implémenté), pas figé en dur dans le code.
Aucune clé n'est ajoutée à `config/studio.yml` par cet ADR : le fichier ne contient
aujourd'hui aucun code qui la lirait (voir « Conséquences »), l'ajouter maintenant
créerait une configuration orpheline.

## Explicitement hors périmètre de cette décision

- **Aucune génération vocale en retour** : Devaimazing/le PM répondent toujours en
  texte, pas de synthèse vocale de réponse. Non demandé, pas ajouté par anticipation.
- **Aucune utilisation de la capacité audio native d'un LLM multimodal** (Gemma ou
  autre) pour cette fonction. Whisper (modèle ASR dédié) est spécifiquement préféré à
  un LLM multimodal généraliste faisant à la fois transcription et compréhension,
  jugé moins fiable et moins mature à ce stade qu'un pipeline en deux étages avec un
  modèle de transcription dédié.

## Conséquences

- `prompts/devaimazing.md` gagne une note explicite (section « Origine des messages »)
  rappelant que Devaimazing traite un texte transcrit exactement comme un texte tapé,
  sans branche de logique dédiée — pour qu'une implémentation future ne réintroduise
  pas cette distinction par erreur.
- `ARCHITECTURE.md`, composant « Interface Telegram » (ADR 0013), gagne une mention
  du support vocal comme capacité décidée mais non implémentée.
- `docs/agents.md`, section Devaimazing, gagne la même précision.
- `README.md`, structure du dépôt, gagne l'entrée de cet ADR (fichier réel sur
  disque).
- **Non fait par cet ADR, laissé à une implémentation future** : aucun code n'est
  écrit. Ni `runtime/studio/tools/whisper.py` (wrapper de transcription, sur le
  modèle de `ollama.py`/`claude_code.py` déjà existants), ni la détection
  voice/audio côté bot Telegram, ni une clé de configuration `config/studio.yml`
  pour la taille de modèle Whisper, ne sont créés par cet ADR — ils dépendent d'un
  bot Telegram et d'une intégration Devaimazing qui n'existent pas encore
  (voir ADR 0013, « Conséquences », même réserve).

## Points laissés ouverts, à trancher à l'implémentation

- État réel du support Whisper dans Ollama au moment de l'implémentation (voir
  ci-dessus) — conditionne le choix entre intégration Ollama et `whisper.cpp`.
- Contention RAM réelle entre Whisper et Qwen si les deux sont actifs simultanément.

## Alternatives rejetées

- **API Whisper d'OpenAI** : payante, contraire à l'objectif de minimisation des
  tokens/coûts API qui traverse tout le projet.
- **LLM multimodal généraliste (ex. Gemma) pour transcription + compréhension en un
  seul modèle** : jugé moins fiable et moins mature (au moment de cette réflexion)
  qu'un pipeline en deux étages avec un modèle ASR dédié.
- **Whisper Large v3 par défaut** : plus précis mais plus lent, disproportionné pour
  l'usage de pilotage court et impératif visé ; reste disponible en configuration si
  la précision de Small s'avère insuffisante en usage réel.
