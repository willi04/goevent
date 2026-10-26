# Conventions de code

Ce document définit les règles de style, structure et sécurité que tout
contributeur au code de GoEvent doit respecter. Il est court par
construction : les conventions ne servent que si elles peuvent être
mémorisées.

Une convention n'est pas une préférence esthétique. C'est une contrainte
qui, si elle est violée, alourdit la maintenance, augmente la surface
de bugs, ou expose la plateforme à un risque. Le respect des conventions
est vérifié en revue de code, pas automatiquement — il n'y a pas de
linter obligatoire au moment de la rédaction de ce document.

## Style Python

Le code Python suit une variante allégée de PEP 8 avec les particularités
suivantes.

### Imports

Trois groupes séparés par une ligne vide : bibliothèque standard,
bibliothèques tierces, code du projet. À l'intérieur d'un groupe, ordre
alphabétique par module.

```python
import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.user import User
```

Imports depuis `core.*` avant `models.*`, `schemas.*`, puis `utils.*`
— reflète le sens des dépendances documenté dans `02-ARCHITECTURE.md`.

### Nommage

- Fonctions et variables : `snake_case`.
- Classes : `PascalCase`.
- Constantes : `SCREAMING_SNAKE_CASE`.
- Fonctions privées d'un module : préfixe `_` (`_process_ticket`,
  `_capabilities_for`).
- Routes de dépendance FastAPI (`get_current_user`,
  `get_current_admin`) : préfixe `get_`.
- Fonctions de verrou : préfixe `lock_` (`lock_event_for_booking`,
  `lock_promo_for_use`).

### Longueur de ligne

100 caractères souple. Pas de configuration formatage automatique
imposée. La lisibilité prime sur la conformité stricte à une limite.

### Docstrings

Docstring obligatoire sur les fonctions publiques de `core/*` et
`utils/*`. Optionnel sur les routes FastAPI dont la signature et le
schéma Pydantic parlent d'eux-mêmes.

Format libre en français, préférer les explications de « pourquoi » à
la paraphrase du « quoi » :

```python
def lock_event_for_booking(db, event_id):
    """
    Verrouille pessimistiquement l'événement (SELECT ... FOR UPDATE) et
    retourne l'objet chargé. À utiliser AVANT toute vérification de
    disponibilité et incrément de seats_sold. Le verrou est libéré à la
    fin de la transaction (commit ou rollback).
    """
```

### Commentaires

Les commentaires expliquent les décisions non-évidentes, pas ce que le
code fait littéralement. Un bon commentaire répond à « pourquoi » ou à
« attention », jamais à « quoi ».

Bon commentaire :

```python
# On incrémente seats_sold à l'intention, PAS à la confirmation, pour
# éliminer la survente en cas de simultanéité. Voir 04-PAYMENT_FLOW.md.
event.seats_sold += 1
```

Mauvais commentaire :

```python
# Incrémente seats_sold de 1
event.seats_sold += 1
```

## Structure des routes FastAPI

Une route suit toujours cette anatomie :

```python
@router.post("/route/path", response_model=SomeOut)
def route_name(
    data: SomeCreate,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
):
    # 1. Vérifications d'accès (si non couvertes par la dépendance)
    # 2. Vérifications métier (existence des ressources référencées)
    # 3. Verrous éventuels sur les ressources partagées
    # 4. Mutation via les fonctions utilitaires appropriées
    # 5. Journalisation sécurité si action sensible
    # 6. Retour Pydantic
```

### Propriété des ressources (scoping)

Le rôle ne suffit pas : **tout le monde peut s'inscrire `organizer`**.
Chaque route qui touche une ressource d'un événement vérifie la
propriété via une clé non éditable par l'utilisateur :

- Billet / scan / lecture QR : `_can_operate_event(user, ticket.event)`
  (`api/tickets.py`) — orga propriétaire (`event.organizer_id`) ou agent
  assigné (`agent_event_id`).
