# Flux de paiement

Ce document décrit les flux financiers de la plateforme : achat de billet
par Orange Money, achat de billet cash, événement gratuit, achat de
régie publicitaire, retrait organisateur. Il détaille le rôle du
fournisseur de paiement NGSER, le mécanisme de webhook, le cron de
réconciliation, et les invariants de sécurité qui empêchent la fraude et
la perte d'argent.

C'est le document le plus critique de la documentation. Modifier du code
qui touche à ces flux sans avoir compris ce document expose la
plateforme à des pertes financières réelles.

## Le fournisseur NGSER

NGSER est l'agrégateur mobile money utilisé par GoEvent pour toutes les
opérations Orange Money entrantes (paiements de fans) et sortantes
(retraits d'organisateurs). Il expose une API HTTP documentée qui gère
l'authentification par token, la création de sessions de paiement, la
vérification de statut et l'envoi de notifications post-paiement.

Le client NGSER est isolé dans `core/ngser.py` et expose quatre fonctions
asynchrones :

- `authenticate(order_id)` : obtient un token de session à partir des
  identifiants de l'application (variables d'environnement `NGSER_AUTH_TOKEN`
  et `NGSER_PAYMENT_TOKEN`).
- `create_payment_session(order_id, amount)` : crée une session de
  paiement entrant et retourne l'URL sur laquelle rediriger le fan pour
  qu'il complète le paiement.
- `create_withdrawal_session(order_id, amount, customer_phone)` : crée
  une session de retrait sortant vers le numéro Orange Money d'un
  organisateur.
- `check_payment_status(order_id)` : interroge NGSER pour obtenir le
  statut réel d'une transaction. C'est la seule source de vérité
  autorisée sur le statut d'un paiement.

## Codes de statut NGSER

Toute réponse NGSER contient un champ `code` dont l'interprétation est
uniforme sur toutes les opérations :

| Code | Signification |
|---|---|
| `"1"` | Transaction réussie |
| `"3"` | Transaction en attente (paiement pas encore complété par le fan) |
| `"0"`, `"2"`, `"4"`, `"5"`, `"6"` | Transaction échouée (rejet, timeout, montant insuffisant, annulation, autre erreur) |

Le champ `data.transaction_amount` accompagne les transactions réussies
et permet une vérification anti-fraude côté GoEvent (voir ci-dessous).

## Format des order_id

Les identifiants de transaction envoyés à NGSER suivent un préfixe qui
détermine leur type et permet le routage backend. Chaque type de flux
utilise son propre préfixe :

| Préfixe | Signification | Généré par |
|---|---|---|
| `EVT...` | Achat de billet | `api/payments.py::initiate_ngser_payment` |
| `WD{id}-{uuid}` | Retrait organisateur | `api/admin.py::validate_payout` |
| `BST-...` | Boost d'événement | `api/promotion.py::initiate_boost` |
| `FBP-...` | Pack Facebook Ads | `api/promotion.py::request_fb_package` |

Le routage vers le bon traitement se fait dans
`core/payment_processing.process_ngser_response` qui inspecte le préfixe
et dispatche vers `_process_ticket`, `_process_payout`, `_process_boost`
ou `_process_fbpack`.

## Le principe de la source unique de vérité

**Aucune notification de paiement reçue de NGSER n'est considérée
comme une source de vérité.** Cette règle est absolue et sans exception.

Le webhook `POST /payment/ngser-webhook` est une route publique, non
authentifiée. N'importe qui sur Internet peut envoyer une requête POST
à cette URL avec un payload forgé prétendant qu'un paiement a réussi.
Sans protection, un attaquant qui devine ou observe un `order_id`
pourrait déclencher la validation d'un billet non payé, ou la mise en
statut `paye` d'un retrait avant que NGSER n'ait effectivement viré
l'argent.

Le mécanisme de protection est simple et systématique : lorsqu'une
notification arrive, le backend extrait uniquement le champ `order_id`
du payload entrant, puis interroge activement NGSER via
`check_payment_status(order_id)` pour obtenir le statut réel. Le
`code` et le `transaction_amount` utilisés pour statuer viennent
exclusivement de cette réponse sortante, jamais du payload entrant.

