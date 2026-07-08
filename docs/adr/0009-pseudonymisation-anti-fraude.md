# ADR 0009 - Pseudonymisation et traçabilité anti-fraude

**Date** : 2026-07  
**Statut** : Accepté

## Contexte

Les projets produits par devaimazing (webaimazing et autres) collectent des données
comportementales utilisateur (tracking de visites, parcours, événements) à des fins
d'observabilité et de diagnostic. Un besoin légitime existe aussi de pouvoir détecter
et remonter des comportements de fraude.

Une anonymisation irréversible (personne, y compris l'éditeur, ne peut recorréler une
donnée à une identité) est incompatible avec ce besoin anti-fraude. Il faut donc un
mécanisme qui protège par défaut, mais qui permette une ré-identification encadrée
en dernier recours.

Par ailleurs, un projet produit par devaimazing peut être cloné par un tiers. Ce tiers
doit pouvoir choisir si ses données d'usage restent strictement locales, ou s'il
accepte de les partager (anonymisées/pseudonymisées) vers un système d'observabilité
mutualisé (Loki centralisé).

## Décision

### Pseudonymisation by design, pas anonymisation

Toute donnée comportementale exportée vers un système d'observabilité (Loki ou
équivalent) est **pseudonymisée par construction** : aucun identifiant direct
(nom, email, ID utilisateur clair) ne transite dans le flux exporté. Un identifiant
pseudonyme (généré, à sens unique dans l'usage courant) sert de clé de corrélation
dans les logs et métriques.

### Table de correspondance étanche

La correspondance pseudonyme ↔ identité réelle est stockée dans une table strictement
séparée du flux d'observabilité :
- Accès réseau restreint (non accessible depuis le réseau où tourne Loki/Grafana/Alloy)
- Accès applicatif restreint aux credentials admin, distincts des credentials d'usage
  courant (support, monitoring)
- Chiffrement au repos recommandé

### Traçabilité de la ré-identification

Toute levée de pseudonymisation (un admin associe un pseudonyme à une identité réelle)
est elle-même tracée dans un audit log dédié : qui a fait la demande, quand, pour
quelle raison déclarée. Cet audit log est traité avec le même niveau de rigueur que
les données qu'il protège.

### Choix de gouvernance pour un projet cloné

Tout projet produit par devaimazing qui inclut du tracking comportemental expose une
option de configuration `analytics_mode` :
- `local` : les données restent dans une base locale à l'installation du cloneur,
  rien n'est exporté vers un système mutualisé.
- `shared` : le cloneur accepte explicitement que ses données pseudonymisées
  remontent vers un système d'observabilité centralisé.

Le module de tracking est architecturé avec une interface d'abstraction (adaptateur)
permettant de router vers l'une ou l'autre destination selon la configuration, sans
dupliquer la logique métier.

### Base légale (RGPD)

La pseudonymisation reste dans le périmètre RGPD (contrairement à l'anonymisation
irréversible). La base légale retenue pour la collecte anti-fraude est l'intérêt
légitime, à documenter explicitement dans la politique de confidentialité du projet
livré. Durée de conservation, droit d'accès et de suppression doivent être respectés,
sous réserve des exceptions légales liées à une enquête en cours.

## Raisons

1. **Le besoin anti-fraude est incompatible avec l'anonymisation forte.** Sans
   capacité de ré-identification, aucune enquête sur un comportement suspect n'est
   possible. La pseudonymisation réversible par le seul détenteur est le compromis
   qui préserve les deux besoins (protection par défaut, capacité d'enquête).

2. **L'étanchéité de la table de correspondance est ce qui rend la pseudonymisation
   réelle plutôt que cosmétique.** Si la clé de jointure est accessible depuis le
   même périmètre que les données comportementales, la protection n'existe pas en
   pratique, seulement en théorie.

3. **Le choix `local`/`shared` respecte la souveraineté du cloneur.** Cohérent avec
   la philosophie AGPL du projet : le code est ouvert, mais l'usage des données
   reste sous le contrôle de celui qui déploie, pas imposé par le studio qui a
   produit le code.

4. **Tracer la ré-identification elle-même** est nécessaire pour que le pouvoir
   d'admin ne soit pas un accès silencieux et non auditable à des données protégées.

## Conséquences

- Tout projet produit par devaimazing qui inclut du tracking comportemental doit
  intégrer cette contrainte dès le cadrage (phase 1, checklist d'intention) et dès
  la conception (phase 2, brief Architecte).
- L'Architecte impose cette contrainte non-fonctionnelle systématiquement quand une
  fonctionnalité de tracking est détectée dans le périmètre d'un run.
- Un skill dédié (`data-privacy.md`) documente le pattern d'implémentation attendu.
- Un projet qui choisit `analytics_mode: shared` doit documenter clairement ce choix
  dans sa propre politique de confidentialité, distincte de celle de devaimazing.

## Alternatives rejetées

- **Anonymisation irréversible complète** : incompatible avec le besoin anti-fraude
  exprimé. Rejetée.
- **Pas de séparation entre données comportementales et identité** (statu quo actuel
  de webaimazing v1) : la ré-identification est triviale pour quiconque a accès à la
  base, pas de traçabilité de qui a fait quoi avec cette capacité. Rejetée.
- **Imposer `shared` par défaut sans option `local`** : contredit la philosophie de
  contrôle utilisateur/cloneur défendue ailleurs dans le projet (AGPL, choix explicite
  plutôt que subi — voir ADR 0008). Rejetée.
