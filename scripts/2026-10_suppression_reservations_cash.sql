-- ════════════════════════════════════════════════════════════════════
-- Suppression du flux "réservation cash" (oct. 2026)
-- À exécuter UNE FOIS, manuellement (console Neon), AVANT le deploy du code
-- qui retire les routes confirm-cash-payment / cancel-reservation.
-- Script de données : hors core/migrations.py (règle 2 : DDL uniquement là-bas).
-- ════════════════════════════════════════════════════════════════════

-- ── 1. APERÇU : qui est concerné ? (lecture seule) ──────────────────
-- Prévenir ces fans (email / téléphone) AVANT l'étape 2 si l'event est à venir.
SELECT t.id            AS ticket_id,
       t.event_id,
       e.title         AS event_title,
       e.event_date,
       u.full_name,
       u.phone_number,
       u.email,
       t.cash_amount,
       t.purchased_at
FROM tickets t
JOIN events e ON e.id = t.event_id
JOIN users  u ON u.id = t.user_id
WHERE t.payment_status = 'reservation_cash'
ORDER BY e.event_date, t.purchased_at;

-- ── 2. ANNULATION (transaction unique, rejouable sans effet) ────────
BEGIN;

-- Verrou des events touchés (même ordre que le code : event puis promo).
SELECT e.id FROM events e
WHERE e.id IN (SELECT event_id FROM tickets WHERE payment_status = 'reservation_cash')
FOR UPDATE;

-- 2.a Libération des places : seats_sold -= nb de réservations par event.
UPDATE events e
SET seats_sold = GREATEST(0, e.seats_sold - r.nb)
FROM (
    SELECT event_id, COUNT(*) AS nb
    FROM tickets
    WHERE payment_status = 'reservation_cash'
    GROUP BY event_id
) r
WHERE e.id = r.event_id;

-- 2.b Codes promo réservés par ces billets : used_count -= 1, usage → released
--     (équivalent SQL de utils/promo.release_promo_usage).
UPDATE promo_codes p
SET used_count = GREATEST(0, p.used_count - r.nb)
FROM (
    SELECT pu.promo_id, COUNT(*) AS nb
    FROM promo_usages pu
    JOIN tickets t ON t.id = pu.ticket_id
    WHERE t.payment_status = 'reservation_cash' AND pu.status = 'reserved'
    GROUP BY pu.promo_id
) r
WHERE p.id = r.promo_id;

UPDATE promo_usages pu
SET status = 'released'
FROM tickets t
WHERE t.id = pu.ticket_id
  AND t.payment_status = 'reservation_cash'
  AND pu.status = 'reserved';

-- 2.c Statut terminal : le billet n'est plus affiché ni scannable.
UPDATE tickets
SET payment_status = 'annule_cash_supprime'
WHERE payment_status = 'reservation_cash';

-- Contrôle : doit renvoyer 0 avant COMMIT.
SELECT COUNT(*) AS restant FROM tickets WHERE payment_status = 'reservation_cash';

COMMIT;

-- ── 3. CONTRÔLE DE COHÉRENCE seats_sold (lecture seule) ─────────────
-- Écart = places comptées sans billet actif correspondant. Doit être 0
-- (hors en_attente_om récents, qui sont légitimement pré-réservés).
SELECT e.id, e.title, e.seats_sold,
       COUNT(t.id) FILTER (WHERE t.payment_status IN ('paye', 'paye_cash', 'en_attente_om')) AS billets_actifs,
       e.seats_sold - COUNT(t.id) FILTER (WHERE t.payment_status IN ('paye', 'paye_cash', 'en_attente_om')) AS ecart
FROM events e
LEFT JOIN tickets t ON t.event_id = e.id
GROUP BY e.id
HAVING e.seats_sold <> COUNT(t.id) FILTER (WHERE t.payment_status IN ('paye', 'paye_cash', 'en_attente_om'))
ORDER BY ecart DESC;