```
                    ┌─────────────────────────────────┐
                    │  Attaquant ou NGSER légitime    │
                    │  POST /payment/ngser-webhook    │
                    │  { order_id, code, amount, ... }│
                    └───────────────┬─────────────────┘
                                    │
                                    ▼
                    ┌─────────────────────────────────┐
                    │  api/payments.py                │
                    │  Extrait UNIQUEMENT order_id    │
                    │  Ignore code et amount entrants │
                    └───────────────┬─────────────────┘
                                    │
                                    ▼
                    ┌─────────────────────────────────┐
                    │  check_payment_status(order_id) │
                    │  → appel HTTPS sortant vers NGSER│
                    └───────────────┬─────────────────┘
                                    │
                                    ▼
                    ┌─────────────────────────────────┐
                    │  Réponse authentifiée NGSER     │
                    │  { code, data.transaction_amount}│
                    │  → SOURCE DE VÉRITÉ             │
                    └───────────────┬─────────────────┘
                                    │
                                    ▼
                    ┌─────────────────────────────────┐
                    │  process_ngser_response(...)    │
                    │  Applique le statut réel        │
                    └─────────────────────────────────┘
```

Cette architecture élimine le risque de fraude par payload forgé, au
prix d'un appel HTTP supplémentaire par notification. La latence
additionnelle (quelques centaines de ms typiques) est acceptable car
le fan ne dépend pas de cette réponse — il attend déjà chez NGSER.

Le corollaire de cette règle : `process_ngser_response` accepte en
paramètre le `code` et le `transaction_amount` déjà vérifiés. Cette
fonction n'appelle jamais NGSER elle-même — c'est la responsabilité de
son appelant (webhook ou cron) de les obtenir. Cela permet à la même
fonction de servir les deux entrées sans dépendance circulaire.

## Flux 1 : Achat d'un billet Orange Money

Le fan a choisi un événement, une catégorie de billet, et cliqué sur
« Payer par Orange Money ».

### Phase 1 : initiation

Le frontend appelle `POST /payment/initiate-ngser` avec le corps :

```json
{
  "event_id": 42,
  "tier_name": "Standard",
  "email": "fan@example.com",
  "promo_code": "NOEL2026"
}
```

Le backend (`api/payments.py::initiate_ngser_payment`) :

1. Charge et verrouille l'événement via `lock_event_for_booking`
   (`SELECT ... FOR UPDATE`). Rejette avec 409 si aucune place
   disponible.
2. Détermine le prix : soit `event.price` (tier par défaut), soit le
   prix du tier sélectionné dans `event.ticket_tiers`.
3. Si un code promo est fourni : verrouille le code via
   `lock_promo_for_use`, applique la réduction via `compute_discount`.
   Rejette avec 409 si le code est épuisé, invalide, ou si la
   réduction rend le montant nul (voir cas particulier ci-dessous).
4. Calcule la commission (4% pour l'acheteur, 7% pour l'organisateur),
   décompose le montant total.
5. Génère un `order_id` `EVT-<uuid>`.
6. Crée l'enregistrement `Payment` (statut `pending`) et le `Ticket`
   (statut `en_attente_om`, `payment_ref = order_id`).
7. Incrémente `event.seats_sold` de 1, commit atomique.
8. Appelle NGSER `create_payment_session(order_id, amount_total)` pour
   obtenir l'URL de paiement.
9. Si `PromoUsage` a été créé, il est en statut `reserved`.
10. Retourne l'URL au frontend, qui redirige le fan.

⚠️ **Invariant** : l'incrémentation de `seats_sold` se fait à
l'initiation, PAS à la confirmation du paiement. Ce choix élimine la
survente en cas de simultanéité — si dix fans tentent d'acheter le
dernier billet en même temps, seul le premier passe le verrou et
incrémente `seats_sold`, les neuf autres reçoivent 409.

### Phase 2 : le fan paie chez NGSER

Le fan complète le paiement sur l'interface hébergée par NGSER. Cette
étape est complètement en dehors du contrôle de GoEvent. Elle peut :

- Aboutir immédiatement (paiement validé côté opérateur).
- Aboutir avec délai (validation asynchrone opérateur, jusqu'à plusieurs
  minutes).
- Échouer (annulation utilisateur, solde insuffisant, timeout).
- Être abandonnée (fan ferme la fenêtre).

### Phase 3 : notification par webhook

Une fois le paiement traité, NGSER envoie une notification POST à
`/payment/ngser-webhook`. Le backend applique la logique de source
unique de vérité décrite plus haut :

1. Extrait `order_id` du payload entrant, ignore tout le reste.
2. Appelle `check_payment_status(order_id)` pour obtenir le vrai statut.
3. Passe le résultat à `process_ngser_response`, qui dispatche selon le
   préfixe. Pour `EVT-...`, appelle `_process_ticket`.
