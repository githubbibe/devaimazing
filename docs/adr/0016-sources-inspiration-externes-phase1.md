# ADR 0016 - Sources d'inspiration externes consultées par le PM en phase 1

**Date** : 2026-07-31
**Statut** : Accepté

## Contexte

Des catalogues publics de patterns et d'implémentations d'agents/apps LLM existent
(exemple ayant motivé cette réflexion : `github.com/Shubhamsaboo/awesome-llm-apps`,
100+ agents et patterns open source, Apache 2.0). Ces catalogues peuvent inspirer ou
accélérer une feature d'un projet développé par devaimazing (webaimazing-v2 ou tout
autre projet futur de l'écosystème *aimazing) — par exemple un pattern de RAG vision,
une UI générative pilotée par chat, un détecteur de dérive de périmètre.

**Ce mécanisme ne concerne pas devaimazing lui-même** (son propre runtime, ses propres
agents) — uniquement les projets que devaimazing développe pour d'autres. À ne pas
confondre avec l'idée notée le 2026-07-29 (`docs/roadmap.md`, point 8 du « Reste à
faire ») sur une boucle rétroactive d'évolution des agents *de devaimazing* : sujet
distinct, périmètre distinct.

Cette réflexion suit le même mouvement que l'ADR 0012 (checklist secrets) : une
préoccupation qu'on plaçait après coup (découvrir en aval qu'un pattern ou outil
existant aurait pu enrichir une feature) est repositionnée en amont, au moment du
cadrage, quand ça coûte le moins cher de changer de direction — même mécanique de
cascade que les ADR 0008 et 0012 (l'erreur la plus coûteuse naît à la racine du run,
pas en audit aval).

**Écart constaté par rapport à l'intuition de départ** : contrairement aux checklists
des ADR 0008/0012 (dont le contenu est écrit en dur dans `prompts/pm.md`, jamais lu
dynamiquement), ce mécanisme suppose un fichier de sources *maintenu à la main dans la
durée* (Steeve y ajoute des entrées au fil de ses découvertes) — un contenu figé dans
le prompt system deviendrait obsolète. Or le PM n'a, avant cet ADR, **aucun mécanisme
d'injection dynamique de fichier** dans son prompt système (contrairement à
Back/Front/Test/Architecte/Sécu, qui bénéficient tous de
`tools.filesystem.inject_skills`). Ce n'est pas une simple mise à jour de texte de
prompt : il faut câbler ce point d'injection pour la première fois côté PM.

## Décision

Un fichier `skills/inspiration-sources.md`, maintenu à la main par Steeve (pas de
synchronisation automatisée), liste des sources externes potentielles — nom, URL,
description courte de ce qu'elle couvre. Ce fichier est injecté dans le prompt système
du PM au moment du cadrage (phase 1, feature ou projet), sur le même mécanisme
technique que `inject_skills` (texte brut concaténé, pas de RAG).

**Principe non négociable : le PM propose, Steeve décide, jamais l'inverse.** Ce
mécanisme ne permet à aucun moment au PM de décider unilatéralement d'intégrer un
pattern externe dans le périmètre d'un projet — prolongement direct du principe déjà
établi par la checklist d'intention (ADR 0008, principe 8 d'`ARCHITECTURE.md`) : le PM
ne comble jamais un trou ni n'ajoute une dimension par lui-même, il signale et
propose, Steeve valide ou non.

### Mécanisme en quatre étapes

