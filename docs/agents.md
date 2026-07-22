# Agents devaimazing

## Vue d'ensemble

devaimazing orchestre 8 rôles d'agent spécialisés (6 identités Git distinctes —
Back-tu et Front-tu partagent celle de Back/Front, voir sections dédiées ci-dessous).
Chaque agent a un périmètre strict et un LLM assigné. Tous sont stateless sauf le PM.

**Principe auditeur/producteur** : un modèle ne peut pas auditer la dette qu'il a
lui-même produite. Les agents producteurs (Back, Front, Test) tournent sur Qwen 2.5 7B.
Les agents auditeurs (Architecte, Sécu) tournent sur Sonnet, qui domine Qwen en capacité.

---

## PM - Project Manager

**LLM** : Claude Code CLI (Opus pour cadrage, Sonnet pour raffinement)  
**Stateful** : oui (checkpointer SQLite LangGraph)  
**Identité Git** : `pm-aimazing <pm@aimazing.fr>`  
**Périmètre** : `specs/` (lecture/écriture), `project-map.md` (écriture)  

**Rôle** :
- Reçoit les objectifs de l'utilisateur (via la CLI, `devaimazing run <projet>`)
- Produit la fiche racine (phase 1, Opus)
- Définit la séquence des agents et écrit les fiches dépendantes (phase 3, Sonnet)
- Maintient le `project-map.md` et l'historique des runs (phase 10, Python pur)
- Gère les checkpoints de validation humaine
- Notifie l'utilisateur via ntfy à la fin de chaque run

**Skills** : `pm.md` (prompt système complet)

---

## Architecte

**LLM** : Claude Sonnet 4.6 (API Anthropic)  
**Stateful** : non  
**Identité Git** : `architect-aimazing <architect@aimazing.fr>`  
**Périmètre** : lecture transverse, écriture dans `docs/`, `specs/run-NNN/architect-*.md`  
**Pourquoi Sonnet** : audite le code produit par Qwen. L'auditeur doit dominer le producteur
(voir ARCHITECTURE.md principe 4).

**Rôle** :
- Audit non-fonctionnel amont : contraintes, carte fichiers, zones d'impact (phase 2)
- Audit des stubs : cohérence inter-fichiers, doublons, dérive (phase 5)
- Audit non-fonctionnel aval : conformité finale, factorisation (phase 9)
- Documentation complète : ADR, OpenAPI, README, CHANGELOG, runbooks (phase 9)

**Skills** : `architect.md`, `documentation.md`, `factorization.md`, `retry-patterns.md`,
`logging-conventions.md`, `error-handling.md`

---

## Back

**LLM** : Ollama, Qwen 2.5 7B Instruct  
**Stateful** : non  
**Identité Git** : `back-aimazing <back@aimazing.fr>`  
**Périmètre** : `/backend/` (création et modification)  

**Rôle** :
- Stub-first : signatures, types, docstrings, contrats (phase 4)
- Implémentation du code backend selon les stubs validés (phase 6)

**Skills** : `backend.md`, `stub-first.md`, `error-handling.md`, `logging-conventions.md`,
`retry-patterns.md`

---

## Back-tu (Test Unitaire Backend)

**LLM** : Ollama, Qwen 2.5 7B Instruct  
**Stateful** : non  
**Identité Git** : `back-aimazing <back@aimazing.fr>` (même identité que Back)  
**Périmètre** : `/tests/unit/backend/`  

**Rôle** :
- Écriture des tests unitaires pour le code Back (phase 6, après Back)
- Input : stubs validés + implémentation Back

**Skills** : `backend.md`, `stub-first.md`, `non-regression.md`

---

## Front

**LLM** : Ollama, Qwen 2.5 7B Instruct  
**Stateful** : non  
**Identité Git** : `front-aimazing <front@aimazing.fr>`  
**Périmètre** : `/frontend/` (création et modification)  

**Rôle** :
- Stub-first : composants, interfaces, contrats (phase 4)
- Implémentation du code frontend selon les stubs validés (phase 6)

**Skills** : `frontend.md`, `stub-first.md`, `error-handling.md`, `logging-conventions.md`

---

## Front-tu (Test Unitaire Frontend)

**LLM** : Ollama, Qwen 2.5 7B Instruct  
**Stateful** : non  
**Identité Git** : `front-aimazing <front@aimazing.fr>` (même identité que Front)  
**Périmètre** : `/tests/unit/frontend/`  

**Rôle** :
- Écriture des tests unitaires pour le code Front (phase 6, après Front)
- Input : stubs validés + implémentation Front

**Skills** : `frontend.md`, `stub-first.md`, `non-regression.md`

---

## Test

**LLM** : Ollama, Qwen 2.5 7B Instruct  
**Stateful** : non  
**Identité Git** : `test-aimazing <test@aimazing.fr>`  
**Périmètre** : `/tests/integration/`, `/tests/e2e/`, lecture transverse  

**Rôle** :
- Tests d'intégration (interactions Back/Front) (phase 7)
- Tests de non-régression sur les zones identifiées par l'Architecte (phase 7)

**Skills** : `test.md`, `non-regression.md`

---

## Sécu

**LLM** : Claude Sonnet 4.6 (API Anthropic) + SAST déterministe (Semgrep, Bandit)  
**Stateful** : non  
**Identité Git** : `security-aimazing <security@aimazing.fr>`  
**Périmètre** : lecture transverse, écriture dans `specs/run-NNN/security-report.md`  
**Pourquoi Sonnet** : audite le code produit par Qwen. L'auditeur doit dominer le producteur
(voir ARCHITECTURE.md principe 4).

**Rôle** :
- Couche 1 : SAST déterministe lancé par le runtime, zéro token (phase 8)
- Couche 2 : audit Sonnet sur ce que le SAST ne couvre pas — logique métier, cohérence globale (phase 8)
- Production du rapport de sécurité

**Skills** : `security.md`, `error-handling.md`

---

## Règles de périmètre

Un agent ne modifie jamais un fichier hors de son périmètre déclaré dans sa fiche.
L'Architecte vérifie ce respect en phase 5 (stubs) et phase 9 (audit aval) en
comparant les diffs avec les périmètres déclarés dans les fiches.

Si un agent modifie un fichier hors périmètre, sa contribution est rejetée et sa
fiche est annotée avec l'écart constaté.
