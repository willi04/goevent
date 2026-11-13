/**
 * web/api/og-signup.js — Aperçu de partage d'une page d'inscription libre (fonction Vercel).
 *
 * Même principe que og-event.js : les robots d'aperçu (WhatsApp, Facebook, Telegram…) n'exécutent
 * pas le JavaScript d'inscription.html. vercel.json envoie UNIQUEMENT ces robots (en-tête
 * user-agent) de /r/:slug (et des alias courts, ex. /fonej) vers cette fonction ; les visiteurs
 * gardent la page statique. Sortie : une petite page avec les balises Open Graph.
 * Données : GET /invitations/signup/{slug} (titre, dates, lieu, affiche) — jamais un nom d'inscrit.
 */
const API = process.env.GOEVENT_API_URL || "https://api.goevent.africa";
const SITE = "https://goevent.africa";
const DEFAULT_IMAGE = SITE + "/og/invitations.png";

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function page(meta, url) {
  const tags = [
    ["og:type", "website"], ["og:site_name", "GoEvent"], ["og:locale", "fr_FR"], ["og:url", url],
    ["og:title", meta.title], ["og:description", meta.description], ["og:image", meta.image],
    ["og:image:width", meta.image === DEFAULT_IMAGE ? "" : "1200"], ["og:image:height", meta.image === DEFAULT_IMAGE ? "" : "630"],
    ["og:image:alt", meta.title],
  ].filter((t) => t[1]).map((t) => '<meta property="' + t[0] + '" content="' + esc(t[1]) + '">').join("\n    ");
  return '<!doctype html>\n<html lang="fr">\n  <head>\n    <meta charset="utf-8">\n' +
    "    <title>" + esc(meta.title) + "</title>\n" +
    '    <meta name="description" content="' + esc(meta.description) + '">\n' +
    '    <link rel="canonical" href="' + esc(url) + '">\n    ' + tags + "\n" +
    '    <meta name="twitter:card" content="summary_large_image">\n' +
    '    <meta name="twitter:title" content="' + esc(meta.title) + '">\n' +
    '    <meta name="twitter:description" content="' + esc(meta.description) + '">\n' +
    '    <meta name="twitter:image" content="' + esc(meta.image) + '">\n' +
    "  </head>\n  <body><p><a href=\"" + esc(url) + "\">" + esc(meta.title) + "</a></p></body>\n</html>\n";
}

async function build(slug, fetchImpl) {
  const fallback = { title: "Inscription — GoEvent", image: DEFAULT_IMAGE,
    description: "Votre pass d'entrée en 30 secondes, sans compte." };
  if (!/^[a-z0-9](?:[a-z0-9-]{1,38}[a-z0-9])$/.test(slug || "")) return { status: 404, meta: fallback };
  let d = null;
  try {
    const r = await fetchImpl(API + "/invitations/signup/" + encodeURIComponent(slug), { headers: { accept: "application/json" } });
    if (r.ok) d = await r.json();
    else if (r.status === 404) return { status: 404, meta: fallback };
  } catch (e) { /* API injoignable : aperçu générique, pas d'erreur pour le robot */ }
  if (!d || !d.event_title) return { status: 200, meta: fallback, cacheShort: true };
  const when = d.when || {};
  const parts = [when.days, when.hours, d.location].filter(Boolean);
  const closed = d.state && d.state !== "open";
  return { status: 200, meta: {
    title: d.event_title + (closed ? "" : " — Inscription gratuite"),
    description: (parts.join(" · ") + (closed ? "" : ". Votre pass d'entrée en 30 secondes, sans compte.")).slice(0, 200),
    image: /^https:\/\/[^\s"'<>]+$/.test(d.og_image_url || "") ? d.og_image_url : DEFAULT_IMAGE,
  } };
}

module.exports = async function handler(req, res) {
  const slug = String((req.query && req.query.slug) || "").toLowerCase();
  const path = String((req.query && req.query.path) || "");
  const out = await build(slug, fetch);
  const url = SITE + (/^\/[a-z0-9-]{3,40}$/.test(path) ? path : "/r/" + encodeURIComponent(slug));
  res.statusCode = out.status;
  res.setHeader("Content-Type", "text/html; charset=utf-8");
  res.setHeader("Cache-Control", out.cacheShort ? "public, s-maxage=60" : "public, s-maxage=300, stale-while-revalidate=86400");
  res.setHeader("X-Robots-Tag", "noarchive");
  res.end(page(out.meta, url));
};
module.exports.build = build;
module.exports.page = page;
