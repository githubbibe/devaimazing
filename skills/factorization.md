# Skill - Factorisation du code

## Objectif

Éviter la duplication de code et de fichiers entre les runs successifs.
Un doublon non détecté crée de la dette technique invisible.

## Ce que l'Architecte cherche

### Doublons de fichiers

Deux fichiers qui font la même chose avec des noms différents.
Exemples :
- `utils/format_date.py` et `helpers/date_formatter.py`
- `components/Button.tsx` et `ui/CustomButton.tsx`

Détection : compare avec `project-map.md` avant que les stubs soient écrits (phase 2)
et après que le code soit produit (phase 9).

### Doublons de fonctions

Même logique réécrite dans deux fichiers différents.
Exemples :
- Deux fonctions de validation d'email dans `backend/users.py` et `backend/auth.py`
- Même calcul de date dans deux composants frontend

Détection : phase 9, lecture transverse du code produit.

## Comment signaler un doublon

### En phase 2 (préventif)

Dans `architect-brief.md` :
```markdown
## Doublons potentiels détectés

- Le run demande la création de `utils/parse_date.py`.
  Attention : `helpers/date_utils.py` (run-002) remplit peut-être ce rôle.
  Recommandation : l'agent Back vérifie et réutilise si possible.
```

### En phase 9 (correctif)

Dans `specs/run-NNN/architect-brief.md`, section "Actions post-run" :
```markdown
## Factorisation à planifier

- `backend/users.py:validate_email` et `backend/auth.py:check_email_format`
  font la même chose. Créer un run dédié pour factoriser dans `utils/validators.py`.
```

Ne jamais modifier le code directement en phase 9 pour factoriser.
Crée une recommandation de run futur. La factorisation sera un run à part entière.