4. `_process_ticket` vérifie l'idempotence (skip si statut n'est plus
   `en_attente_om`), vérifie le montant reçu vs le montant attendu
   (rejet si écart > 1 FCFA), et applique le statut final :
   - `code == "1"` → `Ticket.payment_status = "paye"`, `Payment.status =
     "completed"`, `PromoUsage.status = "confirmed"` (si applicable).
   - `code ∈ {"0","2","4","5","6"}` → `Ticket.payment_status =
     "echec_paiement"`, `PromoUsage.status = "released"`, `seats_sold`
     décrémenté via `lock_event_for_release`.
   - `code == "3"` → aucun changement, on attend le cron.

### Phase 4 : filet de sécurité par cron

Le webhook peut se perdre : panne réseau côté NGSER, erreur backend,
timeout Render. Sans filet, un ticket resterait figé en `en_attente_om`
même si le paiement a réussi. Le cron de réconciliation
(`core/reconcile.py`) tourne toutes les 5 minutes via GitHub Actions
et rattrape ces cas.

Le cron sélectionne les tickets encore en `en_attente_om` dans une
fenêtre temporelle précise :

- **Plus vieux que 15 minutes** (`GRACE_MINUTES`) : laisse le temps au
  webhook de faire son travail dans le cas normal. Interroger NGSER
  immédiatement serait à la fois inutile (le paiement est peut-être
  encore en attente côté opérateur) et coûteux en volume d'appels.
