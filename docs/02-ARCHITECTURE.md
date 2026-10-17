# Architecture

Ce document décrit la structure du code, les choix techniques fondateurs,
et les principes qui guident l'organisation du projet. Il est destiné à
toute personne qui va modifier le code ou évaluer sa qualité structurelle.

## Stack technique

### Backend

| Composant | Technologie | Version |
|---|---|---|
| Runtime | Python | 3.11 |
| Framework HTTP | FastAPI | 0.115 |
| ORM | SQLAlchemy | 2.0 |
| Validation données | Pydantic | 2.9 |
| Base de données | PostgreSQL (Neon, scale-to-zero) | géré |
| Authentification | JWT (python-jose) + bcrypt | — |
| Rate limiting | SlowAPI | 0.1.9 |
| HTTP client sortant | httpx (async) | 0.27 |
| Monitoring | Sentry SDK | 2.18 |
| Hébergement | Render (auto-deploy via GitHub) | — |

### Frontend

| Composant | Technologie | Notes |
|---|---|---|
| Markup | HTML5 statique | Pages indépendantes, pas de SPA |
| Styles | Tailwind CSS via CDN | Pas de build, pas de PostCSS |
| Comportement | JavaScript vanilla | Pas de framework, pas de transpilation |
| Stockage local | localStorage | Token JWT, refresh token, user |
| Monitoring | Sentry browser via CDN | Chargé conditionnellement |
| Hébergement | Render (statique) | Même service que le backend |

### Intégrations externes

| Service | Usage | Critique |
|---|---|---|
| NGSER | Agrégateur Orange Money (paiement entrant et retrait sortant) | Oui |
| Sentry | Capture des erreurs backend et frontend | Non |
| GitHub Actions | Cron de réconciliation paiements toutes les 5 minutes | Oui |
| Neon | Hébergement PostgreSQL managé | Oui |

## Pourquoi cette stack

Trois choix techniques structurants méritent une justification car un
lecteur extérieur pourrait les remettre en cause par habitude.

**FastAPI plutôt que Django ou Flask.** FastAPI offre la validation Pydantic
native, la génération automatique de l'OpenAPI consultable sur `/docs`, et
le support asynchrone qui est utilisé pour les appels sortants vers NGSER
(`async def initiate_ngser_payment`, voir `api/payments.py`). Le code reste
néanmoins majoritairement synchrone — l'asynchronisme est utilisé uniquement
là où il apporte une valeur réelle, pas systématiquement.

**Pas de framework frontend (React, Vue, Svelte).** Ce choix réduit la
complexité du déploiement (pas de build), permet à un développeur de lire
n'importe quelle page sans connaître un framework spécifique, et garde le
projet maintenable par une équipe restreinte. La contrepartie est connue :
les pages riches en interaction comme `organisation_dashboard.html`
deviennent volumineuses (plusieurs milliers de lignes). Ce compromis est
acceptable tant que l'équipe technique reste petite.

**Pas d'ORM migrations type Alembic.** Les migrations de schéma sont
gérées par un mécanisme idempotent simple dans `core/migrations.py` qui
exécute des `ALTER TABLE ... IF NOT EXISTS` au démarrage du backend. Ce
choix évite la complexité d'Alembic pour un projet à équipe restreinte et
à fréquence de changement de schéma modérée. Voir `03-DATA_MODEL.md`
pour le détail du mécanisme.

## Structure du code

Le projet suit une séparation par couche fonctionnelle. Chaque dossier a
une responsabilité unique.

```
goevent/
├── main.py                  # Point d'entrée FastAPI, montage routeurs
├── requirements.txt         # Dépendances Python
├── seed.py                  # Script de peuplement de données de test
│
├── api/                     # Routeurs HTTP, un fichier par domaine
├── core/                    # Logique transverse partagée
├── models/                  # Définitions SQLAlchemy des tables
├── schemas/                 # Schémas Pydantic d'entrée et sortie
├── utils/                   # Fonctions pures utilitaires
├── middleware/              # Middlewares FastAPI
│
├── web/                     # Frontend statique
│   ├── *.html               # Pages
│   ├── bt-api.js            # Client API et fonctions partagées
│   ├── auth-guard.js        # Garde d'authentification client
│   ├── pwa.js, sw.js        # Service worker PWA
│   └── manifest.json        # Manifeste PWA
│
├── docs/                    # Cette documentation
└── .github/workflows/       # GitHub Actions (cron réconciliation)
```

