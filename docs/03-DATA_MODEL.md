# Modèle de données

Ce document décrit le schéma PostgreSQL de GoEvent, les relations entre
tables, et les invariants que le système maintient à tout prix. Un
invariant est une règle que le code garantit implicitement par ses
mécanismes (verrous, séquence d'opérations, contraintes de base). Le
violer par méconnaissance conduit à des incohérences difficiles à
détecter et à corriger.

## Organisation des modèles

Les modèles SQLAlchemy vivent dans `models/`, un fichier par domaine
métier. La table `users` centralise l'identité de tous les rôles (fan,
organizer, staff, agent, admin) — il n'y a pas de tables séparées par
rôle, la distinction se fait via la colonne `role`.

Le mécanisme de migration idempotent (voir `02-ARCHITECTURE.md`) permet
d'ajouter des colonnes à des tables existantes sans intervention manuelle
sur la base : les instructions `ALTER TABLE ... IF NOT EXISTS` sont
définies dans `core/migrations.py` et exécutées au démarrage du backend.

## Vue d'ensemble des tables

| Table | Fichier modèle | Rôle |
|---|---|---|
| `users` | `user.py` | Identité unique de tous les rôles |
| `security_logs` | `user.py` | Journalisation des actions sensibles |
| `password_resets` | `user.py` | Codes temporaires de réinitialisation PIN |
| `events` | `event.py` | Événements publiés par les organisateurs |
| `tickets` | `ticket.py` | Billets réservés ou payés |
| `payments` | `payment.py` | Traces des paiements NGSER (audit) |
| `payout_requests` | `payment.py` | Demandes de retrait organisateur |
| `refund_requests` | `refund.py` | Demandes de remboursement fan |
| `promo_codes` | `promo.py` | Codes promotionnels créés par les organisateurs |
| `promo_usages` | `promo.py` | Traces d'utilisation des codes promo |
| `commissions` | `commission.py` | Commissions dues à l'équipe interne (post-lancement) |
| `boost_orders` | `promotion.py` | Boosts d'événements achetés par les organisateurs |
| `fb_ad_packages` | `promotion.py` | Packs Facebook Ads gérés pour les organisateurs |
| `homepage_content` | `cms.py` | Slides publicitaires et éditoriaux de la page d'accueil |
| `ad_contracts` | `cms.py` | Contrats publicitaires annonceurs |
| `fundraising_campaigns` | `cms.py` | Campagnes de collecte (réservé futur) |
| `favorites` | `social.py` | Événements mis en favori par les fans |
| `followers` | `social.py` | Fans suivant un organisateur |
| `feedbacks` | `social.py` | Retours utilisateurs libres |
| `partner_requests` | `partner.py` | Formulaires de contact partenaires |
| `cancellation_requests` | `partner.py` | Demandes d'annulation d'événement |
| `audience_export_logs` | `audience_log.py` | Traçabilité légale des exports CSV audience |
| `premium_interests` | `premium_interest.py` | Manifestations d'intérêt pour l'offre premium |

## Tables centrales

Les six tables suivantes concentrent la logique métier critique de la
plateforme. Elles sont documentées ici en détail — les autres tables
sont autoportantes et se lisent directement dans les fichiers modèles.

### `users`

Identité unique de tous les rôles. Le numéro de téléphone est
l'identifiant de connexion primaire.

Colonnes essentielles :

- `phone_number` (unique, indexé) : identifiant de connexion, format
  `+236XXXXXXXX` pour la RCA.
- `email` (nullable, index unique en `LOWER()`) : optionnel, utilisé
  pour la récupération de PIN. L'index unique sur la version en
  minuscules interdit les inscriptions avec la même adresse en casses
  différentes.
- `pin_hash` : code PIN à 4 chiffres haché via bcrypt.
- `role` : `"fan"`, `"organizer"`, `"agent"`, `"admin"`, ou une valeur
  correspondant à une fonction staff.
