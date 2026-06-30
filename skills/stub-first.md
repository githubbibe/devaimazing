# Skill - Stub-first

## Objectif

Le stub-first est la pratique de créer les fichiers avec uniquement les contrats
(signatures, types, docstrings) avant toute implémentation.
Il cadre la dérive inter-agents et permet la validation architecturale précoce.

## Format obligatoire d'un stub Python

```python
from typing import Optional
# Tous les imports nécessaires à l'implémentation future

def nom_fonction(param1: Type1, param2: Type2 = valeur_defaut) -> TypeRetour:
    """
    Description concise de ce que fait la fonction.

    Args:
        param1: Description du paramètre. Contraintes éventuelles (non None, > 0, etc.)
        param2: Description. Valeur par défaut et son sens.

    Returns:
        Description de ce qui est retourné. Format si complexe.

    Raises:
        ValueError: Si <condition précise>. Message : "<format du message>".
        TypeError: Si <condition précise>.
        <MonErreurCustom>: Si <condition précise>.

    Side effects:
        - Aucun / ou liste des side effects (DB, fichiers, réseau, cache)

    Example:
        >>> nom_fonction(valeur1, valeur2)
        resultat_attendu

    Notes:
        Contraintes d'implémentation importantes à respecter.
        Patterns à utiliser (voir skill retry-patterns.md si appel réseau).
    """
    ...
```

## Format obligatoire d'un stub TypeScript/React

```typescript
interface NomPropsComponent {
  prop1: Type1;
  prop2?: Type2; // optionnel, défaut: valeur
}

/**
 * Description du composant.
 *
 * @param props.prop1 - Description
 * @param props.prop2 - Description. Défaut: valeur.
 *
 * @example
 * <NomComponent prop1={valeur} />
 *
 * @remarks
 * Contraintes d'implémentation. États à gérer (loading, error, success).
 * Appels API consommés (référence aux stubs Back validés).
 */
export const NomComponent: React.FC<NomPropsComponent> = ({ prop1, prop2 }) => {
  return null; // Implémentation à venir en phase 6
};
```

## Ce qu'un stub NE contient PAS

- Aucune logique métier
- Aucun `if`, `for`, `while`, `switch`
- Aucun appel de fonction métier
- Aucun accès à une base de données
- Aucun appel réseau

## Checklist avant de soumettre les stubs

- [ ] Toutes les fonctions/méthodes publiques ont une docstring complète
- [ ] Tous les types sont déclarés (pas de `Any` sans justification)
- [ ] Toutes les exceptions levées sont listées dans `Raises`
- [ ] Les side effects sont déclarés
- [ ] Au moins un exemple d'usage par fonction
- [ ] Les schémas de données complexes sont décrits
- [ ] Les contrats d'erreur (codes et messages) sont précisés
