/**
 * web/api/og-pass.js — Aperçu du lien d'un pass envoyé sur WhatsApp (fonction Vercel).
 *
 * Même principe que og-signup.js : vercel.json envoie UNIQUEMENT les robots d'aperçu (en-tête
 * user-agent) de /i/:token vers cette fonction ; les invités gardent la page statique. Sans
 * aperçu, l'invité voit un lien nu venant d'un numéro qu'il ne connaît pas.
 * Données : GET /invitations/pass/{token}/preview — l'événement seulement, JAMAIS le nom de
 * l'invité (l'aperçu passe par les serveurs de la messagerie). Image : une par famille, fixe.
 */
const API = process.env.GOEVENT_API_URL || "https://api.goevent.africa";
const SITE = "https://goevent.africa";
const IMAGES = { familial: SITE + "/og/invitation-famille.png", corporate: SITE + "/og/invitation-pro.png" };
const TOKEN_RE = /^[A-Za-z0-9_-]{20,64}$/;

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function page(meta, url) {
  const tags = [
    ["og:type", "website"], ["og:site_name", "GoEvent"], ["og:locale", "fr_FR"], ["og:url", url],
    ["og:title", meta.title], ["og:description", meta.description], ["og:image", meta.image],
    ["og:image:secure_url", meta.image], ["og:image:type", "image/png"],
    ["og:image:width", "1200"], ["og:image:height", "630"], ["og:image:alt", meta.title],
  ].map((t) => '<meta property="' + t[0] + '" content="' + esc(t[1]) + '">').join("\n    ");
  return '<!doctype html>\n<html lang="fr">\n  <head>\n    <meta charset="utf-8">\n' +
    "    <title>" + esc(meta.title) + "</title>\n" +
    '    <meta name="description" content="' + esc(meta.description) + '">\n' +
    '    <meta name="robots" content="noindex, nofollow">\n    ' + tags + "\n" +
    '    <meta name="twitter:card" content="summary_large_image">\n' +
    '    <meta name="twitter:title" content="' + esc(meta.title) + '">\n' +
    '    <meta name="twitter:description" content="' + esc(meta.description) + '">\n' +
    '    <meta name="twitter:image" content="' + esc(meta.image) + '">\n' +
    "  </head>\n  <body><p><a href=\"" + esc(url) + "\">" + esc(meta.title) + "</a></p></body>\n</html>\n";
}

async function build(token, fetchImpl) {
  const fallback = { title: "Votre invitation — GoEvent", image: IMAGES.familial,
    description: "Ouvrez votre invitation et votre pass d'entrée personnel." };
  if (!TOKEN_RE.test(token || "")) return { status: 404, meta: fallback };
  let d = null;
  try {
    const r = await fetchImpl(API + "/invitations/pass/" + encodeURIComponent(token) + "/preview", { headers: { accept: "application/json" } });
    if (r.ok) d = await r.json();
    else if (r.status === 404) return { status: 404, meta: fallback };
  } catch (e) { /* API injoignable : aperçu générique, pas d'erreur pour le robot */ }
  if (!d || !d.event_title) return { status: 200, meta: fallback, cacheShort: true };
  const pro = d.famille === "corporate";
  const parts = [d.when, d.location].filter(Boolean);
  return { status: 200, meta: {
    title: ((pro ? "Invitation : " : "Vous êtes invité(e) : ") + d.event_title).slice(0, 110),
    description: ((d.host_label ? d.host_label + (pro ? " vous convie" : " vous invite") + ". " : "") + parts.join(" · ")
      + (parts.length ? ". " : "") + "Ouvrez votre pass d'entrée personnel.").slice(0, 200),
    image: pro ? IMAGES.corporate : IMAGES.familial,
  } };
}

module.exports = async function handler(req, res) {
  const token = String((req.query && req.query.t) || "");
  const out = await build(token, fetch);
  res.statusCode = out.status;
  res.setHeader("Content-Type", "text/html; charset=utf-8");
  // Une URL par invité : cache court côté CDN, jamais indexé
  res.setHeader("Cache-Control", out.cacheShort ? "public, s-maxage=60" : "public, s-maxage=300, stale-while-revalidate=3600");
  res.setHeader("X-Robots-Tag", "noindex, noarchive");
  res.end(page(out.meta, SITE + "/i/" + encodeURIComponent(token)));
};
module.exports.build = build;
module.exports.page = page;