- `staff_function` : `"commercial"`, `"cm"`, `"designer"` — définit les
  capacités pour les comptes staff internes (voir
  `api/staff.py::_capabilities_for`).
- `orange_money` : numéro de réception des retraits pour les
  organisateurs, distinct du `phone_number` de connexion.
- `agent_event_id` : pour les comptes agent, événement auquel ils sont
  rattachés (accès scanner limité à cet event).
- `failed_login_attempts` et `locked_until` : verrouillage temporaire
  après 5 tentatives échouées.
- `two_factor_secret` et `is_2fa_enabled` : colonnes historiques d'une
  fonctionnalité 2FA retirée. Non utilisées. Suppression prévue.

⚠️ **Invariant** : le trio `role` + `staff_function` + `is_active`
détermine à lui seul l'accès aux routes du back-office. Toute
vérification de permission passe par `core/permissions.py`, jamais par
un test inline `if user.role == "admin"`.

### `events`

Événements publiés par les organisateurs.

Colonnes essentielles :

- `title`, `description`, `location`, `category`, `event_date` :
  attributs éditoriaux.
- `price` : prix de base d'un billet standard, en FCFA. Zéro pour un
  événement gratuit.
- `total_seats` : capacité totale.
- `seats_sold` : compteur des places réservées **ou** payées. Voir
  l'invariant critique ci-dessous.
- `ticket_tiers` : colonne JSONB contenant la liste des catégories de
  billets (nom, prix, quota). Format : `[{"name": "Standard", "price":
  5000, "quantity": 100}, ...]`. Si vide ou nulle, l'événement utilise
  uniquement la catégorie par défaut au prix `event.price`.
- `is_active` : contrôle la visibilité publique. Un événement inactif
  n'apparaît plus dans les listes mais ses billets restent valables.
- `cover_image_url` : image de couverture stockée en base64 directement
  dans la colonne (migration vers CDN Cloudinary prévue).

⚠️ **Invariant critique** : `seats_sold` représente les places
**réservées OU payées**. Le compteur est incrémenté dès l'intention
d'achat (initiation NGSER, réservation cash, réservation gratuite) et
n'est décrémenté qu'en cas d'échec explicite (webhook négatif, cron de
purge des réservations expirées). Il n'existe pas de distinction en
base entre places « bloquées temporairement » et places « effectivement
vendues » — la sémantique est unifiée pour éliminer les incohérences.

Les places restantes sont toujours calculées par `total_seats - seats_sold`,
sans distinction du statut des tickets sous-jacents.