### Le dossier `api/`

Chaque fichier de ce dossier est un routeur FastAPI dédié à un domaine
métier. Aucune logique métier complexe ne doit y vivre — les routes valident
les entrées, appellent des fonctions de `core/` ou `utils/`, et retournent.

| Fichier | Responsabilité |
|---|---|
| `auth.py` | Inscription, connexion, refresh token, gestion du profil et du PIN |
| `events.py` | CRUD événements, recherche, catégories |
| `tickets.py` | Billet gratuit, scan, statistiques d'affluence |
| `payments.py` | Initiation paiement NGSER, webhook, déclenchement réconciliation |
| `organizer.py` | Demandes de retrait, statistiques, gestion des agents |
| `admin.py` | Validation retraits, gestion utilisateurs/events, logs sécurité |
| `staff.py` | Création et gestion des comptes staff internes |
| `agent.py` | Routes spécifiques aux agents de scan |
| `dashboard.py` | Agrégation de statistiques utilisateur |
| `social.py` | Favoris, follow d'organisateurs, feedbacks communautaires |
| `promo_codes.py` | CRUD codes promo (organisateur) et route publique de validation |
| `promotion.py` | Boost organisateur, packs Facebook Ads, marqueurs d'intérêt premium |
| `ads.py` | Régie publicitaire (contrats annonceurs, slides homepage) |
| `audience.py` | Export CSV des audiences segmentées (côté organisateur) |

### Le dossier `core/`

Logique partagée entre plusieurs routeurs. Les fichiers ici ne dépendent
d'aucun fichier de `api/` (sens du flux d'import strict).

| Fichier | Responsabilité |
|---|---|
| `config.py` | Variables d'environnement, paramètres applicatifs (Pydantic Settings) |
| `database.py` | Engine SQLAlchemy, session factory, base déclarative |
| `security.py` | Hash PIN, création/vérification JWT, dépendance `get_current_user`, `log_security_event` |
| `permissions.py` | RBAC centralisé : `get_current_admin`, `_or_staff`, `require_staff_function` |
| `limiter.py` | Configuration SlowAPI |
| `ngser.py` | Client NGSER : création session paiement, retrait, vérification statut |
| `payment_processing.py` | Traitement unifié des réponses NGSER (source unique webhook + cron) |
| `reconcile.py` | Cron de réconciliation des paiements en attente |
| `migrations.py` | Migrations de schéma idempotentes exécutées au démarrage |
| `email.py` | Envoi d'emails transactionnels (codes de réinitialisation) |

### Le dossier `models/`

Définitions SQLAlchemy des tables. Un fichier par domaine. Aucune logique
métier ici — uniquement les colonnes, contraintes, relations et index. La
liste des tables et leurs invariants est documentée dans `03-DATA_MODEL.md`.

### Le dossier `schemas/`

Schémas Pydantic v2 utilisés par les routeurs pour la validation des
entrées (`*Create`, `*Update`, `*In`) et la sérialisation des sorties
(`*Out`). Chaque fichier correspond généralement au routeur de même nom.

### Le dossier `utils/`

Fonctions pures utilitaires, sans état, sans dépendance à FastAPI.

| Fichier | Responsabilité |
|---|---|
| `locks.py` | `lock_event_for_booking`, `lock_event_for_release` — verrous pessimistes sur événement (anti-survente) |
| `promo.py` | `lock_promo_for_use`, `compute_discount`, `confirm_promo_usage`, `release_promo_usage` |
| `slugs.py` | Encodage réversible ID événement vers slug court via Hashids |
| `audience.py` | Helpers pour les segments d'audience exportables |
| `serializers.py` | Helpers de sérialisation transverses |

### Le dossier `middleware/`

Un seul middleware actuellement : `security_headers.py` qui ajoute les
en-têtes HTTP de sécurité standard (CSP, X-Frame-Options, etc.) sur toutes
les réponses.

## Flux de données principaux

Quatre flux structurent l'essentiel des interactions de la plateforme. Ils
sont décrits ici en synthèse et détaillés dans leurs documents spécifiques.

