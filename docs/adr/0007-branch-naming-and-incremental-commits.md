# ADR 0007 - Nommage de branche et commits incrémentaux

**Date** : 2026-07  
**Statut** : Accepté

## Contexte

Un run peut échouer à n'importe quelle phase (agent local en échec, non-régression
cassée, audit sécu bloquant). Sans points de restauration intermédiaires, une erreur
tardive obligerait à tout refaire depuis le début du run. Par ailleurs, le nommage
des branches doit être à la fois lisible et garanti unique.

## Décision

### Commits incrémentaux

Un commit est réalisé à la fin de chaque tâche d'agent terminée (phases 4 à 9),
pas uniquement en phase 10. Chaque commit est signé sous l'identité Git de l'agent
qui l'a produit (voir `docs/agents.md`).

La phase 10 ne committe plus en bloc. Elle se limite à la mise à jour du project-map,
au merge de la branche vers `develop` après validation finale, et à la notification.

### Nommage de branche

Format : `studio/<slug-feature>-<hash5>`

- `<slug-feature>` : nom donné par l'utilisateur en phase 1 (dialogue avec le PM),
  slugifié par le runtime (minuscules, espaces remplacés par des tirets, caractères
  spéciaux retirés).
- `<hash5>` : 5 premiers caractères d'un hash calculé sur (timestamp + nom de la feature),
  ajoutés en suffixe pour garantir l'unicité même en cas de noms de feature identiques
  ou proches.

Exemple : `studio/features-qui-fait-tout-a3f9c`

### Moment de création de la branche

La branche est créée au **démarrage effectif du run** (fin de la phase 3, fiches
dépendantes écrites), pas pendant le dialogue de cadrage de la phase 1. Un cadrage
abandonné ou en cours de discussion ne laisse donc aucune branche orpheline.

## Raisons

1. **Points de restauration** : un commit par tâche permet de revenir à un état
   stable précis en cas d'échec ou de renvoi, sans perdre le travail des agents
   précédents dans la séquence.

2. **Unicité du nommage sans perte de lisibilité** : le hash sur timestamp+nom
   élimine le risque de collision (deux features de même nom à des moments différents)
   tout en gardant un nom de branche lisible en premier segment.

3. **Pas de branche pour un cadrage abandonné** : créer la branche seulement au
   démarrage du run évite d'accumuler des branches vides si l'utilisateur change
   d'avis pendant le dialogue de cadrage.

## Conséquences

- Le nom de la feature est fourni par l'utilisateur, pas généré automatiquement.
  Si l'utilisateur choisit un nom peu soigné, la branche portera ce nom (assumé).
- Le slug est produit par le runtime (fonction déterministe, zéro token), pas par le PM.
- En cas de renvoi (boucle de feedback), l'agent corrige et commit à nouveau sur
  la même branche, pas de nouvelle branche créée.

## Alternatives rejetées

- **Commits groupés en phase 10 uniquement** : pas de point de restauration
  intermédiaire, un échec tardif oblige à tout refaire. Rejeté.
- **Nommage par date seule** (`studio/20260703-slug`) : collision possible si
  plusieurs runs sur la même feature le même jour. Rejeté.
- **Nommage par numéro de run seul** (`studio/run-042`) : peu lisible dans un
  `git branch` sans contexte sur le contenu de la branche. Rejeté au profit du slug.