- Agents d'un orga : `_my_agents_query` (`api/organizer.py`) — agents
  dont `agent_event_id` ∈ events de l'orga.
- **Jamais `org_name`** comme critère d'autorisation : modifiable
  librement via `PUT /auth/me`.
- Ressource d'un autre propriétaire → 404 (pas 403) quand l'id est
  énumérable, pour ne pas servir d'oracle d'existence.

### Vérifications d'accès

Les vérifications de rôle sont **exclusivement** portées par les
dépendances de `core/permissions.py`. Aucun `if current.role == "admin"`
n'est autorisé dans une fonction de route. Pour restreindre à un rôle
précis, importer et utiliser la dépendance correspondante :

```python
# ✅ Correct
from core.permissions import get_current_admin

@router.get("/admin/global-stats")
def admin_stats(current: User = Depends(get_current_admin)):
    ...

# ❌ Incorrect
@router.get("/admin/global-stats")
def admin_stats(current: User = Depends(get_current_user)):
    if current.role != "admin":
        raise HTTPException(403)
    ...
```

Cette règle centralise les changements de politique de permission dans
un seul fichier, permet l'audit sécurité, et évite les incohérences
entre routes.

### Codes de statut HTTP

Le choix du code d'erreur porte du sens et est vérifié en revue :

| Code | Sens | Exemples |
|---|---|---|
| `400` | Requête malformée (au-delà de Pydantic) | PIN pas 4 chiffres, numéro déjà utilisé |
| `401` | Authentification manquante ou invalide | JWT expiré, token absent |
| `403` | Authentifié mais pas autorisé | Fan qui tente une route admin |
| `404` | Ressource introuvable | `event_id` inexistant |
| `409` | Conflit ou ressource épuisée | Places épuisées, code promo épuisé |
| `422` | Validation Pydantic en échec | Automatique, ne pas gérer manuellement |
| `500` | Erreur inattendue backend | Automatique, capturé par Sentry |

⚠️ **Erreurs à éviter** : renvoyer `400` pour tout, renvoyer `500` pour
une erreur métier prévisible, mélanger `403` et `404` (peut créer une
énumération des ressources).

### Message d'erreur

Le champ `detail` d'une `HTTPException` est affiché à l'utilisateur par
`bt-api.js`. Il doit donc être en français, précis, sans jargon
technique, et sans détails internes qui aideraient un attaquant.

```python
# ✅ Correct
raise HTTPException(409, "Ce code promo est épuisé")

# ❌ Incorrect (détails internes exposés)
raise HTTPException(500, "PromoUsage lock_promo_for_use returned None")

# ❌ Incorrect (jargon frontend)
raise HTTPException(400, "Missing tier_name field")
```

## Sécurité

Les règles suivantes portent sur la sécurité de la plateforme. Leur
violation crée une vulnérabilité exploitable, même en cas d'apparence
mineure.

### Journalisation des actions sensibles

Toute action qui touche à l'argent, à l'identité ou à la modération doit
être tracée via `log_security_event`. Cette fonction accepte le type
d'événement, l'utilisateur, la requête et un détail libre :

```python
from core.security import log_security_event

log_security_event(
    db=db,
    event_type="payout_validated",
    user_id=current.id,
    request=request,
    details=f"Payout {payout.id} validé pour {payout.amount} FCFA",
)
```

Types d'événements à journaliser systématiquement :

- Toute connexion (succès et échec).
- Toute modification de PIN ou d'email.
- Toute création, validation, modification de retrait.
- Toute création ou suppression de compte staff ou agent.
- Toute action de modération (bannissement, activation/désactivation).

### Ne jamais logger de secret

Les tokens JWT, refresh tokens, codes PIN (en clair), tokens NGSER, et
mots de passe hashés ne doivent jamais apparaître dans les logs
applicatifs, Sentry, ou `print()`. Le filtre `before_send` de Sentry
masque les payloads des routes `/auth/*` et retire les en-têtes
`Authorization` et `Cookie`, mais cette protection est passive — la
discipline reste sur le développeur.