### Flux 1 : Inscription et authentification

L'utilisateur s'inscrit avec son numéro de téléphone et un code PIN à 4
chiffres. Le numéro de téléphone est l'identifiant unique de connexion.
L'email est optionnel mais conseillé pour la récupération de PIN. Après
inscription, un couple JWT (24h) + refresh token est retourné et stocké
côté client dans `localStorage`. Toutes les requêtes authentifiées
envoient le JWT dans l'en-tête `Authorization: Bearer <token>`. En cas
de réponse 401, le client tente automatiquement un refresh transparent
via la route `POST /auth/refresh` et rejoue la requête.

### Flux 2 : Achat d'un billet

Le fan sélectionne un événement, choisit une catégorie de billet (tier)
et le mode de paiement.

- **Paiement Orange Money** : `POST /payment/initiate-ngser` crée le
  ticket en statut `en_attente_om`, pré-réserve la place, appelle NGSER
  pour générer une URL de paiement, et retourne cette URL au client.
  Le fan complète le paiement sur l'interface NGSER. NGSER notifie
  ensuite GoEvent via le webhook `POST /payment/ngser-webhook` qui
  vérifie activement le statut auprès de NGSER avant de valider le
  ticket. Voir `04-PAYMENT_FLOW.md`.

- **Billet gratuit** (event gratuit ou promo -100 %) : `POST /tickets/reserve-free`
  crée directement le ticket en `paye` (méthode `gratuit`, référence
  `FREE-XXXXXXXX`). Aucun appel NGSER. Refuse tout billet dont le prix
  final est > 0. Le paiement cash à l'entrée est supprimé (oct. 2026).

### Flux 3 : Retrait organisateur

L'organisateur demande un retrait via `POST /organizer/payout-request`.
Le retrait reste en statut `en_attente` jusqu'à validation manuelle par un
administrateur via `POST /admin/payouts/{payout_id}/validate`, qui passe le
statut à `en_cours` et déclenche un appel NGSER B2C sortant. La
confirmation finale (statut `paye`) arrive via le même webhook que les
paiements entrants, qui route vers le bon traitement selon le préfixe de
l'order_id (`WD...`). Voir `04-PAYMENT_FLOW.md`.

### Flux 4 : Scan à l'entrée

L'agent ouvre la page `scanner.html` avec son compte agent, qui est associé
à un événement précis. La caméra du téléphone scanne le QR code du billet
et déclenche `POST /scan` qui vérifie l'authenticité, marque le ticket
comme entré, et retourne un feedback visuel et sonore. La route
`GET /tickets/scan/stats/{event_id}` est interrogée toutes les 30 secondes
pour afficher l'affluence temps réel.

## Principes transverses

Cinq principes guident l'évolution du code et doivent être respectés par
tout contributeur.

### Principe 1 : Source unique de vérité pour les opérations financières

Toute notification de paiement reçue d'un tiers (NGSER) est considérée
comme non fiable. Le statut réel d'une transaction est obtenu par appel
sortant authentifié vers NGSER (`check_payment_status`). Le payload du
webhook entrant ne sert qu'à déclencher la vérification, pas à statuer.

Cette règle est appliquée par `core/payment_processing.process_ngser_response`
qui est l'unique fonction autorisée à modifier le statut d'un ticket, d'un
boost ou d'un retrait suite à un événement de paiement. Elle est invoquée
à la fois par le webhook (`api/payments.py`) et par le cron de
réconciliation (`core/reconcile.py`).

### Principe 2 : Verrous pessimistes pour les ressources comptées

Toute mutation du compteur `seats_sold` d'un événement passe par
`utils.locks.lock_event_for_booking` ou `lock_event_for_release`, qui
acquièrent un verrou `SELECT ... FOR UPDATE` PostgreSQL. Aucun
incrément direct (`event.seats_sold += 1`) n'est autorisé en dehors de
ces fonctions. Le même principe s'applique aux compteurs d'usage des
codes promo via `utils.promo.lock_promo_for_use`.

Cette règle élimine les conditions de course entre acheteurs simultanés
sur les derniers billets disponibles ou les dernières utilisations d'un
code promo.

### Principe 3 : RBAC centralisé

