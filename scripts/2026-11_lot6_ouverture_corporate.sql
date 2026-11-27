-- Lot 6 (nov. 2026) : ouverture animée des invitations corporate.
-- c1 c2 c3 c7 c9 : badge qui descend (enveloppe officielle pour les invités officiels) ;
-- c4 c5 : enveloppe officielle pour tous. c6 c8 c10 : inchangés (aucune animation).
-- Données : à exécuter à la main sur Neon (branche production) AVANT le push. Idempotent.
-- Aucun design corporate en base au 27/11/2026 (vérifié). Même contenu que scripts/seed_inv_*.py.
BEGIN;
UPDATE inv_modele_cadres
   SET scenes = jsonb_set(scenes, '{enveloppe}', 'true'::jsonb)
 WHERE code IN ('c1', 'c2', 'c3', 'c4', 'c5', 'c7', 'c9');
UPDATE inv_modeles m
   SET enveloppe = true, updated_at = now()
  FROM inv_modele_cadres c
 WHERE m.cadre_id = c.id AND c.code IN ('c1', 'c2', 'c3', 'c4', 'c5', 'c7', 'c9') AND m.enveloppe = false;
COMMIT;

-- Contrôle : c1 c2 c3 c4 c5 c7 c9 → true / true ; c6 c8 c10 → false / false
SELECT c.code, c.scenes->>'enveloppe' AS cadre, m.enveloppe AS gabarit
  FROM inv_modele_cadres c JOIN inv_modeles m ON m.cadre_id = c.id
 WHERE c.famille = 'corporate' ORDER BY c.sort_order;