### Validation côté serveur

Aucune validation exclusivement côté client n'est acceptable. Toute
règle métier (âge minimum, format de numéro, longueur de PIN, montant
positif) doit être vérifiée côté serveur, même si le frontend
l'implémente déjà pour l'UX. Le frontend peut être contourné trivialement,
le backend est la ligne de défense.

### Requêtes SQL

Toujours passer par l'ORM SQLAlchemy avec des paramètres. Jamais de
concaténation de chaînes pour construire une requête.

```python
# ✅ Correct
db.query(User).filter(User.email == user_email).first()

# ❌ Incorrect — injection SQL
db.execute(f"SELECT * FROM users WHERE email = '{user_email}'")
```

Les seules requêtes SQL en texte brut acceptées sont les migrations
idempotentes dans `core/migrations.py`, qui ne prennent aucun paramètre
externe.

### Rate limiting

Les routes sensibles doivent porter un décorateur de rate limit
SlowAPI. Les valeurs typiques déjà utilisées dans le code :

- `/auth/login` : `10/minute` par IP.
- `/auth/register` : `3/minute` par IP.
- `/auth/forgot-password` : `3/minute` par IP.
- `/payment/initiate-ngser` : `10/minute` par utilisateur authentifié.

Une nouvelle route sensible doit s'inspirer du rate limit d'une route
comparable existante, pas être laissée sans limite.