Aucune vérification de rôle inline (`if user.role == "admin"`) n'est
autorisée dans les routes. Toute restriction d'accès passe par les
dépendances FastAPI définies dans `core/permissions.py`. Le matrice des
capacités par fonction staff (commercial, community manager, designer)
est définie dans `api/staff.py::_capabilities_for` et constitue la
référence unique des permissions internes.

### Principe 4 : Journalisation des actions sensibles

Toute action ayant un impact sur l'argent, l'identité, ou la modération
doit appeler `core.security.log_security_event` qui persiste l'action
dans une table dédiée. Cette journalisation est consultable par les
administrateurs via `GET /admin/security-logs` et alimente les audits
internes.

### Principe 5 : Erreurs HTTP sémantiques

Les codes d'erreur HTTP utilisés portent du sens et ne sont pas
interchangeables. Une ressource épuisée (places, code promo) retourne
`409 Conflict` avec un message explicite. Un accès non autorisé retourne
`403 Forbidden`. Une ressource introuvable retourne `404 Not Found`. Une
validation Pydantic en échec retourne `422 Unprocessable Entity` (géré
automatiquement par FastAPI). Le client (`bt-api.js`) propage ces codes
et messages dans son système de toasts.

## Cycle de vie d'une requête

Pour comprendre comment une requête HTTP traverse le système, prenons
l'exemple de `POST /payment/initiate-ngser`.

1. Le client envoie la requête avec un JWT dans l'en-tête Authorization
   et un body JSON correspondant au schéma `schemas.payment.PaymentConfirm`.
2. Le middleware `SecurityHeadersMiddleware` enrichit la réponse avec les
   en-têtes de sécurité (cette étape est passive sur les requêtes entrantes).
3. SlowAPI vérifie le rate limit applicable sur cette route.
4. FastAPI valide le body contre `PaymentConfirm`, retournant 422 si la
   validation échoue.
5. La dépendance `get_current_user` (de `core/security.py`) décode le JWT,
   charge l'utilisateur depuis la base, retourne 401 si invalide.
6. La fonction de route s'exécute : verrouille l'événement, vérifie les
   places disponibles, applique le code promo éventuel, crée le ticket et
   le payment record, incrémente `seats_sold`, commit. Puis appelle NGSER
   en sortie pour obtenir l'URL de paiement.
7. La réponse JSON est sérialisée et renvoyée au client.

Si une exception non gérée survient à toute étape, Sentry la capture
automatiquement (via `FastApiIntegration`), avec filtrage des données
sensibles (payload des routes `/auth/*` masqué, en-têtes Authorization
et Cookie retirés).

## Conventions d'imports

Le sens des dépendances est strictement descendant :

```
api/  →  core/  →  models/, schemas/, utils/
   ↘     ↘
    schemas/    models/
```

`core/` n'importe jamais depuis `api/`. `models/` n'importe jamais depuis
`schemas/` ni `core/`. Cette discipline garantit l'absence de cycles
d'imports et permet de tester les couches basses indépendamment.

## Ce qui est délibérément absent

Pour calibrer les attentes d'un lecteur extérieur, voici les éléments
courants dans une stack web moderne qui ne font pas (encore) partie de
GoEvent, par choix conscient :

- **Pas de cache Redis ou Memcached.** PostgreSQL Neon répond à la demande,
  le volume actuel ne le justifie pas. À reconsidérer au-delà de quelques
  centaines de requêtes par seconde.
- **Pas de queue de tâches (Celery, RQ).** Les seules opérations qui
  bénéficieraient d'une queue sont les envois d'emails. Actuellement
  synchrones, leur migration vers `BackgroundTasks` de FastAPI est
  identifiée comme amélioration post-lancement.
- **Pas de tests automatisés exhaustifs.** Quelques tests pytest critiques
  sur les flux paiement sont identifiés comme priorité immédiate
  post-lancement. La discipline actuelle repose sur la revue manuelle et la
  recette end-to-end.
- **Pas de CI/CD avancée.** Le déploiement repose sur l'auto-deploy Render
  au push sur la branche principale. Pas de pipeline de validation avant
  merge — la discipline de commit propre est assumée.

Ces absences sont des choix de stade, pas des oublis. Elles seront
rectifiées au fur et à mesure que la maturité opérationnelle et le volume
de l'équipe le justifieront.
