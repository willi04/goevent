# Vue d'ensemble

## Le produit en une phrase

GoEvent est une plateforme web de billetterie événementielle avec paiement
mobile money intégré, conçue pour les marchés africains francophones où
Orange Money et les solutions équivalentes constituent le moyen de paiement
dominant.

## Ce que fait la plateforme

GoEvent permet à trois rôles distincts d'interagir autour d'événements :

**Les organisateurs** créent et publient des événements (concerts, festivals,
conférences, soirées), définissent des catégories de billets et leurs prix,
gèrent leurs codes promotionnels, suivent leurs ventes en temps réel via un
tableau de bord, et déclenchent des retraits de leurs fonds vers leur compte
Orange Money.

**Les fans** parcourent les événements disponibles, réservent leurs billets
en payant via Orange Money ou en cash à l'entrée, reçoivent un QR code, et
gèrent leurs réservations depuis leur espace personnel.

**Les agents de scan** (employés temporaires de l'organisateur) valident
l'entrée des fans le jour de l'événement via une interface mobile dédiée
qui scanne les QR codes et affiche l'affluence en temps réel.

Une équipe interne (administrateurs, commercial, community manager,
designer) gère la modération, la régie publicitaire et le support depuis
un back-office dédié.

## Marché cible

La plateforme cible initialement la République Centrafricaine (RCA), avec
une expansion prévue vers le Cameroun et le Tchad, puis vers l'ensemble de
l'Afrique francophone.

Ce choix est dicté par trois caractéristiques techniques du marché :

- La pénétration du mobile money y est supérieure à celle des cartes
  bancaires, donc l'intégration Orange Money est l'enjeu de paiement central.
- Les solutions de billetterie internationales (Eventbrite, Billetweb) ne
  supportent pas le mobile money africain et imposent des paiements en
  devises étrangères, créant un vide concurrentiel.
- La langue française est la langue de travail dominante des organisateurs
  d'événements dans cette zone, ce qui élimine les acteurs anglophones.

## Modèle économique

Le modèle économique combine plusieurs sources de revenus, classées par
ordre de priorité et de maturité.

### Source 1 : Commission billetterie (active)

Sur chaque billet payant vendu via Orange Money, GoEvent prélève 11% du
prix de base, répartis ainsi :

- 4% facturés à l'acheteur (frais visibles ajoutés au prix affiché)
- 7% retenus sur la part versée à l'organisateur

L'organisateur reçoit donc 93% du prix de base sur son compte Orange Money
lors du retrait. Cette commission est calculée et appliquée au moment de
l'initiation du paiement, dans `api/payments.py`. Voir `04-PAYMENT_FLOW.md`
pour le détail technique.

### Source 1 bis : Acquisition par les événements gratuits

Les événements gratuits ne génèrent aucune commission directe. Ils servent
de levier d'acquisition à coût marginal quasi nul : un fan qui réserve un
billet gratuit fournit un email vérifié et entre dans l'audience qualifiée
de la plateforme, alimentant les segments exportables côté organisateur
(voir `03-DATA_MODEL.md`, table `audience_export_logs`).

L'hypothèse économique sous-jacente est qu'une fraction significative de
ces utilisateurs convertit vers un achat payant lors d'un événement
ultérieur, monétisant alors via la commission de la source 1.

### Source 2 : Régie publicitaire forfaitaire (active)

Trois formats publicitaires sont disponibles, chacun avec une grille
tarifaire fixe et publique :

- **Slot annonceur** : emplacements payés par des marques tierces sur le
  carrousel hero de la page d'accueil, déclinés en trois durées et prix.
- **Boost organisateur** : un organisateur paie pour mettre en avant son
  événement dans les listes et carrousels pendant une période donnée.
- **Pack Facebook Ads** : GoEvent gère pour le compte de l'organisateur
  une campagne publicitaire Facebook avec une marge fixe.

Ces formats sont implémentés dans `api/promotion.py` et leurs paiements
suivent le même flux NGSER que les billets, avec des préfixes d'order_id
distincts (`BST-`, `FBP-`) qui permettent au backend de router les
notifications de paiement vers le bon traitement.

### Sources 3 et 4 : Reportées

Deux sources de revenus complémentaires sont conçues mais reportées
jusqu'à atteinte de seuils de volume permettant de les exploiter
rentablement : un abonnement organisateur premium (reporté jusqu'à
constitution d'une base d'intérêt mesurée via la table
`premium_interests`) et une régie publicitaire à la performance
(reportée jusqu'à atteinte d'un volume publicitaire mensuel justifiant
l'infrastructure de tracking).

## Sécurité et confiance

Les flux financiers étant le cœur du produit, la plateforme applique des
règles strictes documentées dans `05-CONVENTIONS.md` et détaillées dans
`04-PAYMENT_FLOW.md` :

- Toute mutation de places disponibles passe par un verrou pessimiste
  PostgreSQL (`with_for_update`) pour éliminer la survente.
- Les notifications de paiement reçues du fournisseur de paiement (NGSER)
  ne sont jamais une source de vérité : le statut réel est toujours
  vérifié par appel sortant authentifié vers NGSER.
- Toute action sensible (création, validation, retrait) est tracée dans
  une table `security_events` via la fonction `log_security_event`.
- Les mots de passe (codes PIN à 4 chiffres) sont hachés via bcrypt.
- Les sessions utilisent un couple JWT court (24h) + refresh token, avec
  rafraîchissement automatique transparent côté client.

## État de la plateforme

La plateforme est fonctionnelle et déployée en environnement de pré-production
(sandbox). Le passage en production effective est conditionné à la réception
des clés d'API NGSER en environnement réel par le fournisseur, à
l'exécution d'une recette de qualité end-to-end couvrant les douze scénarios
critiques, et à un soft launch contrôlé auprès d'un panel restreint
d'organisateurs pilotes.

L'architecture, les conventions de code et les flux décrits dans la suite
de cette documentation sont stables et reflètent le système en production
sandbox au moment de la rédaction.
