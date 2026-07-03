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
]


def run_migrations() -> dict:
    """
    Exécute toutes les migrations dans l'ordre.
    Échec non bloquant : log + continue, retentée au boot suivant.
    Retourne un résumé { ok, skipped, failed } pour visibilité au démarrage.
    """
    summary = {"ok": 0, "failed": 0}

    with engine.begin() as conn:
        for label, stmt in MIGRATIONS:
            try:
                conn.execute(text(stmt))
                summary["ok"] += 1
                print(f"[Migration] OK — {label}")
            except Exception as e:
                summary["failed"] += 1
                print(f"[Migration] ⚠️  Échec non bloquant — {label} : {e}")

    print(f"[Migration] Terminé : {summary}")
    return summary
