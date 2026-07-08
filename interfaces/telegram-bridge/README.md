# interfaces/telegram-bridge

Configuration des skills OpenClaw utilisés comme passerelle Telegram pour piloter
devaimazing depuis mobile (voir README, section « Vision »).

## Statut

Stub — dossier créé pour que la structure annoncée dans le README corresponde au
filesystem. Le contenu (skills OpenClaw, mapping des commandes Telegram vers les
runs LangGraph, format des notifications) n'est pas encore implémenté.

## Ce qui est attendu ici (à terme)

- Skills OpenClaw déclarant les commandes Telegram exposées (lancer un run, valider
  un checkpoint, consulter le statut).
- Mapping entre les messages Telegram reçus et les actions déclenchées côté
  `runtime/studio` (voir `runtime/studio/tools/`).
- Format des notifications sortantes, cohérent avec `notifications.no_link_in_message`
  défini dans `config/studio.yml` (message auto-suffisant, pas de lien).

Voir `docs/agents.md` pour le rôle d'OpenClaw/Telegram dans la réception (phase 0)
et la notification de fin de run.
