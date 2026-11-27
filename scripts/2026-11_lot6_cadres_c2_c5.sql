-- Lot 6 (nov. 2026) : cadres corporate c2 « Badge-first » → « Accréditation »,
-- c5 « Webinaire / Live » → « Cérémonie officielle » (contenu, schéma de fiche, suggestions, skin).
-- Données : à exécuter à la main sur Neon (branche production) AVANT le push. Idempotent.
-- Aucun design n'utilisait c2 ni c5 au 27/11/2026 (vérifié). Même contenu que scripts/seed_inv_*.py.
BEGIN;
UPDATE inv_modele_cadres SET name = 'Accréditation', champs = '{"organisateur": "text", "titre": "text", "sous_titre": "text", "logo_url": "image", "lieu": "text", "consignes": "textarea"}'::jsonb, suggest_for = '["accreditation", "conference", "salon"]'::jsonb, scenes = '{"enveloppe": false, "page": true, "programme": false, "pass": "badge"}'::jsonb
 WHERE code = 'c2';
UPDATE inv_modeles m SET name = 'Accréditation — Ardoise', skin = '{"couleurs": {"fond": "#eef1f5", "accent": "#0f172a", "accentD": "#334155", "clair": "#ffffff", "texte": "#0f172a", "muted": "#64748b"}, "polices": {"titre": "Plus Jakarta Sans", "corps": "Plus Jakarta Sans"}, "ornements": {}, "mode": "clair"}'::jsonb, updated_at = now()
  FROM inv_modele_cadres c
 WHERE m.cadre_id = c.id AND c.code = 'c2' AND m.name IN ('Badge-first — Indigo', 'Accréditation — Ardoise');
UPDATE inv_modele_cadres SET name = 'Cérémonie officielle', champs = '{"organisateur": "text", "formule": "text", "titre": "text", "sous_titre": "text", "logo_url": "image", "lieu": "text", "sessions": "list[heure,titre,intervenant]"}'::jsonb, suggest_for = '["ceremonie", "institutionnel", "ag"]'::jsonb, scenes = '{"enveloppe": false, "page": true, "programme": false, "pass": "badge"}'::jsonb
 WHERE code = 'c5';
UPDATE inv_modeles m SET name = 'Cérémonie officielle — Marine & or', skin = '{"couleurs": {"fond": "#f5f3ee", "accent": "#1f3a5f", "accentD": "#8a6d2f", "clair": "#ffffff", "texte": "#1b2433", "muted": "#6b7280"}, "polices": {"titre": "Playfair Display", "corps": "Plus Jakarta Sans"}, "ornements": {"filet": "double"}, "mode": "clair"}'::jsonb, updated_at = now()
  FROM inv_modele_cadres c
 WHERE m.cadre_id = c.id AND c.code = 'c5' AND m.name IN ('Webinaire / Live — Cyan', 'Cérémonie officielle — Marine & or');
COMMIT;

-- Contrôle : 2 lignes, noms « Accréditation » et « Cérémonie officielle »
SELECT c.code, c.name, m.name AS modele FROM inv_modele_cadres c JOIN inv_modeles m ON m.cadre_id = c.id WHERE c.code IN ('c2', 'c5') ORDER BY c.code;
