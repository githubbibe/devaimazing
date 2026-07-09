# ADR 0010 - Quatre piliers non-fonctionnels obligatoires et dette justifiée

**Date** : 2026-07
**Statut** : Accepté

## Contexte

devaimazing doit garantir quatre piliers non-fonctionnels sur tout ce qu'il produit :
résilience, gestion d'erreurs, scalabilité, observabilité. Trois de ces quatre piliers
ont déjà un mécanisme dédié dans le dépôt : `skills/error-handling.md`,
`skills/retry-patterns.md` (résilience), et le couple `skills/logging-conventions.md` /
`docs/metrics.md` (observabilité). La scalabilité n'a ni skill dédié ni section propre
dans `templates/architect-map.md.template`, la fiche de contraintes non-fonctionnelles
que l'Architecte maintient et qui s'applique à tous les agents d'un run.

Ce trou est structurellement le même que celui traité par l'ADR 0008 (checklist
d'intention Phase 1), mais côté technique plutôt que produit : une dimension sans
mécanisme de contrôle explicite finit par être décidée par défaut, silencieusement,
sans que personne n'ait choisi de l'assumer. Une section de contraintes non-fonctionnelles
laissée vide dans l'architect-map est indiscernable, pour un futur run ou un futur agent,
entre un oubli et un choix délibéré (par exemple : scalabilité volontairement limitée
faute de ressources CPU disponibles, ou effets de bord entre deux features tolérés tant
que le volume ne justifie pas de les isoler).

## Décision

Les 4 piliers (résilience, gestion d'erreurs, scalabilité, observabilité) sont des
dimensions non-fonctionnelles obligatoires, vérifiées par l'Architecte en phase 2
(cadrage des contraintes non-fonctionnelles) et maintenues en phase 9 (architect-map),
aussi bien pour devaimazing lui-même que pour tout projet produit.

**Garde-fou (symétrique à l'ADR 0008)** : toute dette assumée sur l'un des 4 piliers doit
être explicitement justifiée et documentée dans l'architect-map. Une section vide sans
justification n'est pas un défaut acceptable — elle remonte au checkpoint humain le plus
proche, au même titre qu'un trou d'intention.

**Interdiction** : l'Architecte ne laisse jamais une section de contraintes
non-fonctionnelles vide sans justification écrite. « Pas encore traité » n'est pas une
justification ; « CPU limité au Mac mini local, charge estimée sous le seuil critique »
en est une.

Concrètement :
- `templates/architect-map.md.template` gagne une section **Scalabilité**, et chacune
  des 4 sections piliers gagne un champ **Dette assumée / Justification**.
- Un skill dédié `skills/scalability.md` est créé, sur le modèle de
  `retry-patterns.md` / `error-handling.md`, pour normaliser les patterns attendus
  (pagination, requêtes N+1, bornage mémoire, isolation des effets de bord entre
  features).
- `templates/architect-map.md.template` gagne aussi une section **Hors champ (connu,
  différé)** listant Performance, Disponibilité et Accessibilité comme dimensions
  identifiées mais volontairement non traitées au stade POC/LVP (voir section
  « Périmètre » ci-dessous).

## Périmètre : dimensions hors champ pour le POC/LVP

Les standards industriels (ISO/IEC 25010, Google SRE) reconnaissent d'autres dimensions
non-fonctionnelles au-delà des 4 piliers retenus ici : **performance/latence**
(distincte de la scalabilité — temps de réponse et usage ressource à charge constante,
plutôt que tenue face à la croissance du volume), **disponibilité** (SLA/uptime,
distincte de la résilience qui décrit le comportement pendant/après une panne), et
**accessibilité** (a11y, pertinente pour les produits cibles avec UI grand public comme
webaimazing).

Ces trois dimensions sont explicitement **hors champ pour l'instant** : le stade
actuel (POC/LVP) ne les justifie pas, et les traiter maintenant serait du surcadrage.
Ce n'est pas un trou silencieux au sens de l'ADR 0008/0010 — c'est un choix de périmètre
assumé et écrit : on sait qu'il faudra les traiter, mais pas à ce stade.

Conséquence pratique : elles n'ont ni garde-fou obligatoire ni section « Dette assumée »
dans l'architect-map (contrairement aux 4 piliers actifs), mais elles sont listées dans
une section dédiée du template (« Hors champ, connu, différé ») pour rester visibles et
ne pas être reperdues. Elles rejoignent le garde-fou de cet ADR dès que le projet dépasse
le stade POC/LVP — à réévaluer explicitement à ce moment-là, pas par glissement.

## Raisons

1. **Symétrie avec l'ADR 0008** : le mécanisme « un trou remonte, il n'est pas comblé
   silencieusement » protège déjà la dette d'intention business en Phase 1 (PM). Le même
   principe protège la dette non-fonctionnelle technique en Phase 2/9 (Architecte).
2. **Une dette non documentée est indiscernable d'un oubli.** Sans justification écrite,
   ni l'Architecte d'un run ultérieur ni un humain relisant l'architect-map ne peuvent
   savoir si l'absence de mécanisme de scalabilité est un choix maîtrisé ou un angle mort.
3. **Cohérence de couverture** : 3 piliers sur 4 avaient déjà un skill dédié ; le seul
   absent (scalabilité) est aussi le plus susceptible de rester informel sans repère
   écrit, faute d'un endroit où le documenter.

## Conséquences

- Le brief Architecte (phase 2) inclut désormais explicitement les 4 piliers dans sa
  checklist de contraintes non-fonctionnelles.
- `templates/architect-map.md.template` gagne la section Scalabilité et un champ Dette
  assumée par pilier.
- `skills/scalability.md` rejoint le corpus consulté par les agents producteurs
  (Back, Front) et par l'Architecte en audit.
- Une section pilier vide sans justification dans l'architect-map est traitée comme un
  trou de dette technique et remonte au checkpoint humain le plus proche.

## Alternatives rejetées

- **Laisser la scalabilité informelle, gérée au cas par cas par l'Architecte** :
  reproduit exactement le risque que l'ADR 0008 a identifié côté produit — une
  dimension sans mécanisme de contrôle explicite finit décidée par défaut. Rejetée.
- **Fusionner scalabilité dans résilience** : les deux dimensions répondent à des
  questions différentes (résilience = tenir face à une panne/erreur transitoire ;
  scalabilité = tenir face à la charge/au volume) et mobilisent des patterns distincts
  (retry/circuit breaker vs pagination/isolation des effets de bord). Rejetée pour ne
  pas diluer le pilier.