⚠️ **Invariant critique** : toute mutation de `seats_sold` passe
**obligatoirement** par `utils.locks.lock_event_for_booking` (verrouille
et vérifie la disponibilité avant l'incrément) ou
`utils.locks.lock_event_for_release` (verrouille avant la
décrémentation). Aucun `event.seats_sold += 1` direct n'est autorisé
dans le code, sans exception.

### `tickets`

Billets réservés ou payés par les fans.

Colonnes essentielles :

- `qr_hash` (unique) : UUID généré à la création, sert de contenu du QR
  code scanné à l'entrée.
- `payment_status` : voir cycle de vie ci-dessous.
- `payment_ref` : référence de la transaction (`EVT-...` pour un paiement
  NGSER, `FREE-XXXXXXXX` pour un événement gratuit, vide pour du cash).
- `payment_method` : `"om"` (Orange Money), `"cash"`, `"gratuit"`.
- `is_used` et `used_at` : marqués lors du scan à l'entrée.
- `reservation_expires_at` : **legacy** (flux cash supprimé oct. 2026, plus écrit). Pour les réservations cash, deadline de
  confirmation par l'organisateur avant purge automatique.
- `cash_collected_by`, `cash_collected_at`, `cash_amount` : traçabilité
  du paiement cash à l'entrée par un agent ou l'organisateur. **Legacy** :
  plus alimentés depuis la suppression du flux cash (oct. 2026) ;
  `reservation_cash` → `annule_cash_supprime` via `scripts/`.

**Cycle de vie de `payment_status`** :

```
                    ┌─────────────────┐
                    │   Achat NGSER   │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐         ┌──────────────┐
                    │ en_attente_om   │────────▶│  paye        │
                    └────────┬────────┘  succès └──────────────┘
                             │
                             │ échec
                             ▼
                    ┌────────────────────────┐
                    │ echec_paiement         │
                    │ expire_abandonne       │
                    └────────────────────────┘

                    ┌─────────────────┐
                    │ Réservation cash│
                    └────────┬────────┘
                             │
                    ┌────────▼─────────┐        ┌──────────────┐
                    │ reservation_cash │───────▶│  paye_cash   │
                    └────────┬─────────┘confirm └──────────────┘
                             │
                             │ expire (>24h)
                             ▼
                    ┌──────────────────┐
                    │  expire_cash     │
                    └──────────────────┘

                    ┌─────────────────┐
                    │  Événement      │
                    │  gratuit        │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │      paye       │  (méthode="gratuit", ref="FREE-...")
                    └─────────────────┘
```

⚠️ **Invariant** : seuls les tickets en statut `paye` ou `paye_cash`
sont valables pour l'entrée à un événement. Le scanner à l'entrée
vérifie ce statut avant marquage `is_used = True`.

### `payments`

Table d'audit des transactions NGSER. Un enregistrement est créé à
chaque initiation de paiement, distinct du ticket qu'il finance. Sert
principalement à la réconciliation comptable et à la répartition des
montants (frais plateforme, part organisateur).

Colonnes essentielles :

- `transaction_id` : order_id NGSER (`EVT...`), lien avec la
  notification webhook.
- `amount` : montant total payé par le fan (prix de base + 4% frais
  acheteur).
- `base_price`, `platform_fee`, `organizer_amount` : décomposition des
  11% de commission (voir `01-OVERVIEW.md`).
- `status` : `pending`, `completed`, `failed`.

### `payout_requests`

Demandes de retrait des organisateurs vers leur compte Orange Money.

Colonnes essentielles :

- `event_id`, `organizer_id`, `amount` : contexte du retrait.
- `status` : `en_attente` (créé par l'orga, à valider par admin),
  `en_cours` (validation admin faite, session NGSER B2C initiée),
  `paye` (webhook NGSER confirmé), retour à `en_attente` en cas
  d'échec pour permettre la relance.
- `payment_ref` : order_id NGSER (`WD{id}-{uuid}`) persisté au moment
  du déclenchement admin. Nécessaire au cron de réconciliation pour
  interroger NGSER en cas de perte du webhook.

⚠️ **Invariant** : `status = "en_cours"` implique `payment_ref` non-nul.
Les deux transitions sont commit dans la même transaction, garantissant
que le cron peut toujours retrouver l'order_id d'un retrait en cours.

### `promo_codes` et `promo_usages`

Codes promotionnels créés par les organisateurs et traces
d'utilisation.

`promo_codes` colonnes essentielles :

- `code` : chaîne alphanumérique jusqu'à 20 caractères. Un même code
  peut exister pour plusieurs organisateurs (pas de contrainte
  d'unicité globale) mais est unique par organisateur.
- `event_id` : nullable. Si nul, le code s'applique à tous les événements
  de l'organisateur. Sinon, restreint à un événement précis.
- `tier_name` : nullable. Si renseigné, le code ne s'applique qu'à une
  catégorie de billet précise de l'événement.
- `discount_type` : `"percent"` ou `"fixed"`.
- `discount_value` : valeur numérique interprétée selon `discount_type`.
- `max_uses_global` : plafond total d'utilisations, nullable pour
  illimité.
- `max_uses_per_user` : plafond par utilisateur, défaut 1.
- `used_count` : compteur d'utilisations effectives.
- `starts_at`, `ends_at` : fenêtre de validité, nullable pour permanent.
- `is_active` : désactivation manuelle par l'orga.

`promo_usages` colonnes essentielles :

- `promo_id`, `user_id`, `ticket_id` : liaison.
- `discount_fcfa` : montant effectif de la réduction appliquée.
- `status` : `"reserved"` (intention pendant l'attente de paiement
  NGSER), `"confirmed"` (paiement validé, décrémente définitivement),
  `"released"` (paiement échoué, place libérée sans consommer).
- Contrainte unique `(promo_id, ticket_id)` : un même code ne peut être
  utilisé qu'une fois par ticket.

⚠️ **Invariant** : toute intention d'utilisation d'un code promo passe
par `utils.promo.lock_promo_for_use` qui verrouille pessimistiquement la
ligne `promo_codes` correspondante, vérifie les quotas et crée le
`PromoUsage` en statut `"reserved"`. Les fonctions
`confirm_promo_usage` et `release_promo_usage` gèrent respectivement
la transition finale succès ou échec.

⚠️ **Invariant** : un `PromoUsage` en statut `"reserved"` qui reste
figé plus de 24h est un signal d'anomalie — soit un webhook perdu, soit
un cron qui n'a pas tourné. À surveiller en production.

## Tables secondaires notables

Les tables suivantes ne portent pas d'invariants critiques mais méritent
une note.

### `boost_orders` et `fb_ad_packages`

Achats de régie publicitaire par les organisateurs. Mêmes flux de
paiement que les tickets, avec préfixes d'order_id distincts (`BST-`
pour les boosts, `FBP-` pour les packs Facebook). Le statut suit un
cycle proche de celui des tickets : `en_attente_om` → `actif` (boost)
ou `en_attente_lancement` (pack FB).

### `commissions`

Table de suivi des commissions dues à l'équipe interne (commercial
notamment). Alimentée post-lancement. Les colonnes `beneficiary_user_id`,
`source_type`, `source_id` permettent de tracer chaque commission jusqu'à
sa source (paiement, retrait, contrat pub).

### `security_logs`

Journalisation des actions sensibles alimentée par la fonction
`core.security.log_security_event`. Consultable par les administrateurs
via `GET /admin/security-logs`. Événements typés par `event_type` :
`login_success`, `login_failed`, `password_reset_requested`,
`payout_validated`, etc.

### `audience_export_logs`

Traçabilité légale des exports CSV d'audience par les organisateurs
(RGPD, protection des données personnelles). Chaque export est
enregistré avec l'organisateur, l'événement source, le nombre de lignes,
l'IP, l'user-agent et les filtres appliqués. Ne pas modifier
rétroactivement.

### `site_contents`, `site_page_revisions`, `faq_items` (back-office CMS)

- `site_contents` : `key` (PK, ex. `index.004`, `global.contact.email`),
  `value`, `updated_at`, `updated_by`. Uniquement les **surcharges** : la
  valeur par défaut est le texte du HTML (registre : `core/cms_registry.py`).
  Images : data URL PNG/JPEG/WebP ≤ 1,5 Mo, servies par
  `GET /content/media/{key}?v=<timestamp>` (cache long). Une clé dont
  l'élément a disparu du HTML est ignorée (orpheline, sans effet).
- `site_page_revisions` : `slug` (`cgu`, `confidentialite`,
  `remboursements`), `title`, `body_html` (nettoyé), `note`, `created_at`,
  `created_by`. **Append-only** : la version en ligne est l'`id` max du slug,
  l'historique sert de preuve des versions successives des documents légaux.
- `faq_items` : `category` (`compte`, `achat`, `billet`, `orga`, `autre`),
  `question`, `answer_html` (nettoyé), `position`, `is_active`.

### `zone_blocks`, `site_settings` (emplacements et tarifs)

- `zone_blocks` : `zone` (clé de `core/zones.py`, indexée), `kind`
  (`image|banner|announcement|cards`), `title`, `body`, `image` (data URL
  ≤ 1,5 Mo, servie par `GET /zones/media/{id}?v=…`), `link_url`,
  `link_label`, `sponsor`, `theme` (`green|dark|light|gold`), `items` (JSON
  des cartes, 8 max), `position`, `is_active`, `starts_at`, `ends_at` (UTC
  naïf), `impressions`, `clicks`, `created_at`, `updated_at`, `updated_by`.
- `site_settings` : `key` (PK) → `value` (JSON). `zones` : réglages par zone
  (`empty`, `every`) ; `pricing` : tarifs surchargés (seules les clés connues
  de `core/pricing.DEFAULT_PRICING` sont lues).

## Relations principales

Les relations foreign key du système, visualisées :

```
users ──┬── events (organizer_id)
        ├── tickets (user_id)
        ├── promo_codes (organizer_id)
        ├── payout_requests (organizer_id)
        ├── favorites, followers, feedbacks
        └── ... (la plupart des tables ont un lien vers users)

events ─┬── tickets (event_id)
        ├── promo_codes (event_id, nullable)
        ├── payout_requests (event_id)
        ├── boost_orders (event_id)
        ├── fb_ad_packages (event_id)
        └── refund_requests (event_id, nullable)

tickets ─┬── promo_usages (ticket_id)
         └── refund_requests (ticket_id)

promo_codes ─── promo_usages (promo_id)
```

## Contraintes d'intégrité en base

Les contraintes SQL PostgreSQL suivantes sont maintenues au niveau de la
base et constituent la dernière ligne de défense contre les
incohérences :

- `users.phone_number` : unique, index.
- `users.email` : index unique sur `LOWER(email)` créé via migration
  idempotente (voir `core/migrations.py`). Empêche les collisions même
  en cas de saisie en casses différentes.
- `tickets.qr_hash` : unique.
- `promo_usages (promo_id, ticket_id)` : unique.
- `fb_ad_packages.utm_campaign` : unique.

## Choix de conception à connaître

Trois choix de modélisation méritent une justification explicite car ils
diffèrent des pratiques courantes.

**Pas de tables séparées par rôle utilisateur.** Fan, organisateur,
agent, staff et admin partagent la même table `users` avec un champ
`role`. Ce choix simplifie l'authentification (une seule requête) et
permet la migration facile d'un rôle à un autre (un fan qui devient
organisateur ne perd pas son historique). La contrepartie est que
certaines colonnes ne sont pertinentes que pour un rôle donné (par
exemple `agent_event_id` uniquement pour les agents).

