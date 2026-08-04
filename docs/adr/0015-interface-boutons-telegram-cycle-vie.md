# ADR 0015 - Interface à boutons Telegram : cycle de vie projet et feature

**Date** : 2026-07-30
**Statut** : Accepté — conception uniquement, pas encore implémenté. Complète l'ADR
0013 (S1-S4 livrées) sans le contredire ; ne remplace aucune de ses décisions.

## Contexte

L'ADR 0013 a livré un bot Telegram fonctionnel (registre d'outils, confirmation
Oui/Non, compréhension du langage naturel via Devaimazing), mais deux limites
restaient ouvertes (`docs/roadmap.md`, « Reste à faire », points 7 et 10) :

- Aucune navigation par bouton : `/status`/`/progression` exigent de taper un
  `run_id` à la main, `/new`/`/archive` ne couvrent pas tout le cycle de vie d'un
  projet (pas d'édition), et rien ne couvre le cycle de vie d'un run
  (`runs`/`resume`/`retry`/`metrics` restent CLI-only, `cli.py`).
- Aucun moyen de supprimer un run raté — cruft observée en conditions réelles sur
  `webaimazing-v2` (commits « sauvegarde » automatiques, `specs/run-NNN/` orphelins,
  checkpoints `state.db` orphelins).

Le dialogue de cadrage du PM (phase 1) est aujourd'hui strictement un mécanisme
terminal (`input()`/`print()` synchrone, `runtime/studio/nodes/pm.py`) — cette ADR le
porte dans un topic Telegram, ce qui change la nature du second problème plus qu'elle
ne le résout par un outil dédié (voir Décision 8).

## Décision 1 — Indicateur de traitement natif, pas d'animation personnalisée

`send_chat_action(chat_id, "typing")`, renvoyé périodiquement tant qu'un traitement
dure, disparaît automatiquement à l'envoi de la réponse. Rejeté : message animé
personnalisé (cycle de motifs de points édité à intervalles réguliers) — plus fidèle
visuellement mais consomme des appels `edit_message` répétés (limite Telegram ~1/s)
et demande de gérer un état supplémentaire (id du message à éditer/supprimer) pour un
gain non retenu comme nécessaire.

## Décision 2 — Commandes revues

**Révoquées**, remplacées par la navigation par boutons (Décision 7) ou par une
fonctionnalité déjà native à Telegram :
- `/status`, `/progression` — remplacées par la navigation projet → feature → run.
- `/projects` — remplacée par la liste native des topics du groupe (barre des sujets
  Telegram) ; un topic = un projet (ADR 0013, Décision 2) rend une commande dédiée
  redondante.

**`/new` scindée** en deux commandes distinctes (nommage volontairement explicite,
pas de commande générique ambiguë) :
- `/new_project` (General) — voir Décision 3.
- `/new_feature` (topic-projet, ou General avec sélection préalable du topic) — voir
  Décision 4.

**Conservées, comportement précisé** : `/archive` (Décision 5), `/stop` (Décision 6).

**Différée** : `/reject` — un mécanisme de rejet de checkpoint est identifié comme
utile (revenir sur la *conception* d'une feature déjà cadrée, en rouvrant le dialogue
PM sur sa fiche — distinct du mécanisme `## Feedback` déjà existant, qui corrige un
*comportement* non voulu pendant l'implémentation, boucle `inner_retry_limit`), mais
aucun cas d'usage concret ne s'est encore présenté pour trancher son design précis.
Reste `NotImplementedError` comme aujourd'hui.

## Décision 3 — `/new_project` (General)

Demande un nom (ex. `webaimazing-v2`), puis exécute dans l'ordre, avec un message de
progression dans le topic nouvellement créé à chaque étape franchie :

```
Le projet <nom> est en cours de création.
Le dossier <nom> a été créé.
Le repo local a été créé.
Le repo distant a été créé.
Le Project Manager attend maintenant une description du nouveau projet, ou versez
une fiche projet.md.
```

L'utilisateur répond par une description libre tapée dans le topic, ou en envoyant un
fichier `fiche projet.md`. Le PM engage alors, **dans ce topic**, le même mécanisme de
dialogue itératif que la phase 1 actuelle (`QUESTION:`/`FICHE_VALIDEE:`,
`runtime/studio/nodes/pm.py::_run_validation_dialogue`), mais porté sur le **projet
dans son ensemble** plutôt que sur une feature isolée — produit la fiche projet
(Décision 8) puis propose un ordonnancement initial (`planification.md`, Décision 9).

Contrairement au dialogue de cadrage d'un run existant (terminal, `input()`/`print()`
synchrones), ce dialogue tourne message par message dans le topic Telegram — chaque
tour attend la réponse de l'utilisateur dans ce même canal.

## Décision 4 — Cycle de vie d'une feature : `/new_feature` et `/run`

**`/new_feature`** (topic-projet, ou General + sélection du topic) : lance le même
mécanisme de dialogue PM que ci-dessus, mais scope feature (équivalent de la phase 1
actuelle). Règle déjà actée sur `prompts/pm.md` (2026-07-29/30) : une question par
tour, nom de la feature demandé en premier, seul, avant tout autre point. Produit une
fiche feature (Décision 8). **Chaque nouvelle fiche déclenche une re-proposition
complète de l'ordre** dans `planification.md` par le PM (pas un simple ajout en fin de
liste) — voir Décision 9.

