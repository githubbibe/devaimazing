# ADR 0004 - Licence AGPL-3.0

**Date** : 2026-06  
**Statut** : Accepté

## Contexte

devaimazing est un projet public sur GitHub. Le choix de licence détermine ce que
les tiers peuvent faire avec le code, et protège le travail de l'auteur.

## Décision

GNU Affero General Public License v3.0 (AGPL-3.0).

## Raisons

1. **Protection contre l'appropriation** : personne ne peut prendre le code, le fermer
   et le revendre comme produit propriétaire. Toute version dérivée doit rester sous AGPL.

2. **Couverture SaaS** : contrairement à la GPL classique, l'AGPL couvre le cas où
   quelqu'un fait tourner le code en service réseau payant sans distribuer de binaire.
   C'est le risque principal pour un studio de développement IA.

3. **Copyright auteur préservé** : l'auteur conserve le copyright sur sa propre partie
   du code (droit moral français imprescriptible).

4. **Évolutivité vers modèle commercial** : si des contributeurs tiers rejoignent,
   un CLA (Contributor License Agreement) peut être mis en place pour permettre
   à l'auteur de relicencier commercialement. La transparence sur ce point est obligatoire.

## Conséquences

- Les contributions de tiers restent sous AGPL tant qu'aucun CLA n'est signé.
- Certaines entreprises bloquent l'AGPL en politique IT : friction d'adoption possible.
- Le README doit être transparent sur le modèle de licence et les implications pour les contributeurs.

## Alternatives rejetées

- **MIT / Apache 2.0** : permissives, permettent l'appropriation commerciale fermée. Refusé.
- **GPL v3** : ne couvre pas le cas SaaS. Insuffisant.
- **BSL / FSL** : non reconnues par l'OSI, friction supplémentaire, pas dans le sélecteur GitHub natif.
