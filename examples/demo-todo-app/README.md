# demo-todo-app

Projet exemple pour valider et démontrer devaimazing.

**Ce dossier ne contient pas le code du projet.** Le vrai dépôt git (FastAPI + React,
scaffoldé et fonctionnel, testé le 2026-07-10) vit à `~/code/aimazing/demo-todo-app/`
(hors du dépôt devaimazing — voir ADR sur les projets cibles). Configuration devaimazing
correspondante : `config/projects/demo-todo-app.yml`.

Application todo list simple (FastAPI + React) utilisée pour :
- Tester le pipeline LangGraph complet
- Démontrer devaimazing à des recruteurs ou contributeurs
- Benchmarker les modèles Ollama

## État du scaffold (2026-07-10)

- Backend : `GET /todos`, `POST /todos`, `GET /todos/{id}`. SQLite local.
- Frontend : Vite + React + TypeScript, liste et création de todos.
- 4 tests unitaires backend (`pytest`, tous verts).
- Volontairement absent : `PATCH /todos/{id}/complete` (backend) et le bouton
  correspondant (frontend) — c'est l'objectif du run de démonstration ci-dessous.

## Lancer le run de démonstration

```bash
devaimazing run demo-todo-app
```

## Objectif du run d'exemple

"Ajouter un endpoint PATCH /todos/{id}/complete qui marque une todo comme terminée,
avec test unitaire et test d'intégration."

C'est volontairement simple pour que le run complet tourne en moins de 15 minutes
et soit facilement reproductible.