**`/run <nom_feature>`** (topic-projet uniquement — nécessite le contexte projet du
topic, pas d'équivalent General) :
- Compare le hash du commit de la fiche feature à celui enregistré lors du dernier run
  de cette feature (stocké dans `planification.md`, voir Décision 9). Hash identique
  → répond « rien à implémenter, tout a déjà été fait », aucun run lancé. Hash
  différent (fiche jamais traitée, ou modifiée depuis) → lance le run.

  Alternative rejetée : versionner les fiches et reporter un numéro de version dans
  les commentaires du code produit (ex. « code produit selon la fiche xxx.md en
  version yyyy.yyy.yy »). Rejetée au profit du hash/commit : une comparaison de hash
  garantit objectivement l'absence de changement, alors qu'un numéro de version
  dépend d'un incrément manuel — source d'erreur humaine que le hash élimine
  structurellement.
- Avancement affiché par **édition du même message** Telegram (pas un nouveau message
  par étape). Si les événements d'avancement arrivent plus vite qu'1/seconde (limite
  Telegram), ils sont regroupés en une seule édition plutôt que de risquer un rejet
  API ou une perte d'événements.
- Un checkpoint humain rencontré **en cours d'exécution** (audit Architecte/Sécu après
  implémentation, distinct du dialogue de cadrage initial) ne nécessite aucun
  mécanisme dédié : il retombe sur le principe déjà établi — un message est posté dans
  le topic, l'utilisateur y répond, exactement comme le reste du dialogue.

## Décision 5 — `/archive`

`/archive <nom>` (General, argument requis — pas de contexte projet implicite) ;
`/archive` (topic-projet, sans argument — le topic donne le contexte). Confirmation
obligatoire dans les deux cas (aligné sur la classification `destructif` de l'ADR
0013, Décision 4). **Changement de comportement** par rapport à l'implémentation
actuelle (`_handle_archive_projet`, ferme le topic via `close_forum_topic`) : le topic
est désormais **supprimé**, pas seulement fermé, pour que la liste native des topics
(Décision 2) reste toujours à jour (uniquement les projets actifs).

## Décision 6 — `/stop`

Reprend la classification de l'ADR 0013 (destructif, sauvegarde automatique de l'état
avant arrêt) mais **déroge explicitement à la règle de confirmation systématique** :
arrêt **immédiat, sans confirmation**, traité en **priorité absolue** même si le bot
est occupé ailleurs (dialogue PM en cours, run en cours de streaming). No-op (message
informatif) si aucun processus n'est en cours. Cette dérogation est justifiée par le
contexte d'urgence que la commande sert — attendre une confirmation irait contre son
objectif. Pour la même raison, `/stop` reste **hors de l'arborescence de boutons**
(Décision 7) : la faire passer par plusieurs niveaux de menu contredirait l'exigence
d'immédiateté ; elle reste une commande tapée directement.

## Décision 7 — Arborescence de boutons

**Racine General** :
- `[Nouveau projet]` → Décision 3
- `[Nouvelle feature]` → sélection du topic (projet) → Décision 4 (`/new_feature`)
- `[Lancer une feature]` → sélection du topic (projet) → liste des features de
  `planification.md` avec leur statut → Décision 4 (`/run`)
- `[Archiver ce projet]` → sélection du topic (projet) → Décision 5

**Racine Topic-projet** : les trois mêmes boutons (hors « Nouveau projet », qui n'a pas
de sens dans un topic déjà lié à un projet), **sans** l'étape de sélection — le
contexte est déjà donné par le topic.

**Révision (2026-08-01)** : le bouton persistant « Menu ▶ » (clavier de réponse
Telegram, remplace la commande tapée `/menu`) a **priorité absolue**, au même titre
que `/stop` (Décision 6) — il n'est **plus** soumis à l'interception normale par un
dialogue de cadrage PM ou un run en attente. Décision initiale inversée : un clic
pendant un dialogue en attente était avalé comme réponse à la question du PM au lieu
d'afficher le menu, sans aucun retour visible côté utilisateur — gap constaté en usage
réel (`todolist3`, cadrage de la feature `gestion-taches`). Contrairement à `/stop`,
« Menu ▶ » reste une simple navigation UI sans effet destructif : le dialogue ou run
interrompu par ce court-circuit n'est ni annulé ni perdu, il reste en attente et
reprend normalement à la prochaine réponse de l'utilisateur.

