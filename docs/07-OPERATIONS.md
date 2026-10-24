# Opérations — déploiement, bascule prod, monitoring, incidents

Ce document est le runbook de production. Il doit pouvoir être suivi à
froid, par quelqu'un qui n'a pas écrit le code, un soir d'événement.

## Topologie

| Brique | Hébergement | Rôle |
|---|---|---|
| Frontend `web/` | Vercel → `goevent.africa` | Pages statiques, PWA. Rewrite `/e/:slug` (`web/vercel.json`). |
| API FastAPI | Render → `api.goevent.africa` | Toute la logique métier. |
| PostgreSQL | Neon | Base unique. Migrations DDL jouées au boot (`core/migrations.py`). |
| Cron reconcile | GitHub Actions (`.github/workflows/reconcile.yml`) | `POST /admin/reconcile` toutes les 5 min. |
| Paiement | NGSER (Orange Money) | Sessions d'achat, retraits, `check_payment_status`. |
| Erreurs | Sentry (back + front) | DSN back en variable d'env, DSN front dans `web/bt-api.js` (public par nature). |
| Email | Gmail SMTP (mot de passe d'application) | Billets, codes de reset, annulations. |

## Configuration Render

### Start command

Render → service API → **Settings** → **Build & Deploy** → **Start Command** :

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips='*'
```

⚠️ `--proxy-headers --forwarded-allow-ips='*'` est **obligatoire**.
Sans eux, uvicorn voit l'IP du load balancer Render pour toutes les
requêtes : le rate limiter (`core/limiter.py`) compte alors tous les
utilisateurs ensemble (login 5/min, inscription 3/min **pour toute la
plateforme**) et `security_logs.ip_address` ne contient que des IP
internes. uvicorn 0.30 (version épinglée) retient la **dernière** entrée
de `X-Forwarded-For`, celle ajoutée par Render : non falsifiable par le
client. Si uvicorn est mis à jour, revérifier ce comportement.

Vérification après deploy : se connecter, puis
`GET /admin/security-logs` (compte admin) → la ligne `login_success`
doit porter **votre** IP publique, pas une 10.x.x.x.

### Variables d'environnement

Liste complète et commentée : `.env.example`. Points critiques :

- `ENV=production` en prod (active la vérification SSL NGSER).
- `RECONCILE_SECRET` : identique au secret GitHub `RECONCILE_SECRET`.
  Vide ⇒ `/admin/reconcile` refuse tout (403).
- `HASHIDS_SALT` : doit être défini (la valeur par défaut est publique).
  ⚠️ Le **changer** invalide tous les slugs existants (`/e/<slug>`, liens
  de partage et liens promo déjà diffusés). Le fixer une fois, avant le
  lancement public, puis ne plus y toucher.
- `SECRET_KEY` : la changer déconnecte tous les utilisateurs (JWT
  invalidés). Rotation immédiate en cas de fuite.

## Déploiement

1. Scripts de **données** éventuels (`scripts/*.sql`) : exécutés à la
   main sur Neon **avant** le deploy du code qui en dépend. Chaque script
   commence par une requête d'aperçu en lecture seule.
2. Merge sur `main` → deploy Render.
3. Au boot, lire les logs Render :
   - `[Migration] Terminé : {'ok': N, 'failed': 0}` — `failed > 0` n'est
     pas bloquant (retenté au boot suivant) mais doit être compris : le
     plus souvent un `lock_timeout` (5 s) parce que l'ancienne instance
     tenait une transaction. Redéployer suffit en général.
   - `[Sentry] Activé — environment=production`.
4. Smoke test : `GET https://api.goevent.africa/` → `"status": "En ligne"`.

**Rollback** : Render → *Deploys* → *Rollback* sur le deploy précédent.
Les migrations sont additives (`ADD COLUMN/INDEX IF NOT EXISTS`) : l'ancien
code tourne sur le nouveau schéma.

## Cron de réconciliation

`POST /admin/reconcile` (header `X-Reconcile-Secret`) :

1. Réinterroge NGSER pour les billets, boosts, packs FB en `en_attente_om`
   entre 15 min et 24 h, et les retraits `en_cours` validés il y a 15 min
   à 24 h.
2. Expire les checkouts OM abandonnés depuis plus de 24 h
   (`expire_old_orphans`), après un dernier `check_payment_status`.

Le résumé JSON est visible dans les logs du job GitHub Actions.

⚠️ Deux façons connues de le voir s'arrêter **sans alerte** :

- **Abonnement Render suspendu** : l'API ne répond plus, le job échoue.
- **GitHub désactive les workflows `schedule`** d'un repo public après
  60 jours sans commit. Réactivation manuelle : onglet *Actions* →
  *NGSER Reconcile* → *Enable workflow*. Un commit ne suffit pas à le
  réactiver.

Contrôle hebdomadaire : onglet *Actions*, les runs doivent apparaître
toutes les ~5 min (GitHub peut retarder de 5 à 15 min aux heures
chargées). Déclenchement manuel : *Run workflow* (`workflow_dispatch`).

## Procédure de bascule sandbox → production NGSER

À dérouler dans l'ordre, sans sauter d'étape. Cocher au fur et à mesure.

**Avant les clés**
- [ ] Sur sandbox : achat OM de bout en bout, webhook reçu, billet `paye`,
      email reçu, scan **avec un compte agent** sur téléphone.
- [ ] Sur sandbox : retrait test validé par l'admin, statut `paye`.
- [ ] Start command Render avec `--proxy-headers` (section ci-dessus).
- [ ] `HASHIDS_SALT` définitif posé.
- [ ] Comptes staff créés : ≥ 1 commercial, ≥ 1 CM.

**Le jour J**
- [ ] ⚠️ **Masquer le contenu de démo AVANT d'activer les clés prod** : avec
      de vraies clés, un fan pourrait payer réellement un billet pour un
      event fictif. Events de démo = ids 44 à 59 (orga `+23670000002`) :
      ```sql
      -- Aperçu : aucun billet ne doit exister sur ces events
      SELECT e.id, e.title, COUNT(t.id) AS billets
      FROM events e LEFT JOIN tickets t ON t.event_id = e.id
      WHERE e.id BETWEEN 44 AND 59 GROUP BY e.id ORDER BY e.id;
      -- Masquage (réversible)
      UPDATE events SET is_active = false WHERE id BETWEEN 44 AND 59;
      ```
      Les comptes de démo (ids 16, 17, 23, 25–28) ont un PIN aléatoire
      inconnu depuis le 17/10/2026 : connexion impossible. Suppression
      définitive (comptes + events) après l'arrivée des vrais events, par
      script dédié (clés étrangères : billets de test, promos, boosts).
- [ ] Render : `NGSER_AUTH_TOKEN`, `NGSER_PAYMENT_TOKEN`,
      `NGSER_WITHDRAWAL_TOKEN`, `NGSER_SERVICE_NAME`, `NGSER_BASE_URL`
      (URL prod NGSER), `ENV=production`.
- [ ] `RECONCILE_SECRET` identique Render ↔ GitHub.
- [ ] URL de notification déclarée chez NGSER :
      `https://api.goevent.africa/payment/ngser-webhook`.
- [ ] Deploy, logs de boot OK (`failed: 0`, Sentry actif).
- [ ] Premier achat réel, petit montant, sur un event test non publié :
      billet `paye`, email reçu, `Payment.amount` = montant débité.
- [ ] Premier retrait réel, petit montant (≤ 1 000 FCFA) : statut `paye`,
      argent reçu sur le téléphone. **Si l'écran admin affiche « réponse
      ambiguë / ne pas relancer » : ne PAS recliquer** (cf. incident
      « Retrait bloqué en cours »).
- [ ] Requêtes de cohérence (section Monitoring) : toutes à 0.

**Les 24 h suivantes**
- [ ] Sentry ouvert en permanence, logs Render filtrés sur `🚨` et `❌`.
- [ ] Requêtes de cohérence matin et soir.

Critère d'ouverture publique (soft launch) : 0 P0, 5 billets payants de
bout en bout sans intervention, 1 retrait réussi, analyse des
`failure_reason`.

## Contenu du site (back-office)

- Délai de mise en ligne d'une modification : immédiat pour l'éditeur
  (cache invalidé dans son navigateur), ≤ 1 minute pour les visiteurs
  (`Cache-Control: max-age=60`), jusqu'à 5 minutes pour un visiteur déjà sur
  le site (cache de session de `cms.js`).
- Pages légales : toujours renseigner « Ce qui change » à la publication.
  Restaurer une ancienne version = la charger dans l'éditeur puis la publier
  (nouvelle version datée, l'historique n'est jamais réécrit).
- Ajout ou suppression de texte dans une page : relancer
  `python scripts/cms_tag.py`, commiter, déployer (API + front). Le registre
  est relu au démarrage de l'API.
- Traçabilité : `security_logs` → `cms_update`, `cms_page_published`,
  `cms_faq_created|updated|deleted|imported`.
- Emplacements : un bloc programmé (début futur) ou terminé reste dans
  l'admin avec son statut ; supprimer un bloc efface ses statistiques
  (relever affichages/clics avant pour le bilan du partenaire).
- Tarifs (admin) : changement effectif pour les paiements suivants en
  ≈ 30 s (cache API), affichage ≤ 1 min. Une commande déjà payée garde son
  montant. Journal : `pricing_update` (ancien → nouveau).
- Traçabilité emplacements : `zone_block_created|updated|deleted`,
  `zone_settings_updated`.

## Monitoring

### Logs à surveiller (Render)

| Motif | Sens | Action |
|---|---|---|
| `🚨 process_ngser` … `montant incohérent` | NGSER confirme un montant ≠ attendu | Incident « Montant incohérent » |
| `🚨 process_ngser PAYOUT : statut ambigu` | Retrait en code 4/5/6 | Incident « Retrait bloqué en cours » |
| `❌ Webhook : erreur check_payment_status` | NGSER injoignable | Le cron rattrapera ; si persistant → incident « NGSER indisponible » |
| `[Migration] ⚠️` | Migration non appliquée | Lire l'erreur, redéployer |

### Journal de sécurité (`security_logs`)

Événements utiles : `payout_paid`, `payout_rejected`, `payout_ambiguous`,
`payout_phone_changed` (numéro de destination des fonds modifié),
`scan_forbidden` (tentative de scan hors de son event), `account_locked`,
`login_failed`, `free_ticket`, `agent_removed`.

```sql
SELECT created_at, event_type, user_id, ip_address, details
FROM security_logs
WHERE event_type IN ('payout_ambiguous','payout_phone_changed','scan_forbidden','account_locked')
  AND created_at > now() - interval '7 days'
ORDER BY created_at DESC;
```

### Requêtes de cohérence (doivent toutes renvoyer 0 ligne)

```sql
-- 1. Billets OM bloqués en attente depuis > 25 h (cron en panne ?)
SELECT id, payment_ref, purchased_at FROM tickets
WHERE payment_status = 'en_attente_om' AND purchased_at < now() - interval '25 hours';

-- 2. Retraits en cours depuis > 1 h (issue NGSER à trancher à la main)
SELECT id, organizer_id, amount, payment_ref, processed_at FROM payout_requests
WHERE status = 'en_cours' AND processed_at < now() - interval '1 hour';

-- 3. Paiement complété sans billet valide (ou l'inverse)
SELECT p.id, p.transaction_id, t.payment_status FROM payments p
JOIN tickets t ON t.id = p.ticket_id
WHERE (p.status = 'completed' AND t.payment_status NOT IN ('paye','paye_cash'))
   OR (p.status <> 'completed' AND t.payment_status = 'paye' AND t.payment_method = 'orange_money');

-- 4. seats_sold désynchronisé des billets actifs
SELECT e.id, e.title, e.seats_sold,
       COUNT(t.id) FILTER (WHERE t.payment_status IN ('paye','paye_cash','en_attente_om')) AS actifs
FROM events e LEFT JOIN tickets t ON t.event_id = e.id
GROUP BY e.id
HAVING e.seats_sold <> COUNT(t.id) FILTER (WHERE t.payment_status IN ('paye','paye_cash','en_attente_om'));

-- 5. Retraits dépassant la cagnotte électronique de l'event
SELECT pr.event_id, SUM(pr.amount) AS retire, MAX(c.cagnotte) AS cagnotte
FROM payout_requests pr
JOIN (
  SELECT t.event_id, SUM(p.organizer_amount) AS cagnotte
  FROM payments p JOIN tickets t ON t.id = p.ticket_id
  WHERE p.status = 'completed' AND COALESCE(t.payment_method,'') NOT IN ('cash','gratuit')
  GROUP BY t.event_id
) c ON c.event_id = pr.event_id
WHERE pr.status IN ('en_attente','en_cours','paye')
GROUP BY pr.event_id
HAVING SUM(pr.amount) > MAX(c.cagnotte) + 1;
```

## Runbook incidents

Règle générale pour l'argent : **dans le doute, ne rien relancer**. Un
statut figé se corrige à la main ; un double virement ne se rattrape pas.

### Fan débité mais pas de billet

1. Retrouver le ticket : `SELECT * FROM tickets WHERE user_id = … ORDER BY purchased_at DESC;`
2. Statut `en_attente_om` depuis < 24 h : le cron le validera. Pour
   forcer : *Run workflow* sur le job reconcile.
3. Statut `echec_paiement` / `expire_abandonne` alors que le fan montre
   un SMS de débit Orange : vérifier l'`order_id` (`payment_ref`) chez
   NGSER. Si NGSER confirme le succès → ouvrir un incident, valider le
   billet à la main (`payment_status='paye'`, `payments.status='completed'`)
   **dans une transaction**, en vérifiant d'abord qu'il reste une place
   (sinon `seats_sold + 1` peut dépasser `total_seats` : prévenir l'orga).

### Retrait bloqué « en cours »

Un retrait reste `en_cours` quand l'issue côté NGSER est inconnue
(timeout, 5xx, code 4/5/6). C'est voulu : remettre `en_attente`
permettait un second virement.

1. Attendre un passage du cron (≤ 30 min) : il tranche si NGSER répond.
2. Toujours `en_cours` après 1 h : vérifier chez NGSER avec le
   `payment_ref` (`WD<id>-…`), et/ou demander à l'orga s'il a reçu l'argent.
3. Argent **reçu** → `UPDATE payout_requests SET status='paye' WHERE id=…;`
4. Argent **non reçu et NGSER confirme l'échec** →
   `UPDATE payout_requests SET status='en_attente' WHERE id=…;` puis
   l'admin peut revalider (nouveau `payment_ref`).
5. Tracer la décision : `INSERT INTO security_logs (event_type, user_id, details, created_at) VALUES ('payout_manual_fix', <admin_id>, '…', now());`

### Montant incohérent (`🚨`)

NGSER confirme un paiement avec un montant différent de l'attendu. Billet :
passé en `echec_paiement` et place libérée automatiquement. Retrait :
statut inchangé. Dans les deux cas : comparer avec le relevé NGSER, ne
rien rembourser ni valider avant d'avoir compris l'écart.

### NGSER indisponible

- Les achats échouent proprement (place libérée, message « service
  temporairement indisponible »).
- Les webhooks qui ne peuvent pas être vérifiés sont rattrapés par le
  cron. `expire_old_orphans` ne libère **rien** tant que NGSER ne répond pas.
- Communiquer aux orgas en cas d'événement le jour même.

### Cron reconcile en échec

- HTTP 403 : `RECONCILE_SECRET` différent entre Render et GitHub.
- HTTP 5xx / timeout : API down (Render suspendu ? deploy en cours ?).
- Aucun run visible : workflow désactivé (cf. section Cron).

### Boot bloqué / migration en échec

`[Migration] ⚠️ … lock timeout` : une autre connexion tenait la table.
Redéployer. Si l'erreur est autre (SQL invalide), corriger la migration
dans `core/migrations.py` : ne jamais modifier une entrée déjà déployée,
ajouter une nouvelle entrée corrective en fin de liste.

### Fuite de secret

1. Révoquer / changer immédiatement la valeur (Render, Google, NGSER).
2. `SECRET_KEY` changée ⇒ tous les utilisateurs devront se reconnecter.
3. Le repo est public : un secret commité doit être considéré comme
   compromis même après suppression du fichier ; réécrire l'historique
   n'annule pas l'exposition (forks, caches), la rotation si.
