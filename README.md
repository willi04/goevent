# GoEvent Africa

Plateforme de billetterie en ligne conçue pour la République centrafricaine : création d’événements, vente de billets payés par Orange Money ou en cash, billets à QR code et contrôle d’accès le jour J.

**Statut : bêta, en pré-production.** La plateforme est déployée sur [goevent.africa](https://goevent.africa) et testée par un panel restreint d’utilisateurs. Les paiements Orange Money passent par l’environnement de **test** de l’agrégateur NGSER ; le passage en production dépend de la réception des clés d’API réelles et d’une recette complète des scénarios critiques.

## Fonctionnalités

- **Organisateurs** : création et publication d’événements, catégories de billets, codes promo, tableau de bord des ventes, demandes de retrait vers Orange Money.
- **Acheteurs** : catalogue d’événements, achat par Orange Money ou réservation payable en cash, billet à QR code, espace « Mes billets ».
- **Agents de scan** : validation des billets à l’entrée depuis une interface mobile.
- **Back-office** : modération, régie publicitaire (mise en avant d’événements), support.

## Architecture

| Couche | Technologies |
| --- | --- |
| API | Python 3.11, FastAPI, SQLAlchemy 2, Pydantic 2 |
| Base de données | PostgreSQL |
| Frontend | Pages HTML statiques, Tailwind CSS, JavaScript sans framework (PWA) |
| Paiement | Agrégateur NGSER (Orange Money), environnement de test |
| Hébergement & suivi | Render (API), Sentry (erreurs) |

```
api/          routes HTTP (auth, événements, billets, paiements, organisateur, admin…)
core/         configuration, base de données, sécurité, client NGSER, réconciliation
models/       modèles SQLAlchemy (tables)
schemas/      schémas Pydantic (validation des entrées/sorties)
utils/        verrous, codes promo, sérialisation
middleware/   en-têtes de sécurité
web/          frontend statique
docs/         documentation technique détaillée
```

## Points techniques notables

- **Fiabilité des paiements.** Un webhook NGSER ne fait que déclencher une vérification : le statut réel d’un paiement est toujours relu auprès de NGSER. Une **réconciliation automatique** (GitHub Actions, toutes les 5 minutes) reprend les paiements restés en attente et expire ceux qui sont orphelins depuis 24 heures.
- **Pas de survente.** Les places vendues et l’usage des codes promo sont modifiés sous verrou PostgreSQL (`SELECT … FOR UPDATE`), pour éviter les conflits entre acheteurs simultanés.
- **Sécurité.** Jetons JWT de courte durée, mots de passe hachés (bcrypt), double authentification, limitation du débit des requêtes, nettoyage des saisies, en-têtes de sécurité, contrôle d’accès par rôle centralisé.
- **Configuration validée au démarrage.** Si une variable critique manque, l’API refuse de démarrer plutôt que d’échouer au premier paiement.

## Documentation

| Document | Contenu |
| --- | --- |
| [Vue d’ensemble](docs/01-OVERVIEW.md) | Produit, rôles, modèle économique, état de la plateforme |
| [Architecture](docs/02-ARCHITECTURE.md) | Structure du code, flux principaux, principes transverses |
| [Modèle de données](docs/03-DATA_MODEL.md) | Tables, relations, contraintes d’intégrité |
| [Flux de paiement](docs/04-PAYMENT_FLOW.md) | Orange Money, cash, webhook, réconciliation, retraits |
| [Conventions](docs/05-CONVENTIONS.md) | Règles de code du projet |

## Lancer le projet en local

```bash
pip install -r requirements.txt
cp .env.example .env      # puis renseigner les variables ci-dessous
uvicorn main:app --reload
```

Variables obligatoires : `DATABASE_URL`, `SECRET_KEY`, `NGSER_AUTH_TOKEN`, `NGSER_PAYMENT_TOKEN`.
Variables utiles : `ENV` (`development` en local), `HASHIDS_SALT`, `RECONCILE_SECRET`, `SENDER_EMAIL`, `SENDER_PASSWORD`, `SENTRY_DSN_BACKEND`.

Le frontend (`web/`) est statique : il peut être servi par n’importe quel serveur local (port 5500 autorisé en développement).

## Analyse des données

Les données de la phase de test ont été nettoyées et analysées en SQL dans un dépôt dédié : [goevent-analytics-sql](https://github.com/willi04/goevent-analytics-sql) (qualité des données, table de faits, fonctions fenêtre, CTE).

## Limites et prochaines étapes

- Ajouter des tests automatisés, en priorité sur le flux de paiement.
- Passer en production avec les clés NGSER réelles, après recette complète.
- Préparer un entrepôt analytique (schéma en étoile) alimenté par la base de production.

## Auteur

**Emmanuel MADOUKOU**, licencié en Mathématiques-Informatique (Université de Bangui), futur Data Engineer.
[github.com/willi04](https://github.com/willi04)