**Révision (2026-08-04)** : ajout de `[Modifier une feature]` (General et
Topic-projet, même sélection de topic/liste que `[Lancer une feature]`) — gap
constaté en usage réel (`todolist3`) : aucun bouton ne permettait de reprendre le
cadrage d'une feature déjà validée (à faire, en cours, ou déjà terminée) pour la
corriger. Sélectionne une feature dans la liste de `planification.md`, lit sa fiche
actuelle (`specs/<run_id>/card-root.md`) et démarre un dialogue de cadrage PM **seedé**
avec ce contenu (le PM voit l'existant et demande ce qui doit changer, au lieu de
repartir d'une page blanche). Valide toujours vers un **nouveau** `run_id` — la ligne
`planification.md` de cette feature est remplacée (Décision 9, indexée par nom de
feature), ce qui signale naturellement à `/run` qu'une nouvelle version doit être
produite. Le code déjà fusionné dans `develop` par le run précédent (voir
`studio.nodes.closer`) n'est **pas retiré** : le nouveau run doit l'**adapter**, pas
repartir de zéro — un retour en arrière (revert du merge) serait risqué (réécriture
d'historique, casserait d'éventuelles features postérieures qui en dépendent) pour un
bénéfice non établi.

**Révision (2026-08-04)** : les 3 boutons liés à une feature (`Nouvelle feature`,
`Modifier une feature`, `Lancer une feature`) sont regroupés derrière un bouton racine
unique **`Feature...`**, qui ouvre un sous-menu `[Créer, Modifier, Lancer]` — racine
moins chargée (3 boutons hors ce sous-menu, au lieu de 5). Chaque bouton du sous-menu
porte le même `callback_data` que l'ancien bouton racine direct
(`menu:new_feature`/`menu:modifier_feature`/`menu:run_feature`) : aucun changement du
dispatch en aval, un niveau de navigation purement visuel en plus. `◀ Retour` depuis ce
sous-menu ramène toujours à `menu:root` (pas au sous-menu précédent), cohérent avec le
principe déjà posé plus haut (pas de vrai back-stack).

## Décision 8 — Contenu des fiches

- **Fiche projet** : nom, objectif, utilisateurs cibles, contraintes, périmètre.
- **Fiche feature** : nom, objectif, critères d'acceptation, périmètre.

Volontairement plus courtes que le template `card-root.md.template` actuel (pas de
checklist d'intention/sécurité détaillée dans ce premier cadrage) — champ à réconcilier
avec le contrat existant au moment de l'implémentation, pas tranché ici.

## Décision 9 — `planification.md`

**Un seul fichier** (pas de fichier séparé « ordonnancement » + « planification ») —
alternative rejetée : deux fichiers distincts (liste structurée d'un côté, raisonnement
de l'autre), rejetée parce qu'ils porteraient sur le même sujet et risqueraient de
diverger (l'un mis à jour, pas l'autre) sans bénéfice fonctionnel identifié.

Contenu :
- Features groupées par sous-phases optionnelles (ex. « Phase 1 : fondations »).
- Pour chaque feature, dans l'ordre : statut (à faire / en cours / fait), hash du
  commit de sa fiche (source unique de vérité pour la détection de changement de
  `/run`, Décision 4 — pas de mécanisme de suivi séparé).
- Une section expliquant le raisonnement derrière l'ordre retenu.

Réévalué en entier par le PM à chaque nouvelle fiche produite (Décision 4), pas
simplement complété en fin de liste.

**Révision (2026-08-04)** : ajout d'une colonne **hash du commit de merge** (vers
`git.base_branch`, renseigné par `studio.nodes.closer` à la fin d'un run réussi) — un
hash Git réel, distinct du hash de contenu de la fiche (calculé plus tôt, avant tout
commit, voir `studio.tools.planification`). Objectif : associer durablement une version
de fiche à sa version de code livrée, prérequis à la reprise/édition d'une feature déjà
exécutée (Décision 7, `[Modifier une feature]`). Compatibilité ascendante : une ligne
écrite avant l'introduction de ce champ (4 colonnes) reste lisible, valeur `None`.

## Conséquences

**Ce que ça couvre** : cycle de vie complet projet (création → cadrage → archivage) et
feature (cadrage → exécution), navigation par boutons cohérente avec le registre
d'outils existant (toute action reste destinée à passer par `execute_tool`, principe
structurant de l'ADR 0013 Décision 4, non remis en cause ici).

**Ce qui reste explicitement ouvert, reporté à une session future** :
- Détail du mécanisme `/reject` (pas de cas d'usage concret encore identifié).
- Processus de récupération et d'analyse en cas de **crash** d'un run (pas de checkpoint
  de pause, un plantage réel) — non cadré.
- La suppression d'un run raté (`docs/roadmap.md`, point 10) est jugée **superflue**
  par ce nouveau flux : la cruft qui la motivait (commits « sauvegarde » automatiques,
  checkpoints orphelins) provenait spécifiquement du process CLI interrompu au clavier
  (`Ctrl+C`) ; avec un dialogue de cadrage porté par le topic Telegram (pas de process
  local à interrompre brutalement) et un `/run` qui ne produit de commits que s'il y a
  effectivement quelque chose de nouveau à faire (Décision 4), cette cause disparaît
  largement. Pas de nouvel outil de suppression prévu sur cette base.
- Phasage d'implémentation non tranché dans cette ADR — à définir au moment de
  l'implémentation.
