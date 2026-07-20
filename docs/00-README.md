# Documentation technique GoEvent

Plateforme de billetterie événementielle et régie publicitaire pour le marché
africain francophone. Backend FastAPI + PostgreSQL, frontend HTML/Tailwind/JS
vanilla, paiement mobile money via NGSER (Orange Money).

## À qui s'adresse cette documentation

Cette documentation décrit l'architecture, les flux et les conventions du
système GoEvent. Elle est destinée à toute personne devant comprendre,
maintenir, étendre ou évaluer le code de la plateforme :

- Développeurs rejoignant l'équipe technique
- Partenaires techniques évaluant une intégration
- Auditeurs réalisant une revue de code ou de sécurité

Le ton est technique et explicatif. Les décisions de design sont accompagnées
de leur justification — pas pour défendre les choix, mais pour permettre au
lecteur de comprendre les contraintes et d'éviter de remettre en cause des
invariants par méconnaissance.

## Comment naviguer cette documentation

Les fichiers sont numérotés dans l'ordre de lecture recommandé pour une
première découverte. Chaque fichier traite d'une dimension distincte et peut
ensuite être consulté indépendamment.

| Fichier | Contenu | Lire si... |
|---|---|---|
| `01-OVERVIEW.md` | Produit, audience cible, modèle économique | Vous découvrez GoEvent |
| `02-ARCHITECTURE.md` | Stack, structure du code, choix techniques | Vous allez modifier du code |
| `03-DATA_MODEL.md` | Modèle BD, relations, invariants | Vous touchez aux données |
| `04-PAYMENT_FLOW.md` | NGSER, webhook, cron, locks pessimistes | Vous touchez aux paiements |
| `05-CONVENTIONS.md` | Règles de code, sécurité, anti-patterns | Vous écrivez du code |
| `06-SETUP_LOCAL.md` | Faire tourner le projet en local | Vous démarrez |
| `07-OPERATIONS.md` | Déploiement, monitoring, incidents | Vous gérez la production |

## Ce que cette documentation ne couvre pas

- L'état d'avancement des chantiers en cours et la roadmap produit, qui
  vivent dans un fichier interne `TECH_Core.md` non versionné. Cette doc
  décrit le système tel qu'il est, pas ce qu'il deviendra.
- Les secrets, clés API et identifiants. Toujours dans les variables
  d'environnement, jamais dans le code ni dans la doc.
- Les détails commerciaux (CA, marges réelles, contrats orgas pilotes),
  qui restent confidentiels.

## Conventions de cette documentation

- Les chemins de fichiers sont absolus depuis la racine du projet
  (`api/payments.py`, pas `payments.py`).
- Les noms de routes API sont préfixés par leur méthode HTTP
  (`POST /payment/initiate-ngser`).
- Les blocs de code montrent l'état actuel du système. Si un comportement
  est en cours de migration, il est explicitement signalé.
- Les avertissements signalés ⚠️ portent sur des invariants à respecter —
  les ignorer peut casser la production ou la cohérence des données.

## Maintenance de cette documentation

Cette documentation est versionnée avec le code dans le dossier `docs/`.
Toute modification structurelle du système (nouveau flux, nouveau modèle,
changement de convention) doit s'accompagner d'une mise à jour de la
documentation dans la même pull request.

Une documentation qui se désynchronise du code devient un piège pour le
prochain lecteur. La règle est simple : si le code change et que la doc ne
reflète plus la réalité, c'est la doc qui est cassée — pas le code.
