# Topologie infrastructure - devaimazing et écosystème *aimazing

Ce document décrit l'architecture réseau Podman actuelle (Mac mini M4 Pro) et la
trajectoire vers une architecture distribuée future.

---

## Contexte et principe de sécurité

La séparation historique entre sessions macOS (`bibe` admin / `dataimazing` daily
driver) répondait à une contrainte de Colima (VM à privilèges élevés). Podman en
mode rootless ne nécessite pas de session admin : tous les services tournent sous
une seule session macOS (`dataimazing`), avec l'isolation assurée au niveau des
réseaux Podman, pas au niveau des sessions utilisateur.

**Principe retenu** : une session macOS unique, plusieurs réseaux Podman étanches.
L'isolation entre espaces (dev du studio, prod du studio, prod des services produits)
se fait par réseau, pas par compte système.

---

## Les espaces

### 1. `services-prod-network`

Les services produits par devaimazing, en production réelle, servant de vrais
utilisateurs. Aujourd'hui : webaimazing v1.

Contenu : nginx, backend, frontend, redis, postgres (données réelles), prometheus prod.

### 2. `devaimazing-prod-network`

Le daemon devaimazing lui-même, tournant en permanence, orchestrant les runs de
développement pour les projets configurés.

Contenu : daemon LangGraph, SQLite (state + metrics), prometheus dev.

**Pas de PostgreSQL permanent dans ce réseau** : les tests d'intégration/E2E utilisent
un PostgreSQL éphémère livré avec chaque projet cloné (voir section Autonomie des
projets produits), pas une base partagée avec l'infrastructure du studio.

### 3. `preprod-network` (temporaire)

Environnement de validation avant bascule d'une nouvelle version en prod. Monté et
démonté à la demande (spin up / spin down), pas permanent.

Contenu au moment de son activation : backend + frontend de la version candidate,
PostgreSQL de test éphémère (données contrôlées, reproductibles, jetables).

### 4. Hors Podman

Les repos Git ne sont pas des containers : `devaimazing` (code du studio) et les
projets produits (`webaimazing-v2`, etc.) vivent comme repos sur le disque, clonés
sous `~/code/aimazing/`.

---

## Services mutualisés (multi-network)

Trois services sont partagés entre les réseaux pour éviter la duplication de
ressources coûteuses en RAM :

**Ollama** — une seule instance sert les modèles locaux (Qwen pour devaimazing,
modèles de webaimazing v1 si applicable). Accessible depuis `services-prod-network`
et `devaimazing-prod-network`.

**Grafana** — UI unique de visualisation, lit les datasources Prometheus prod et
dev, ainsi que Loki (voir ci-dessous). Aucune donnée sensible n'y transite en clair
au-delà de ce qui est déjà exposé par les métriques et logs pseudonymisés.

**Loki** — agrégation centralisée des logs de tous les espaces, alimentée par
Grafana Alloy (voir ci-dessous).

---

## Observabilité : Grafana Alloy + Loki

### Contexte

Promtail (agent historique de collecte de logs pour Loki) est **end-of-life depuis
le 2 mars 2026**. Le support commercial a cessé, aucune évolution future. Le
remplaçant officiel est **Grafana Alloy**, agent unifié qui collecte logs, métriques
et traces (compatible OpenTelemetry).

### Rôle de Loki dans l'architecture

Loki remplace le besoin d'un outil de diagnostic actif connecté à la production.
Les logs JSON structurés (déjà en place dans webaimazing) sont collectés en continu ;
un incident se diagnostique en rejouant la séquence depuis les logs centralisés,
sans jamais connecter un environnement de test à la production réelle.

### Déploiement d'Alloy

Un agent Alloy par service (pas par réseau), configuré pour pousser ses données
vers Loki et Prometheus via une adresse résolvable en DNS. Ce choix anticipe la
trajectoire vers une architecture distribuée (voir section suivante) : quand un
service migre vers un autre serveur, seule la configuration DNS d'Alloy change,
l'architecture de collecte reste identique.

---

## Autonomie des projets produits par devaimazing

Un projet produit par devaimazing (par exemple webaimazing-v2) doit être **autonome
à la livraison** : un tiers qui clone le repo doit pouvoir lancer les tests et faire
tourner le projet sans dépendre de l'infrastructure du studio.

Conséquence : chaque projet embarque dans son propre compose Podman un PostgreSQL
de test, indépendant de toute infrastructure partagée. C'est ce PostgreSQL qui
alimente `preprod-network` au moment de son activation, et non un service partagé
appartenant à devaimazing.

---

## Gouvernance des données comportementales

Voir ADR 0009 pour le détail complet. Résumé opérationnel :

- Toute donnée comportementale exportée vers Loki est pseudonymisée par construction.
- La table de correspondance pseudonyme ↔ identité est étanche (réseau et accès
  applicatif restreints à l'admin).
- Le cloneur d'un projet choisit `analytics_mode: local` (rien n'est exporté,
  tout reste dans son PostgreSQL local) ou `analytics_mode: shared` (données
  pseudonymisées remontées vers un Loki mutualisé).

---

## Trajectoire future : architecture distribuée

Vision à moyen terme : chaque service (au sens micro-service) tourne potentiellement
sur un serveur distinct, avec son propre DNS, sa propre localisation géographique.
Les services mutualisés à faible consommation de ressources (Loki, Grafana, et
potentiellement Ollama selon les besoins de latence) restent centralisés ; les
services à charge variable ou à isoler fortement peuvent être déployés indépendamment.

Ce que les choix actuels préservent pour cette trajectoire :
- Alloy configuré par DNS dès maintenant (pas de dépendance à une adresse locale figée)
- Isolation par réseau déjà pensée comme équivalent local de l'isolation par serveur
- Loki et Grafana déjà conçus comme services mutualisés indépendants des services
  qu'ils observent

Ce qui reste à faire le moment venu (hors périmètre de cet ADR/document, à traiter
quand la migration devient concrète) : service discovery, gestion des secrets
distribuée, résilience réseau inter-serveurs.