Choix de la clé (`core/limiter.py`) : en RCA, une IP = souvent des
centaines de personnes (CGNAT mobile, Wi-Fi d'une salle). Route
authentifiée à fort trafic → `key_func=user_key` ; page publique à jeton →
limite par jeton + `shared_limit(PUBLIC_IP_CEILING, scope="public_token_pages")`.
Jamais `request.client.host` directement : `core.client_ip.client_ip(request)`.

## Verrous et intégrité

Les règles suivantes portent sur les mutations de ressources partagées.
Les violer crée des incohérences visibles uniquement en cas de
simultanéité — donc rares en développement, systématiques en production.

### Compteurs `seats_sold`

Toute mutation passe par `utils.locks` :

- `lock_event_for_booking(db, event_id)` : à utiliser AVANT l'incrément
  de `seats_sold`. Verrouille et retourne l'événement, ou `None` si
  aucune place disponible.
- `lock_event_for_release(db, event_id)` : à utiliser AVANT le
  décrément. Verrouille et retourne l'événement, jamais `None`.

Ces fonctions acquièrent un verrou pessimiste PostgreSQL qui sera libéré
à la fin de la transaction en cours. Toute autre transaction qui tente
d'accéder au même événement pour un booking attendra la libération —
ce qui sérialise les accès concurrents sur le même événement sans
bloquer les événements différents.

### Compteurs `used_count` des codes promo

Même principe pour les codes promo :

- `lock_promo_for_use(db, code, event_id, user_id, ticket_id, ...)` :
  crée un `PromoUsage` en statut `reserved` après vérification des
  quotas sous verrou.
- `confirm_promo_usage(db, ticket_id)` : passe le `PromoUsage` en
  `confirmed` et incrémente `used_count` définitivement.
- `release_promo_usage(db, ticket_id)` : passe en `released`, aucun
  effet sur `used_count`.

### Transactions atomiques

Une mutation qui touche à plusieurs tables interdépendantes doit être
commit en une seule transaction. Ne jamais commit à la moitié d'une
opération métier, sinon un crash entre deux commits laisse le système
dans un état incohérent.

Cas typique : création d'un ticket + création du paiement + incrément
`seats_sold` doivent commit ensemble. Si NGSER échoue après ce commit,
le rollback n'est plus possible, mais l'état reste cohérent en attendant
le cron ou l'action manuelle.

## Style JavaScript et HTML

Les règles suivantes s'appliquent au frontend (`web/`).

### Modules JavaScript

Le fichier `web/bt-api.js` centralise les utilitaires partagés (client
API, toasts, formulaires, composants mobiles). Toute logique commune à
plusieurs pages doit y vivre plutôt qu'être dupliquée. Une fonction
utilisée par une seule page peut rester inline dans la page.

Les fonctions publiques exposées par `bt-api.js` sont en `camelCase`
préfixées `bt` (`btApi.post`, `btToast`, `btMobileBottomNav`) — le
préfixe signale l'origine et évite les collisions avec le code inline.

### Garde d'authentification

Les fonctions `requireAuth([roles])` et `requireGuest()` doivent
**toujours** être appelées après le chargement du DOM, jamais dans un
`<script>` inline en `<head>`. En `<head>`, le script s'exécute avant
que `bt-api.js` soit chargé (race condition réseau), et
`requireAuth` peut être `undefined`.

Pattern correct :

```html
<script>
document.addEventListener("DOMContentLoaded", () => {
    if (typeof requireAuth === "function") requireAuth(["organizer"]);
});
</script>
```

### Modification de la vue

Pas de framework réactif — les mutations DOM se font manuellement.
Convention : chaque section de vue a une fonction `renderX()` qui la
peuple à partir d'un état (variable JS), et cette fonction est appelée
à chaque changement d'état pertinent. Ne pas mélanger la mutation directe
du DOM et le re-render — choisir un pattern par page.

### Sécurité XSS

L'injection HTML via `innerHTML` est autorisée mais uniquement pour du
contenu contrôlé par le développeur (template) mêlé à des variables
échappées. Pour toute donnée provenant de l'API ou de l'utilisateur,
utiliser `textContent` ou une fonction d'échappement explicite. Un titre
d'événement mal échappé peut casser toute la page ou introduire du JS
malveillant si l'organisateur est hostile.

### Contenu éditable (CMS)

- Tout texte éditorial ajouté dans une page publique doit être balisé :
  relancer `python scripts/cms_tag.py` (idempotent, ne renumérote jamais une
  clé existante). Ne jamais réutiliser ni renommer une clé `data-cms` déjà en
  production : la surcharge correspondante serait perdue ou appliquée au
  mauvais endroit.
- Un élément dont le texte est écrit par le JS (id référencé dans un script)
  ne doit pas porter `data-cms` : le script de balisage les ignore.
- Valeurs CMS appliquées en `textContent` / `setAttribute` uniquement. Seuls
  `body_html` des pages légales et `answer_html` de la FAQ passent en
  `innerHTML`, après nettoyage bleach côté serveur (`api/cms.py`).
- Un montant ou un pourcentage commercial affiché (`… FCFA`) doit rester
  aligné sur le tarif réellement facturé (`core/config.py`) : ces champs sont
  réservés à l'admin.
- **Prix** : jamais écrit en dur dans une page. Mettre la valeur par défaut
  dans l'élément et `data-price="groupe.cle"` (format :
  `data-price-format="number|pct|month"`), la clé étant déclarée dans
  `core/pricing.py`. Côté API, tout montant facturé se lit via
  `get_pricing(db, fresh=True)`, jamais via une constante.
- **Emplacements** : une nouvelle zone = une entrée dans `core/zones.py` +
  un `<div data-zone="…" class="hidden">` dans la page. Ne jamais renommer
  une clé de zone en production (ses blocs deviendraient invisibles).
- **Événements privés** : toute nouvelle requête publique sur `Event`
  (liste, recherche, fiche, achat, promo, partage, recommandations) filtre
  `Event.visibility == "public"`. Les invités ne vont jamais dans `tickets`.
  Une donnée d'invité n'apparaît ni sur le pass (pas de téléphone), ni au
  scan (pas de téléphone), ni sur le lien de suivi (chiffres seulement).

### QR codes et bibliothèques tierces