- **Plus jeune que 24 heures** (`MAX_AGE_HOURS`) : au-delà, le paiement
  est considéré comme abandonné et sera traité par la fonction
  `expire_old_orphans`, appelée en fin de `POST /admin/reconcile`
  (avant oct. 2026 elle n'était appelée nulle part : places perdues à vie).
  Elle fait un **dernier `check_payment_status`** avant de libérer :
  code `1` → billet validé (paiement tardif), code `""` ou erreur réseau
  → ticket laissé intact (pas de libération sans preuve), autres codes
  (y compris `3`) → `expire_abandonne` + place libérée. Lots de 100.

Pour chaque ticket dans la fenêtre, le cron appelle
`check_payment_status(payment_ref)` puis `process_ngser_response`. Le
résultat est le même que celui du webhook, la logique de mise à jour
étant partagée. C'est la mise en application du principe de source unique
décrit dans `02-ARCHITECTURE.md`.

### Cas particulier : promo qui ramène le prix à zéro

NGSER ne peut pas traiter une transaction de montant nul. Si un code
promo valide ramène le prix d'un billet à zéro, `initiate_ngser_payment`
rejette avec 409 en invitant le fan à choisir le paiement cash. La
réservation cash à zéro passe par `POST /tickets/reserve-cash` qui
détecte le prix nul et crée directement un ticket en statut `paye`
avec méthode `gratuit`.

Cette garde est appliquée sur le prix brut de l'événement pour éviter
un contournement du contrôle de deadline : un événement payant à J-24h
reste bloqué même si un code promo local le rendrait gratuit.

## Flux 2 : Réservation cash à l'entrée

Le fan a choisi « Payer en espèces à l'entrée ». Aucun appel à NGSER.

Le frontend appelle `POST /tickets/reserve-cash`. Le backend :

1. Verrouille l'événement, vérifie disponibilité.
2. Applique un code promo éventuel comme en flux 1.
3. Crée le `Ticket` avec `payment_status = "reservation_cash"`,
   `payment_method = "cash"`, `reservation_expires_at = now + 24h`.
4. Incrémente `seats_sold` de 1.
5. Retourne les détails du ticket au fan (avec QR code).

Le jour de l'événement, l'organisateur ou un agent encaisse le
paiement et appelle `POST /tickets/{ticket_id}/confirm-cash-payment`
qui passe le statut à `paye_cash`, renseigne `cash_collected_by`,
`cash_collected_at`, `cash_amount`.

⚠️ **Non implémenté** : aucune expiration automatique des réservations
cash (`reservation_expires_at` est posé mais jamais lu). Une réservation
jamais honorée garde sa place jusqu'à l'événement. Décision produit
attendue (cf. TECH_Core P1) : la deadline est-elle « réserver avant
J-1 » ou « payer avant J-1 » ?

## Flux 3 : Événement gratuit

L'événement a `price = 0` et pas de tier payant. Aucun appel à NGSER.

Le frontend appelle `POST /tickets/reserve-cash` (même route que le
cash, à ne pas confondre — la sémantique interne diverge). Le backend
détecte le prix nul et :

1. Verrouille l'événement, vérifie disponibilité.
2. Crée le `Ticket` directement en `payment_status = "paye"`,
   `payment_method = "gratuit"`, `payment_ref = "FREE-XXXXXXXX"` (UUID
   court).
3. Incrémente `seats_sold` de 1.
4. Retourne le ticket au fan.

Pas de phase de confirmation, pas de cron. Le ticket est valable
immédiatement pour l'entrée.

⚠️ **Invariant** : la deadline de réservation habituelle (bloquée à
J-24h pour éviter les fraudes de dernière minute) est relâchée pour
les événements gratuits, mais uniquement sur la base du `event.price`
brut. Un événement à `price = 5000` avec un code promo qui le rend
gratuit reste soumis à la deadline. Cette garde est essentielle pour
éviter le contournement du contrôle par manipulation de codes promo.

## Flux 4 : Retrait organisateur

L'organisateur veut récupérer les fonds accumulés sur ses ventes.

### Phase 1 : demande par l'organisateur

`POST /organizer/payout-request` crée un `PayoutRequest` avec
`status = "en_attente"`, `amount` plafonné par la **cagnotte retirable**
(`_cagnotte_retirable`) : somme des `Payment.organizer_amount` complétés
**hors cash et gratuit**. Le cash est déjà dans la poche de l'orga ;
l'inclure revenait à le lui reverser une 2e fois par OM. À ce stade,
aucun appel NGSER. Un changement de `phone_number` (numéro de
destination des fonds) est validé `+236XXXXXXXX` et tracé
(`payout_phone_changed`).

### Phase 2 : validation par un administrateur

Un administrateur consulte la liste via `GET /admin/payouts` et
valide manuellement chaque demande via `POST /admin/payouts/{payout_id}/validate`.
Ce point de contrôle humain est intentionnel — un retrait est une
sortie d'argent qui doit rester sous supervision.

Le backend (`api/admin.py::validate_payout`) :

1. Génère `withdrawal_id = "WD{payout.id}-{uuid_court}"`.
2. Persiste dans la même transaction : `payout.status = "en_cours"`
   ET `payout.payment_ref = withdrawal_id`. Cette atomicité garantit
   l'invariant : un retrait en cours a toujours un `payment_ref`
   exploitable par le cron.
3. Récupère le numéro Orange Money de destination (soit
   `user.orange_money`, soit `user.phone_number` en fallback).
4. Appelle NGSER `create_withdrawal_session(withdrawal_id, amount,
   phone)` qui déclenche le transfert sortant.
5. Issue de l'appel :
   - HTTP 4xx (refus explicite, aucun argent sorti) → `en_attente`,
     relance admin possible (`payout_rejected`).
   - Timeout / erreur réseau / 5xx / réponse illisible → **reste
     `en_cours`** (`payout_ambiguous`). Le cron tranchera via
     `check_payment_status`. Remettre `en_attente` permettait un 2e clic
     admin = nouveau `WD…` = **double virement**.
   - Succès → `paye` (`payout_paid`).

### Phase 3 : confirmation par webhook ou cron

Même mécanisme que pour un achat, mais le préfixe `WD` du order_id
route vers `_process_payout` au lieu de `_process_ticket`. La logique
est similaire :

- `code == "1"` avec montant cohérent → `payout.status = "paye"`.
- `code == "1"` avec montant incohérent → statut inchangé, log
  d'alerte pour investigation admin. Aucune mise à jour automatique
  du statut n'est faite dans ce cas — un écart de montant sur un
  retrait est un signal suspect qui doit rester tracé plutôt qu'être
  résolu automatiquement dans un sens ou l'autre.
- `code ∈ {"0","2"}` (rejet / solde insuffisant) → `payout.status = "en_attente"`.
- `code ∈ {"4","5","6"}` (inexistant / timeout / inconnu) → **reste
  `en_cours`**, action `ambiguous`, investigation humaine. Au-delà de
  24 h après `processed_at` le cron ne le réinterroge plus : un
  `en_cours` ancien est une alerte admin, pas un état normal.
- `code == "3"` → aucun changement.

## Flux 5 : Achat de régie publicitaire

Les organisateurs peuvent acheter deux types de régie via NGSER :

- **Boost d'événement** (`api/promotion.py::initiate_boost`) : met en
  avant leur événement dans les listes pendant N jours. Order_id
  préfixé `BST-`.
- **Pack Facebook Ads** (`api/promotion.py::request_fb_package`) :
  campagne Facebook déléguée à GoEvent. Order_id préfixé `FBP-`.

Le flux d'achat est structurellement identique à celui d'un billet :
initiation avec création de session NGSER, redirection du fan chez
NGSER, notification par webhook, réconciliation par cron. La logique
métier de mise à jour (activation du boost, lancement de la campagne
FB) est portée par `_process_boost` et `_process_fbpack` dans
`core/payment_processing.py`.

## Table synoptique des mécanismes anti-perte

Le tableau ci-dessous résume, pour chaque risque identifié, le mécanisme
qui le protège.

| Risque | Mécanisme de protection | Localisation |
|---|---|---|
| Survente sur les derniers billets | Verrou pessimiste `SELECT ... FOR UPDATE` sur l'événement | `utils/locks.py` |
| Double-utilisation d'un code promo | Verrou pessimiste sur `promo_codes` + contrainte unique `(promo_id, ticket_id)` | `utils/promo.py`, `models/promo.py` |
| Payload webhook forgé (fraude billet) | Ignore payload, vérifie via `check_payment_status` | `api/payments.py::ngser_webhook` |
| Payload webhook forgé (fraude retrait) | Même mécanisme, dispatch par préfixe | `core/payment_processing.py::_process_payout` |
| Webhook perdu (billet) | Cron 5min sur tickets en `en_attente_om` | `core/reconcile.py` |
| Webhook perdu (retrait) | Cron 5min sur payouts en `en_cours` avec `payment_ref` non-nul | `core/reconcile.py` |
| Cron non exécuté | GitHub Actions cron `*/5 * * * *`, secret `RECONCILE_SECRET` en double vérification | `.github/workflows/`, `api/payments.py::admin_reconcile` |
| Checkout OM abandonné >24h | `expire_old_orphans` (check final NGSER puis libération), appelé par `/admin/reconcile` | `core/reconcile.py` |
| Incohérence entre montant demandé et payé | Vérification `abs(recu - attendu) > 1` FCFA, rejet | `core/payment_processing.py` |
| Contournement deadline via promo gratuite | Contrôle sur `event.price` brut, pas sur prix après promo | `api/payments.py` |
| Décrémentation multiple d'une même place | Idempotence par statut : skip si déjà traité | `core/payment_processing.py` |

## Ce qu'il ne faut jamais faire

Les erreurs suivantes, si elles étaient introduites dans le code par un
développeur qui n'aurait pas lu ce document, exposeraient la plateforme
à des pertes financières. Elles sont listées ici pour rendre les
interdictions explicites plutôt qu'implicites.

- Ne jamais faire confiance au `status_id` ou `transaction_amount`
  d'un payload webhook entrant. Toujours passer par
  `check_payment_status`.
- Ne jamais modifier `event.seats_sold` en dehors de
  `lock_event_for_booking` et `lock_event_for_release`.
- Ne jamais valider un ticket en statut `en_attente_om` sans avoir
  vérifié son statut auprès de NGSER.
- Ne jamais initier un paiement NGSER de montant nul, même sur un
  chemin non nominal.
- Ne jamais court-circuiter le point de contrôle humain sur les
  retraits organisateur en automatisant la transition
  `en_attente → en_cours` sur `POST /organizer/payout-request`.
- Ne jamais dupliquer la logique de `_process_ticket`, `_process_payout`,
  `_process_boost`, `_process_fbpack` ailleurs dans le code. Si un
  nouveau chemin d'appel apparaît, il doit passer par
  `process_ngser_response` pour rester cohérent.

## Environnements et clés

Deux environnements sont configurés :

- **Sandbox** : `NGSER_BASE_URL` pointe vers l'environnement de test de
  NGSER, `ENV=sandbox`. Tous les paiements sont fictifs. C'est
  l'environnement de développement et de recette.
- **Production** : `NGSER_BASE_URL` pointe vers l'environnement réel,
  `ENV=production`. Tout paiement est un vrai transfert d'argent.

Le passage en production nécessite la réception des clés de production
NGSER (`NGSER_AUTH_TOKEN`, `NGSER_PAYMENT_TOKEN`,
`NGSER_WITHDRAWAL_TOKEN`, `NGSER_SERVICE_NAME`) et leur configuration
dans les variables d'environnement Render. La procédure complète de
bascule est documentée dans `07-OPERATIONS.md`.
