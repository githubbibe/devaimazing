# Skill - Tests de non-régression

## Principe

Un test de non-régression vérifie qu'une modification n'a pas cassé un comportement
existant qui fonctionnait avant. C'est la protection contre la régression silencieuse.

## Quand l'écrire

L'Architecte identifie en phase 2 les zones d'impact. Pour chaque zone :

1. Vérifie si des tests existent déjà pour ce comportement.
2. Si oui : exécute-les et ajoute des tests complémentaires si la couverture est insuffisante.
3. Si non : écris les tests de non-régression avant que la modification soit commitée.

## Format d'un test de non-régression

```python
def test_<fonctionnalite_existante>_non_regresse_apres_<changement>():
    """
    Non-régression : vérifie que <comportement> fonctionne toujours
    après la modification de <zone_impactée> dans run-XXX.
    
    Zones d'impact référencées : architect-brief.md run-XXX, section "Zones d'impact".
    """
    # Arrange
    ...
    # Act
    ...
    # Assert
    ...
```

## En cas d'échec

Si un test de non-régression échoue :

1. Ne corrige PAS le test pour qu'il passe.
2. Ne corrige PAS le code sans validation humaine.
3. Annote ta fiche `feedback` avec :
   - Nom du test qui échoue
   - Output d'erreur complet
   - Hypothèse sur la cause (quelle modification a probablement cassé quoi)
4. Stoppe et notifie via le runtime (checkpoint forcé).