1. **Fichier de sources léger.** `skills/inspiration-sources.md` — une liste courte,
   pas un corpus à indexer. Chaque entrée : nom, URL, description courte (de quoi
   savoir si la source mérite d'être creusée, pas son contenu détaillé).
2. **Le PM consulte ce fichier en phase 1.** Injecté dans son prompt système comme les
   skills des autres agents (coût quasi nul : une liste courte, pas un RAG).
3. **Détection de ressemblance et proposition.** Si, au cours du dialogue de cadrage,
   le PM identifie qu'une dimension du projet en cours de cadrage ressemble à ce que
   couvre une source listée, il le signale explicitement à Steeve et **propose**
   d'investiguer — il ne décide pas seul de creuser, il demande.
4. **Investigation ciblée si validée.** Si Steeve valide, le PM effectue une recherche
   ciblée sur la source précise (`web_fetch` sur son URL, ou une recherche web
   restreinte à cette source) — jamais une recherche exploratoire large sur le sujet
   en général. Coût token assumé consciemment, uniquement quand un signal de
   pertinence a déjà été établi par l'étape 3, jamais systématiquement à chaque run.

**Pourquoi le PM investigue lui-même (pas Steeve en dehors du run)** : renvoyer la
recherche à Steeve casserait le flux de dialogue itératif de la phase 1. Le PM reste
le pilote du cadrage de bout en bout, y compris pour cette étape d'investigation
ponctuelle.

## Raisons

1. **Allocation par la cascade, même mécanique que les ADR 0008/0012** : repérer
   qu'un pattern externe existe déjà coûte une question en phase 1 ; le découvrir
   après implémentation coûte une réimplémentation.
2. **Mécanisme plutôt que mémoire** : sans liste explicite consultée systématiquement,
   la détection de ressemblance dépend de l'inspiration du jour du modèle — pas fiable
   comme mécanisme, pareil que les checklists des ADR 0008/0012.
3. **Coût borné par construction** : liste courte, maintenue à la main, jamais
   synchronisée automatiquement — le fichier reste petit par construction, pas de
   dérive de coût token à mesure que les catalogues externes grossissent.
4. **PM propose, humain décide** : cohérent avec le principe déjà établi (ADR 0008) —
   étendre ce principe à un nouveau type de signal (ressemblance à une source externe)
   plutôt que d'inventer une nouvelle relation de décision.

## Conséquences

- `skills/inspiration-sources.md` créé, avec une première entrée (`awesome-llm-apps`,
  la source ayant motivé cette réflexion).
- **Nouveau point d'injection côté PM** (n'existait pas avant cet ADR) :
  `nodes/pm.py` gagne une fonction qui construit le prompt système de phase 1 en
  concaténant `prompts/pm.md` et `skills/inspiration-sources.md` (absence tolérée,
  dégradation silencieuse si le fichier n'existe pas — mécanisme optionnel, pas
  bloquant) — réutilisée par le dialogue terminal (`_run_cadrage`, `_run_brief_import`)
  et par le dialogue Telegram (`studio.telegram.pm_dialogue._start_dialogue`, ADR
  0015), pour ne pas dupliquer la construction entre les deux canaux.
- `prompts/pm.md` gagne une section « Sources d'inspiration externes », dans les
  responsabilités de phase 1, aux côtés des checklists d'intention et de sécurité.
- `docs/workflow.md` (phase 1) documente ce troisième mécanisme.
- `ARCHITECTURE.md` gagne un principe 12, symétrique aux principes 8 et 11 côté
  relation PM/humain, mais distinct par nature (signal externe optionnel, pas
  checklist obligatoire à chaque run).
- `README.md` (arborescence `skills/`) référence le nouveau fichier.

## Alternatives rejetées

- **Un RAG complet** sur le contenu de ces catalogues (embeddings, base vectorielle) :
  jugé disproportionné, même raisonnement et même conclusion que le rejet du RAG pour
  la mémoire de l'agent Devaimazing (ADR 0013, Décision 3) — le volume réel (une liste
  de sources à parcourir manuellement) ne justifie aucun étage d'indexation.
- **Une recherche web systématique à chaque run**, sans filtre préalable : coûte cher
  (la phase 1 tourne sur Opus) et risque de mal cibler, comme n'importe quelle
  recherche non guidée — l'étape 3 (détection de ressemblance avant investigation)
  existe précisément pour éviter ce coût par défaut.
- **Synchronisation automatisée** du fichier de sources avec l'état réel des
  catalogues externes (qui évoluent au fil du temps) : la mise à jour reste manuelle
  et volontaire, à la charge de Steeve — cohérent avec le choix déjà fait pour
  `skills/*.md` en général (fichiers courts maintenus à la main, pas de pipeline de
  synchronisation à construire).
- **Fusionner ce mécanisme dans la checklist d'intention (ADR 0008)** : nature de
  question différente (un signal de ressemblance à une source externe, pas une
  question structurelle de contrôle utilisateur par dimension) — même raisonnement de
  séparation que l'ADR 0012 vis-à-vis de l'ADR 0008.

## Reste explicitement ouvert

- **Comportement de Claude Code CLI en mode `-p` (headless) face à un outil réseau
  (`web_fetch`/recherche web) non vérifié empiriquement dans ce dépôt** — seul le
  comportement des outils Read/Glob/Grep (autorisés sans invite) et Write/Edit/Bash
  (refusés proprement) a été vérifié à ce jour (voir `tools/claude_code.py`, docstring
  de `run_claude_code`). Si l'étape 4 (investigation) se heurte à un refus d'outil
  systématique, le mécanisme dégrade déjà gracieusement (refus non fatal, contenu
  textuel produit quand même) — mais l'investigation elle-même resterait alors
  impossible en pratique, à constater en usage réel plutôt qu'à présumer ici.
- **Format de `skills/inspiration-sources.md` au-delà d'une liste plate** (regroupement
  par catégorie si le fichier grossit) — non tranché, le fichier est amené à rester
  court par construction.
- **Extension à la phase 2 (Architecte)** : non tranchée par cet ADR, qui ne statue
  que sur le PM en phase 1. Une extension à l'Architecte demande une décision séparée.
