# Skill - Scalabilité et isolation des effets de bord

## Principe

Toute fonctionnalité doit tenir face à une augmentation raisonnable du volume de
données ou de charge, sans dégradation en cascade des autres fonctionnalités. Quand
ce n'est pas le cas, la limite doit être connue, mesurée et documentée dans
l'architect-map (`Dette assumée / Justification`) — jamais un angle mort silencieux.
Voir ADR 0010.

## Pagination obligatoire

Toute liste potentiellement non bornée est paginée dès sa première version, pas ajoutée
a posteriori quand la lenteur apparaît en prod.

```python
async def list_items(limit: int = 50, cursor: str | None = None) -> Page[Item]:
    ...
```

- Jamais de `SELECT *` sans `LIMIT` sur une table qui grossit avec l'usage.
- Le défaut de `limit` est explicite et documenté, pas laissé au hasard du driver.

## Requêtes N+1

Toute boucle qui déclenche une requête (DB, API externe) par itération est un signal
d'alerte. Charger en lot (`WHERE id IN (...)`, `JOIN`, ou requête agrégée) avant de
boucler sur les objets Python.

```python
# À éviter
for order in orders:
    order.customer = get_customer(order.customer_id)

# Attendu
customer_ids = {o.customer_id for o in orders}
customers = get_customers_by_ids(customer_ids)
```

## Bornage mémoire

Tout traitement sur une collection dont la taille dépend de l'usage (pas du code) doit
soit être borné explicitement (limite haute + erreur claire si dépassée), soit être
traité en flux (streaming / génération paresseuse) plutôt que chargé intégralement en
mémoire.

## Isolation des effets de bord entre features

Une fonctionnalité ne doit pas dégrader les performances d'une autre fonctionnalité qui
partage la même ressource (base de données, file, cache). Patterns attendus :
- Requêtes lourdes isolées dans leur propre chemin (pas de jointure large sur le chemin
  critique d'une autre feature).
- Files d'attente ou jobs asynchrones pour tout traitement non nécessaire à la réponse
  immédiate.
- Index dédiés plutôt que requêtes qui scannent une table entière partagée par plusieurs
  features.

## Dette assumée : ce qui est légitime

La scalabilité n'est pas un absolu à maximiser partout — c'est une dimension à choisir
consciemment, comme les 3 autres piliers (ADR 0010). Une dette est légitime si elle est
mesurée et écrite, par exemple :
- Ressources CPU/mémoire limitées (déploiement local, Mac mini) : le volume attendu est
  documenté, avec le seuil au-delà duquel la dette devient bloquante.
- Un traitement synchrone accepté tant que le volume reste sous un seuil chiffré, avec un
  point de bascule identifié (ex. passage en file asynchrone au-delà de N requêtes/min).

Ce qui n'est pas légitime : une section « Scalabilité » vide dans l'architect-map sans
aucune de ces justifications.