- Un QR de billet (`qr_hash`) ou d'invitation **vaut droit d'entrée** : il
  est généré localement, jamais par un service tiers. Navigateur :
  `<img data-bt-qr="…">` (rendu automatique) ou `btQrDataUrl(texte, px)`
  pour un téléchargement (bt-api.js, bibliothèque `web/vendor/`). Email :
  `core.email.qr_png()` + image intégrée (`cid:`).
- Toute bibliothèque JS tierce exécutée sur une page sensible (scanner,
  billet, paiement) est **hébergée dans `web/vendor/`, version figée dans
  le nom du fichier** (ex. `html5-qrcode-2.3.8.min.js`), jamais chargée
  depuis un CDN sans version. Mise à jour = nouveau fichier + tests.

### Service worker (`web/sw.js`)

- Il n'intercepte **que des GET** : lectures publiques de l'API en liste
  blanche (`CACHEABLE_API`, sans en-tête `Authorization`), pages HTML
  (réseau d'abord, cache hors ligne), fichiers statiques. Une requête
  POST/PUT/DELETE (paiement, scan…) ne doit jamais passer par un délai ou
  un cache du service worker : un faux « hors ligne » pendant que le
  serveur traite entraîne un second clic, donc un double paiement.
- Incrémenter `APP_VERSION` à chaque modification du fichier ou de
  l'App Shell.

### Localisation

Toute chaîne visible à l'utilisateur est en français. Toute chaîne
technique (nom de variable, log console, commentaire) peut être en
français ou en anglais, avec préférence pour la langue de l'équipe
(français).

## Nommage des ressources externes

Certaines ressources externes ont un nom fixé par convention
transversale à respecter :

- Les URLs de partage d'événement suivent le format
  `https://goevent.africa/e/{hashid}?promo={code}` où `hashid` est
  généré par `utils.slugs.event_id_to_slug`.
- Les tokens NGSER dans les variables d'environnement suivent le
  format `NGSER_<PURPOSE>_TOKEN` (`NGSER_AUTH_TOKEN`,
  `NGSER_PAYMENT_TOKEN`, `NGSER_WITHDRAWAL_TOKEN`).
- Les préfixes d'order_id NGSER sont documentés dans
  `04-PAYMENT_FLOW.md` et ne doivent pas être renommés sans mise à
  jour coordonnée du dispatcher dans `core/payment_processing.py`.

## Anti-patterns à éviter

Les patterns suivants ont été identifiés dans le code au cours de son
histoire et sont explicitement à éviter dans les nouvelles
contributions.

### Vérification de rôle inline

Traité au-dessus dans « Vérifications d'accès ». Toute vérification de
rôle inline est une régression.

### Incrément direct de `seats_sold`

Traité au-dessus dans « Compteurs `seats_sold` ». Chercher un
`event.seats_sold +=` en dehors de `utils/locks.py` doit ne rien
retourner.

### Duplication de la logique de traitement NGSER

`core/payment_processing.py` est la source unique. Toute logique de
mise à jour de ticket, boost, pack FB ou payout suite à une réponse
NGSER doit y vivre. Copier-coller la logique dans une route est un
anti-pattern qui a déjà causé des bugs (voir historique du webhook
sécurisé).

### Retour de valeurs différentes selon les paramètres

Une fonction qui retourne parfois un dict et parfois une exception
selon un paramètre optionnel est difficile à raisonner. Préférer deux
fonctions distinctes ou un retour cohérent.

### Print pour du debug persistant

`print()` reste en production sur Render et pollue les logs. Utiliser
`print` avec parcimonie et uniquement pour des messages significatifs
(démarrage migration, traitement webhook, réconciliation cron). Pour
du debug ponctuel, retirer avant merge.

### Modification silencieuse de comportement

Un changement de comportement d'une fonction utilisée ailleurs doit
être documenté dans le commit et validé en revue. Si `_process_ticket`
change son critère d'idempotence, tous les appelants (webhook, cron,
tests) doivent être vérifiés.
