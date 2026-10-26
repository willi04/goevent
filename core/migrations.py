"""
core/migrations.py
Migrations runtime idempotentes (Neon, pas d'Alembic — cf TECH_Core).

POURQUOI CE FICHIER
─────────────────────────────────────────────────────────────
SQLAlchemy `Base.metadata.create_all()` crée les tables MANQUANTES mais ne
propage JAMAIS les changements de colonnes sur tables existantes. Toute
nouvelle Column sur un modèle déjà déployé doit avoir son ALTER TABLE ici,
sinon le code prod va tenter de lire/écrire une colonne inexistante → 500.

RÈGLES
─────────────────────────────────────────────────────────────
1. CHAQUE instruction DOIT être idempotente : `IF NOT EXISTS` obligatoire
   pour ADD COLUMN / CREATE INDEX, `IF EXISTS` pour DROP. Une migration
   doit pouvoir être rejouée 100 fois sans effet ni erreur.
2. Pas de DELETE/UPDATE de données ici. Ce fichier ne fait QUE des DDL
   (structure). Les backfills de données vont dans des scripts ad-hoc
   exécutés manuellement, jamais au boot.
3. JAMAIS retirer une entrée passée. On AJOUTE à la fin de la liste, on
   ne ré-organise pas. L'ordre de la liste est l'historique chronologique
   du schéma.
4. Échec non bloquant : si une migration plante (typiquement réseau),
   on log et on continue — le backend doit pouvoir démarrer même si une
   migration optionnelle échoue. Re-tentée au prochain boot.

USAGE
─────────────────────────────────────────────────────────────
Appelé une fois depuis main.py après Base.metadata.create_all() :

    from core.migrations import run_migrations
    run_migrations()

Pour ajouter une migration : append à la liste MIGRATIONS, déployer.
Pour la rejouer manuellement : aussi exécutable en console Neon (les
instructions sont du SQL standard portable).
"""
from sqlalchemy import text

from core.database import engine


# Liste chronologique des migrations DDL. Append-only.
# Format : (description_courte, sql_idempotent)
MIGRATIONS = [
    # ──────────────────────────────────────────────────────────
    # Sécurisation webhook NGSER : persister l'order_id WD{id}-{uuid}
    # des retraits B2C pour que le cron reconcile puisse interroger
    # NGSER sans avoir à le reforger (UUID aléatoire, impossible sinon).
    # Déjà exécutée manuellement en sandbox via console Neon — laissée
    # ici pour synchroniser tout nouvel environnement (prod, dev local).
    # IF NOT EXISTS garantit l'idempotence si déjà appliquée.
    # ──────────────────────────────────────────────────────────
    (
        "payout_requests.payment_ref",
        "ALTER TABLE payout_requests ADD COLUMN IF NOT EXISTS payment_ref VARCHAR",
    ),
    (
        "payout_requests.payment_ref (index)",
        "CREATE INDEX IF NOT EXISTS ix_payout_requests_payment_ref "
        "ON payout_requests (payment_ref)",
    ),
    (
        "payout_requests.processed_at",
        "ALTER TABLE payout_requests ADD COLUMN IF NOT EXISTS processed_at TIMESTAMP",
    ),
    (
        "payout_requests.processed_at (index)",
        "CREATE INDEX IF NOT EXISTS ix_payout_requests_processed_at "
        "ON payout_requests (processed_at)",
    ),
   (
        "tickets.failure_reason",
        "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS failure_reason VARCHAR(50)",
    ),
    # ──────────────────────────────────────────────────────────
    # Audit oct. 2026 — index chemins chauds (aucun n'existait) :
    # - tickets.payment_ref : lookup FOR UPDATE webhook + reconcile
    # - tickets(event_id, payment_status) : stats scan, cagnotte, listes orga
    # - tickets.user_id : /tickets/my, limite 3 réservations
    # - payments.transaction_id / ticket_id : _process_ticket, cagnotte
    # - security_logs(user_id, created_at) : lu à CHAQUE requête authentifiée
    #   (SessionManager.verify_session) → seq scan croissant sans cet index.
    # ──────────────────────────────────────────────────────────
    (
        "tickets.payment_ref (index)",
        "CREATE INDEX IF NOT EXISTS ix_tickets_payment_ref ON tickets (payment_ref)",
    ),
    (
        "tickets(event_id, payment_status) (index)",
        "CREATE INDEX IF NOT EXISTS ix_tickets_event_status ON tickets (event_id, payment_status)",
    ),
    (
        "tickets.user_id (index)",
        "CREATE INDEX IF NOT EXISTS ix_tickets_user_id ON tickets (user_id)",
    ),
    (
        "payments.transaction_id (index)",
        "CREATE INDEX IF NOT EXISTS ix_payments_transaction_id ON payments (transaction_id)",
    ),
    (
        "payments.ticket_id (index)",
        "CREATE INDEX IF NOT EXISTS ix_payments_ticket_id ON payments (ticket_id)",
    ),
    (
        "security_logs(user_id, created_at) (index)",
        "CREATE INDEX IF NOT EXISTS ix_security_logs_user_created "
        "ON security_logs (user_id, created_at DESC)",
    ),
    # ──────────────────────────────────────────────────────────
    # Invitations digitales (oct. 2026) : événements privés jamais listés ni
    # vendus. DEFAULT constant → ajout instantané (métadonnée, PG ≥ 11), les
    # événements existants restent "public". Toute requête Event lit cette
    # colonne : à exécuter AVANT le déploiement si possible (cf. docs/07).
    # ──────────────────────────────────────────────────────────
    (
        "events.visibility",
        "ALTER TABLE events ADD COLUMN IF NOT EXISTS visibility VARCHAR(10) NOT NULL DEFAULT 'public'",
    ),
]


def run_migrations() -> dict:
    """
    Exécute toutes les migrations dans l'ordre.
    Échec non bloquant : log + continue, retentée au boot suivant.
    Retourne un résumé { ok, skipped, failed } pour visibilité au démarrage.
    """
    summary = {"ok": 0, "failed": 0}

    # 1 transaction PAR migration. Avant : une seule transaction pour toute la
    # liste → en PostgreSQL, un échec met la transaction en état "aborted",
    # toutes les suivantes échouent et le COMMIT final devient un ROLLBACK
    # silencieux (y compris des migrations loguées "OK"). Règle 4 violée.
    #
    # lock_timeout : ADD COLUMN IF NOT EXISTS prend un ACCESS EXCLUSIVE lock
    # MÊME si la colonne existe. Au deploy Render, l'ancienne instance peut
    # tenir une transaction ouverte (cron reconcile) → l'ALTER attend sans fin,
    # et toutes les requêtes sur la table s'empilent derrière = outage + boot
    # bloqué. 5s max puis échec non bloquant, retenté au boot suivant.
    for label, stmt in MIGRATIONS:
        try:
            with engine.begin() as conn:
                conn.execute(text("SET LOCAL lock_timeout = '5s'"))
                conn.execute(text(stmt))
            summary["ok"] += 1
            print(f"[Migration] OK — {label}")
        except Exception as e:
            summary["failed"] += 1
            print(f"[Migration] ⚠️  Échec non bloquant — {label} : {e}")

    print(f"[Migration] Terminé : {summary}")
    return summary