**Images stockées en base64 dans la base.** Solution transitoire pour
éviter la dépendance à un CDN au démarrage. Compression côté frontend
imposée à moins de 1 Mo par image. Migration vers Cloudinary prévue
quand le volume d'images stockées le justifiera.

**`ticket_tiers` en JSONB au lieu d'une table dédiée.** Chaque événement
peut définir plusieurs catégories de billets (`name`, `price`,
`quantity`). Stocker cela dans une colonne JSONB de la table `events`
plutôt que dans une table `event_tiers` séparée réduit le nombre de
JOINs sur le chemin critique de l'affichage des événements. La
contrepartie est que la mise à jour d'un tier nécessite une lecture,
modification et écriture de tout le JSON — acceptable car les tiers
sont rarement modifiés après création d'un événement.

## Conventions de nommage

- Tables : snake_case pluriel (`payout_requests`, `promo_usages`).
- Colonnes : snake_case singulier.
- Foreign keys : `<table_référencée_singulier>_id` (`user_id`,
  `event_id`), sauf cas ambigus où le rôle est précisé
  (`organizer_id`, `cash_collected_by`).
- Statuts : chaînes en français, snake_case (`en_attente`, `paye`,
  `expire_abandonne`). Ce choix reflète la langue de travail de
  l'équipe et est cohérent sur toutes les tables.
- Timestamps : `created_at`, `updated_at`, `<verbe>_at`
  (`purchased_at`, `used_at`, `paid_at`).
- Booléens : préfixe `is_` (`is_active`, `is_used`).
