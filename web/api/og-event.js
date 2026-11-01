/**
 * web/api/og-event.js — Aperçu de partage d'un événement (fonction Vercel, lot 5).
 *
 * Les robots d'aperçu (WhatsApp, Facebook, Telegram, X, LinkedIn…) n'exécutent pas
 * le JavaScript de event_detail.html : ils ne voyaient que « GoEvent » sans image.
 * vercel.json envoie UNIQUEMENT ces robots (en-tête user-agent) de /e/:slug vers
 * cette fonction ; les visiteurs gardent la page statique.
 * Sortie : une petite page HTML avec les balises Open Graph de l'événement.
 * Aucune donnée privée : uniquement GET /events/by-slug (événements publics actifs).
 */
const API = process.env.GOEVENT_API_URL || "https://api.goevent.africa";
const SITE = "https://goevent.africa";
const DEFAULT_IMAGE = SITE + "/og/evenement.png";

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function plain(html) {
  return String(html || "")
    .replace(/<[^>]*>/g, " ")
    .replace(/&nbsp;/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"').replace(/&#39;/g, "'")
    .replace(/\s+/g, " ").trim();
}

function fcfa(n) {
  return String(Math.round(n || 0)).replace(/\B(?=(\d{3})+(?!\d))/g, " ") + " FCFA";
}

function when(iso) {
  if (!iso) return "";
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z");
  if (isNaN(d)) return "";
  const s = d.toLocaleString("fr-FR", { timeZone: "Africa/Bangui", weekday: "long", day: "numeric",
    month: "long", hour: "2-digit", minute: "2-digit" }).replace(":", "h");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function minPrice(ev) {
  const tiers = Array.isArray(ev.ticket_tiers) ? ev.ticket_tiers : [];
  const prices = tiers.map((t) => Number(t && t.price)).filter((p) => !isNaN(p));
  return prices.length ? Math.min.apply(null, prices) : Number(ev.price) || 0;
}

function page(meta, slug) {
  const url = SITE + "/e/" + encodeURIComponent(slug);
  const tags = [
    ["og:type", "website"], ["og:site_name", "GoEvent"], ["og:locale", "fr_FR"], ["og:url", url],
    ["og:title", meta.title], ["og:description", meta.description], ["og:image", meta.image],
    ["og:image:alt", meta.title],
  ].map((t) => '<meta property="' + t[0] + '" content="' + esc(t[1]) + '">').join("\n    ");
  return '<!doctype html>\n<html lang="fr">\n  <head>\n    <meta charset="utf-8">\n' +
    "    <title>" + esc(meta.title) + "</title>\n" +
    '    <meta name="description" content="' + esc(meta.description) + '">\n' +
    '    <link rel="canonical" href="' + esc(url) + '">\n    ' + tags + "\n" +
    '    <meta name="twitter:card" content="summary_large_image">\n' +
    '    <meta name="twitter:title" content="' + esc(meta.title) + '">\n' +
    '    <meta name="twitter:description" content="' + esc(meta.description) + '">\n' +
    '    <meta name="twitter:image" content="' + esc(meta.image) + '">\n' +
    '    <meta http-equiv="refresh" content="0;url=' + esc("/event_detail.html?slug=" + encodeURIComponent(slug)) + '">\n' +
    "  </head>\n  <body><p><a href=\"" + esc(url) + "\">" + esc(meta.title) + "</a></p></body>\n</html>\n";
}

async function build(slug, fetchImpl) {
  const fallback = { title: "Événement sur GoEvent", image: DEFAULT_IMAGE,
    description: "Billets en ligne, payés par Orange Money. Un pass, une entrée, zéro fraude." };
  if (!/^[A-Za-z0-9]{4,32}$/.test(slug || "")) return { status: 404, meta: fallback };
  let ev = null;
  try {
    const r = await fetchImpl(API + "/events/by-slug/" + encodeURIComponent(slug), { headers: { accept: "application/json" } });
    if (r.ok) ev = await r.json();
    else if (r.status === 404) return { status: 404, meta: fallback };
  } catch (e) { /* API injoignable : aperçu générique, pas d'erreur pour le robot */ }
  if (!ev || !ev.title) return { status: 200, meta: fallback, cacheShort: true };
  const low = minPrice(ev);
  const parts = [when(ev.event_date), ev.location, low > 0 ? "dès " + fcfa(low) : "Gratuit"].filter(Boolean);
  const desc = plain(ev.description);
  const description = (parts.join(" · ") + (desc ? " — " + desc : "")).slice(0, 200);
  const img = String(ev.cover_image_url || "");
  return { status: 200, meta: {
    title: ev.title + " — GoEvent",
    description,
    image: /^https:\/\/[^\s"'<>]+$/.test(img) ? img : DEFAULT_IMAGE,   // image base64 : illisible par les robots
  } };
}

module.exports = async function handler(req, res) {
  const slug = String((req.query && req.query.slug) || "");
  const out = await build(slug, fetch);
  res.statusCode = out.status;
  res.setHeader("Content-Type", "text/html; charset=utf-8");
  res.setHeader("Cache-Control", out.cacheShort ? "public, s-maxage=60" : "public, s-maxage=600, stale-while-revalidate=86400");
  res.setHeader("X-Robots-Tag", "noarchive");
  res.end(page(out.meta, slug || ""));
};
module.exports.build = build;
module.exports.page = page;
