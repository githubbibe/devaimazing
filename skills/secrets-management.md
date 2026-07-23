# Skill - Gestion des secrets

## Objectif

Ce skill s'applique à tout projet produit par devaimazing dont la fiche racine
(`card-root.md`) porte une contrainte de sécurité issue de la checklist de phase 1
(ADR 0012) : mots de passe admin, certificats, clés API, ou tout autre secret. Il
encode les patterns d'implémentation attendus pour que Back et Front respectent cette
contrainte sans avoir à la réinventer au cas par cas, et donne à l'Architecte une base
de vérification en phase 5 et en audit aval.

## Principe

Un secret n'est jamais commité en clair dans le repo cible, quel que soit le niveau de
sécurité retenu en phase 1 (par défaut ou renforcé par une contrainte légale/sponsor).
Le niveau exact de gestion (rotation automatisée, chiffrement au repos, séparation
stricte des environnements, audit d'accès) est celui inscrit dans `card-root.md` par le
PM et décliné par l'Architecte dans le brief (phase 2) puis dans `architect-map.md`
(section Sécurité) — ce skill ne le redéfinit pas, il donne le pattern de code pour le
respecter.

## Pattern d'implémentation attendu

### 1. Aucun secret en dur dans le code

```python
# Interdit
API_KEY = "sk-abc123..."

# Attendu : lu depuis l'environnement (ou le client du gestionnaire de secrets retenu)
API_KEY = os.environ["EXTERNAL_API_KEY"]
```

Aucune exception pour les environnements de développement : une valeur de test reste
hors du repo, fournie via variable d'environnement locale ou fichier ignoré par Git.

### 2. `.env.example` versionné, `.env` jamais versionné

```
.env.example   # committé : liste les clés attendues, valeurs vides ou factices
.env           # jamais committé, dans .gitignore dès la création du projet
```

`.env.example` documente la forme attendue (`EXTERNAL_API_KEY=`), jamais une valeur
réelle même partielle ou expirée.

### 3. Chargement via une interface d'abstraction

Si le niveau retenu en phase 1 impose un outil tiers de gestion de secrets (SOPS+age,
Infisical, ou autre — choix documenté dans `architect-map.md`, hors périmètre de ce
skill), le code métier ne référence jamais directement le SDK de cet outil dispersé
dans plusieurs fichiers. Une interface d'abstraction unique (ex : fonction
`get_secret(name: str) -> str`) centralise l'accès, pour que changer d'outil reste un
changement localisé.

### 4. Séparation des environnements

Si la contrainte de phase 1 impose une séparation stricte dev/staging/prod, les
secrets de chaque environnement vivent dans des emplacements distincts du
gestionnaire retenu (namespaces, projets ou vaults séparés) — jamais un seul jeu de
secrets partagé entre environnements avec une variable qui bascule le comportement.

### 5. Messages d'erreur et logs

Un secret ne doit jamais apparaître dans un message d'erreur, une stack trace, ou un
log applicatif, même partiellement (voir aussi `skills/logging-conventions.md` et
`skills/error-handling.md` pour la gestion générale des erreurs). Une exception levée
lors du chargement d'un secret manquant nomme la clé attendue, jamais sa valeur.

## Ce que ce skill NE couvre PAS

- Le choix concret de l'outil tiers de gestion de secrets pour un projet donné : décision
  prise au cas par cas par l'Architecte, documentée dans `architect-map.md` du projet
  (voir ADR 0012).
- Le contenu précis d'une politique de conformité légale (RGPD, sectorielle) : identifié
  par le PM en phase 1, hors périmètre technique de ce skill.
- Le chiffrement au repos de la base de données applicative (dimension distincte,
  dépendant de l'infrastructure du projet).

## Checklist pour l'Architecte

Quand une fiche de run touche à un projet portant une contrainte de sécurité en phase 1
(`card-root.md`), vérifier en phase 5 (audit des stubs) et en audit aval (phase 9) :

- [ ] Aucun secret en dur n'apparaît dans le code ou les stubs
- [ ] `.env.example` est présent et versionné, `.env` est dans `.gitignore`
- [ ] Si un outil tiers est requis, l'accès passe par une interface d'abstraction unique
- [ ] La séparation dev/staging/prod est respectée si elle a été exigée en phase 1
- [ ] Aucun secret n'apparaît dans un message d'erreur ou un log
- [ ] `architect-map.md` (section Sécurité) documente l'outil retenu et sa justification
