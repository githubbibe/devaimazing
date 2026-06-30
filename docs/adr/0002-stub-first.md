# ADR 0002 - Stub-first obligatoire

**Date** : 2026-06  
**Statut** : Accepté

## Contexte

Un agent LLM qui code peut produire du code fonctionnel mais incohérent avec les autres
agents (noms de fonctions différents, interfaces incompatibles, doublons non détectés).
Sans mécanisme de cadrage précoce, la dérive est silencieuse et coûteuse à corriger après.

## Décision

Avant toute implémentation, chaque agent codant (Back, Front) produit des fichiers
contenant uniquement :

- Signatures de fonctions/méthodes avec types complets
- Docstrings détaillées (objectif, paramètres, retours, exceptions levées)
- Dépendances et imports
- Side effects déclarés
- Exemples d'usage
- Schémas de données
- Contrats d'erreur (codes et messages)

Aucune logique métier n'est écrite à ce stade. L'Architecte valide les stubs
(cohérence inter-fichiers, respect des contraintes, absence de doublons) avant
que l'implémentation commence.

## Raisons

1. **Cadrage précoce de la dérive** : détecter une incohérence d'interface sur un stub
   coûte 0 token de refactoring. La détecter après implémentation coûte un run complet.

2. **Contrat inter-agents** : les stubs constituent le contrat explicite entre Back et Front.
   Front sait exactement quelles API Back exposera avant que Back les implémente.

3. **Lisibilité humaine** : un stub bien documenté est plus lisible qu'un code commenté.
   L'humain peut valider l'intent sans lire de l'implémentation.

4. **Détection de doublons** : l'Architecte peut comparer les stubs avec le `project-map.md`
   et détecter `utils/format_date.py` vs `helpers/date_formatter.py` avant qu'ils soient écrits.

## Conséquences

- Chaque run a une phase 4 (stub) et une phase 6 (implémentation) distinctes.
- La phase 5 (audit stubs) par l'Architecte est non-négociable, même en mode automatique.
- Les agents Back et Front doivent avoir un skill `stub-first.md` décrivant le format attendu.
- Un stub incomplet ou insuffisamment documenté est renvoyé à l'agent avec annotation.

## Format stub attendu (exemple Python)

```python
def format_date(date: datetime, locale: str = "fr_FR") -> str:
    """
    Formate une date selon la locale donnée.

    Args:
        date: La date à formater. Ne doit pas être None.
        locale: Code locale ISO. Supporte fr_FR, en_US, en_GB.

    Returns:
        Chaîne formatée selon la locale. Ex: "12 juin 2026" pour fr_FR.

    Raises:
        ValueError: Si locale non supportée.
        TypeError: Si date n'est pas un objet datetime.

    Side effects:
        Aucun. Fonction pure.

    Example:
        >>> format_date(datetime(2026, 6, 12), "fr_FR")
        "12 juin 2026"
    """
    ...  # Implementation à venir en phase 6
```
