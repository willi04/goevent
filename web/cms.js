/* ════════════════════════════════════════════════════════════════
 * cms.js — Contenu éditable depuis le back-office (admin.html → Contenu du site)
 *
 * Le HTML contient le texte par défaut. Ce script récupère les surcharges et
 * les applique :
 *   data-cms="clé"            → textContent (jamais innerHTML : pas d'injection)
 *   data-cms-img="clé"        → src
 *   data-cms-href="clé"       → href (+ data-cms-href-prefix, ex. "mailto:")
 *   data-cms-hide-empty       → élément masqué tant que la valeur est vide
 *   data-cms-page="slug"      → corps de page légale (HTML nettoyé côté serveur)
 *   data-cms-page-date        → « Dernière mise à jour : … » de la version publiée
 *   data-cms-faq              → FAQ du centre d'aide
 *   data-zone="clé"           → emplacement : blocs (affiches, bannières, annonces, cartes)
 *                               ou encart « emplacement disponible » (cf. core/zones.py)
 *   data-zone-infeed="clé"    → grille : cartes sponsorisées insérées (btZonesInfeed)
 *   Bandeau global « global.top » : injecté en haut des pages publiques de la
 *   liste blanche TOPBAR_PAGES (jamais admin, scanner, paiement, tableaux de bord,
 *   pass /i/, suivi /suivi/, invitations.html).
 *   data-price="groupe.clé"   → prix en vigueur (GET /pricing, source unique des tarifs)
 * Sans réseau / API en panne : la page garde son contenu statique.
 * Autonome (pas de dépendance à bt-api.js) : utilisé aussi par /simulateur/.
 * ════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";
  var API = (typeof BT_API !== "undefined" && BT_API) || "https://api.goevent.africa";
  var TTL = 5 * 60 * 1000;
  var values = null;

  function store(k, v) { try { sessionStorage.setItem(k, JSON.stringify({ t: Date.now(), v: v })); } catch (e) {} }
  function load(k) {
    try {
      var raw = sessionStorage.getItem(k);
      if (!raw) return null;
      var o = JSON.parse(raw);
      return Date.now() - o.t < TTL ? o.v : null;
    } catch (e) { return null; }
  }
  // Mis à jour par admin.html à chaque enregistrement → invalide caches navigateur et session
  function bust() { try { return localStorage.getItem("goevent_cms_bust") || "0"; } catch (e) { return "0"; } }

  function getJSON(path, cacheKey) {
    var cached = cacheKey ? load(cacheKey) : null;
    if (cached) return Promise.resolve(cached);
    var ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
    var timer = ctrl ? setTimeout(function () { ctrl.abort(); }, 7000) : null;
    var sep = path.indexOf("?") >= 0 ? "&" : "?";
    return fetch(API + path + sep + "b=" + encodeURIComponent(bust()), ctrl ? { signal: ctrl.signal } : {})
      .then(function (r) { if (timer) clearTimeout(timer); return r.ok ? r.json() : null; })
      .then(function (data) { if (data && cacheKey) store(cacheKey, data); return data; })
      .catch(function () { return null; });
  }

  function absolute(url) { return url && url.charAt(0) === "/" ? API + url : url; }
  function safeHref(href) {
    var low = String(href || "").trim().toLowerCase();
    if (!low) return null;
    if (/^(https?:|mailto:|tel:)/.test(low)) return href;
    return /^[a-z][a-z0-9+.-]*:/.test(low) ? null : href; // bloque javascript:, data:…
  }

  function apply(root) {
    if (!values || !root || !root.querySelectorAll) return;
    var nodes;
    nodes = root.querySelectorAll("[data-cms]");
    for (var i = 0; i < nodes.length; i++) {
      var k = nodes[i].getAttribute("data-cms");
      if (Object.prototype.hasOwnProperty.call(values, k) && nodes[i].textContent !== values[k]) {
        nodes[i].textContent = values[k];
        if (values[k].indexOf("\n") >= 0) nodes[i].style.whiteSpace = "pre-line";
      }
    }
    nodes = root.querySelectorAll("[data-cms-img]");
    for (i = 0; i < nodes.length; i++) {
      var ki = nodes[i].getAttribute("data-cms-img");
      if (values[ki]) nodes[i].setAttribute("src", absolute(values[ki]));
    }
    nodes = root.querySelectorAll("[data-cms-href]");
    for (i = 0; i < nodes.length; i++) {
      var el = nodes[i], kh = el.getAttribute("data-cms-href");
      if (!Object.prototype.hasOwnProperty.call(values, kh)) continue;
      var v = String(values[kh] || "").trim();
      var prefix = el.getAttribute("data-cms-href-prefix") || "";
      if (prefix === "https://wa.me/") v = v.replace(/\D/g, "");
      var href = v ? safeHref(prefix + v) : null;
      if (href) {
        el.setAttribute("href", href);
        el.classList.remove("hidden");
      } else if (el.hasAttribute("data-cms-hide-empty")) {
        el.classList.add("hidden");
      }
    }
  }

  function pagesInDom() {
    var set = { global: 1 };
    var nodes = document.querySelectorAll("[data-cms],[data-cms-img],[data-cms-href]");
    for (var i = 0; i < nodes.length; i++) {
      var k = nodes[i].getAttribute("data-cms") || nodes[i].getAttribute("data-cms-img") ||
              nodes[i].getAttribute("data-cms-href") || "";
      var p = k.split(".")[0];
      if (p) set[p] = 1;
    }
    return Object.keys(set).sort().join(",");
  }

  function frDate(iso) {
    if (!iso) return "";
    var d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z");
    try { return d.toLocaleDateString("fr-FR", { day: "numeric", month: "long", year: "numeric" }); }
    catch (e) { return d.toISOString().slice(0, 10); }
  }

  function legalPage() {
    var el = document.querySelector("[data-cms-page]");
    if (!el) return;
    var slug = el.getAttribute("data-cms-page");
    getJSON("/pages/" + encodeURIComponent(slug), "goevent_cms_page:" + slug + ":" + bust()).then(function (p) {
      if (!p || !p.body_html) return;
      el.innerHTML = p.body_html; // nettoyé par bleach côté serveur (liste blanche)
      var dates = document.querySelectorAll("[data-cms-page-date]");
      for (var i = 0; i < dates.length; i++) dates[i].textContent = "Dernière mise à jour : " + frDate(p.updated_at);
      // Lien vers une section (#invitations…) : le texte vient d'être remplacé, on y revient
      var id = decodeURIComponent((location.hash || "").slice(1));
      var target = id && /^[A-Za-z0-9_-]{1,40}$/.test(id) ? document.getElementById(id) : null;
      if (target && target.scrollIntoView) target.scrollIntoView();
    });
  }

  function faq() {
    var box = document.querySelector("[data-cms-faq]");
    if (!box) return;
    getJSON("/faq", "goevent_cms_faq:" + bust()).then(function (data) {
      if (!data || !data.items || !data.items.length) return; // FAQ statique conservée
      var frag = document.createDocumentFragment();
      data.items.forEach(function (it) {
        var item = document.createElement("div");
        item.className = "faq-item bg-white rounded-2xl border border-gray-100 shadow-sm overflow-hidden";
        item.setAttribute("data-cat", it.category);
        var btn = document.createElement("button");
        btn.className = "w-full text-left p-5 flex justify-between items-center gap-4 hover:bg-gray-50 transition-colors";
        btn.setAttribute("onclick", "toggleFaq(this)");
        var q = document.createElement("span");
        q.className = "font-bold text-gray-900 text-sm md:text-base";
        q.textContent = it.question;
        var icon = document.createElement("span");
        icon.className = "faq-icon material-symbols-outlined text-primary flex-shrink-0";
        icon.textContent = "expand_more";
        btn.appendChild(q); btn.appendChild(icon);
        var ans = document.createElement("div");
        ans.className = "faq-answer px-5 pb-5";
        var body = document.createElement("div");
        body.className = "text-sm text-gray-600 leading-relaxed space-y-2";
        body.innerHTML = it.answer_html; // nettoyé par bleach côté serveur
        ans.appendChild(body);
        item.appendChild(btn); item.appendChild(ans);
        frag.appendChild(item);
      });
      // On ne retire que les questions statiques : le conteneur porte aussi le bloc #no-results
      var old = box.querySelectorAll(".faq-item");
      for (var j = 0; j < old.length; j++) old[j].parentNode.removeChild(old[j]);
      box.insertBefore(frag, box.firstChild);
      var counts = document.querySelectorAll('.category-pill[data-cat="all"] .category-count');
      for (var i = 0; i < counts.length; i++) counts[i].textContent = String(data.items.length);
    });
  }

  // ════════════════════════════════════════════════════════════════
  // TARIFS — source unique (GET /pricing, réglés dans le back-office)
  //   data-price="boost.7d"  data-price-format="fcfa" (défaut) | "number" | "pct"
  //   null → « Sur devis ». window.btPricing() → Promise des tarifs (JS : dashboard orga…)
  // ════════════════════════════════════════════════════════════════
  var pricingPromise = null;
  function btPricing() {
    if (!pricingPromise) {
      pricingPromise = getJSON("/pricing", "goevent_pricing:" + bust()).then(function (p) {
        if (p) { window.btPricingCache = p; applyPrices(document, p); }
        return p;
      });
    }
    return pricingPromise;
  }
  function priceValue(p, path) {
    var parts = String(path || "").split("."), v = p;
    for (var i = 0; i < parts.length; i++) { if (v == null) return undefined; v = v[parts[i]]; }
    return v && typeof v === "object" && "amount" in v ? v.amount : v;
  }
  function fmtPrice(v, format) {
    if (v === null) return format === "number" ? "—" : "Sur devis";
    if (typeof v !== "number") return null;
    if (format === "pct") return String(v).replace(".", ",") + "%";
    if (format === "month") return Math.round(v).toLocaleString("fr-FR") + " FCFA / mois";
    var n = Math.round(v).toLocaleString("fr-FR");
    return format === "number" ? n : n + " FCFA";
  }
  function applyPrices(root, p) {
    if (!p || !root.querySelectorAll) return;
    var nodes = root.querySelectorAll("[data-price]");
    for (var i = 0; i < nodes.length; i++) {
      var v = priceValue(p, nodes[i].getAttribute("data-price"));
      if (v === undefined) continue;
      var txt = fmtPrice(v, nodes[i].getAttribute("data-price-format") || "fcfa");
      if (txt !== null && nodes[i].textContent !== txt) nodes[i].textContent = txt;
      if (nodes[i].hasAttribute("data-price-value") && typeof v === "number") nodes[i].setAttribute("value", String(v));
    }
  }

  // ════════════════════════════════════════════════════════════════
  // EMPLACEMENTS — <div data-zone="clé"> remplis avec les blocs du back-office
  // ════════════════════════════════════════════════════════════════
  var zonesData = null, pendingInfeed = [], seen = {};
  var THEMES = {
    green: { box: "bg-[#00502c] text-white", btn: "bg-white text-[#00502c]", muted: "text-white/80" },
    dark: { box: "bg-gray-900 text-white", btn: "bg-[#fecb00] text-gray-900", muted: "text-white/75" },
    light: { box: "bg-white text-gray-900 border border-gray-100 shadow-sm", btn: "bg-[#00502c] text-white", muted: "text-gray-600" },
    gold: { box: "bg-[#fecb00] text-gray-900", btn: "bg-gray-900 text-white", muted: "text-gray-800" },
  };
  function h(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null && text !== "") n.textContent = text;
    return n;
  }
  function beacon(kind, ids) {
    if (!ids.length) return;
    var body = JSON.stringify(kind === "c" ? { c: ids } : { i: ids });
    try {
      if (navigator.sendBeacon) { navigator.sendBeacon(API + "/zones/events", new Blob([body], { type: "text/plain" })); return; }
    } catch (e) {}
    try { fetch(API + "/zones/events", { method: "POST", body: body, keepalive: true }); } catch (e) {}
  }
  function linkWrap(b, child, cls) {
    var href = b.link_url ? safeHref(b.link_url) : null;
    if (!href) { var d = h("div", cls); d.appendChild(child); return d; }
    var a = h("a", cls);
    a.setAttribute("href", href);
    if (/^https?:/i.test(href)) { a.setAttribute("target", "_blank"); a.setAttribute("rel", "noopener sponsored"); }
    a.addEventListener("click", function () { beacon("c", [b.id]); });
    a.appendChild(child);
    return a;
  }
  function sponsorTag(b, dark) {
    if (!b.sponsor) return null;
    return h("span", "inline-block text-[10px] font-black uppercase tracking-widest px-2 py-1 rounded-full " +
      (dark ? "bg-black/40 text-white" : "bg-black/5 text-gray-600"), "Sponsorisé · " + b.sponsor);
  }
  function button(b, theme) {
    if (!b.link_url || !b.link_label) return null;
    return h("span", "inline-block mt-4 px-6 py-3 rounded-full font-black text-sm shadow-sm " + theme.btn, b.link_label);
  }
  function renderImage(b) {
    var wrap = h("div", "relative");
    var img = h("img", "w-full h-auto max-h-[420px] object-cover rounded-3xl shadow-sm block");
    img.setAttribute("src", absolute(b.image));
    img.setAttribute("alt", b.title || b.sponsor || "");
    img.setAttribute("loading", "lazy");
    wrap.appendChild(img);
    var tag = sponsorTag(b, true);
    if (tag) { tag.className += " absolute top-3 left-3"; wrap.appendChild(tag); }
    return linkWrap(b, wrap, "block");
  }
  function renderBanner(b, narrow) {
    var theme = THEMES[b.theme] || THEMES.green;
    var box = h("div", "relative overflow-hidden rounded-[2rem] " + (narrow ? "min-h-[260px]" : "min-h-[220px] md:min-h-[300px]") +
      " flex items-end " + (b.image ? "bg-gray-900 text-white" : theme.box));
    if (b.image) {
      var img = h("img", "absolute inset-0 w-full h-full object-cover");
      img.setAttribute("src", absolute(b.image)); img.setAttribute("alt", ""); img.setAttribute("loading", "lazy");
      box.appendChild(img);
      box.appendChild(h("div", "absolute inset-0 bg-gradient-to-t from-black/80 via-black/30 to-transparent"));
    }
    var c = h("div", "relative z-10 p-6 " + (narrow ? "" : "md:p-10 ") + "w-full");
    var tag = sponsorTag(b, true); if (tag) c.appendChild(tag);
    if (b.title) c.appendChild(h("h3", "font-extrabold mt-2 leading-tight " + (narrow ? "text-xl" : "text-2xl md:text-4xl"), b.title));
    if (b.body) { var p = h("p", "mt-2 text-sm md:text-base " + (b.image ? "text-white/85" : theme.muted), b.body); p.style.whiteSpace = "pre-line"; c.appendChild(p); }
    var btn = button(b, b.image ? THEMES.green : theme); if (btn) c.appendChild(btn);
    box.appendChild(c);
    return linkWrap(b, box, "block");
  }
  function renderAnnouncement(b, narrow) {
    var theme = THEMES[b.theme] || THEMES.green;
    var box = h("div", "rounded-3xl p-6 " + (narrow ? "" : "md:p-8 md:flex md:items-center md:justify-between md:gap-8 ") + theme.box);
    var t = h("div", "min-w-0");
    var tag = sponsorTag(b, b.theme === "green" || b.theme === "dark"); if (tag) t.appendChild(tag);
    if (b.title) t.appendChild(h("h3", "font-extrabold leading-tight mt-1 " + (narrow ? "text-lg" : "text-xl md:text-2xl"), b.title));
    if (b.body) { var p = h("p", "mt-2 text-sm " + theme.muted, b.body); p.style.whiteSpace = "pre-line"; t.appendChild(p); }
    box.appendChild(t);
    var btn = button(b, theme); if (btn) { btn.className += " shrink-0"; box.appendChild(btn); }
    return linkWrap(b, box, "block");
  }
  function renderCards(b) {
    var sec = h("section", "py-16 md:py-20 px-6 md:px-16 text-center");
    if (b.title) sec.appendChild(h("h2", "text-3xl md:text-4xl font-extrabold mb-4 text-gray-900", b.title));
    if (b.body) sec.appendChild(h("p", "text-gray-500 max-w-2xl mx-auto mb-10", b.body));
    var n = Math.min(Math.max((b.items || []).length, 1), 4);
    var cols = { 1: "md:grid-cols-1", 2: "md:grid-cols-2", 3: "md:grid-cols-3", 4: "md:grid-cols-4" }[n];
    var grid = h("div", "grid grid-cols-1 sm:grid-cols-2 " + cols + " gap-10 md:gap-14 max-w-6xl mx-auto mt-10");
    (b.items || []).forEach(function (it) {
      var card = h("div", "");
      var circ = h("div", "w-20 h-20 md:w-24 md:h-24 rounded-full bg-[#9df6b9] mx-auto flex items-center justify-center mb-6 shadow-xl");
      circ.appendChild(h("span", "material-symbols-outlined text-4xl text-[#00210f]", it.icon || "star"));
      card.appendChild(circ);
      card.appendChild(h("h3", "text-xl font-bold mb-3 text-gray-900", it.title));
      if (it.text) card.appendChild(h("p", "text-gray-500 text-sm md:text-base max-w-xs mx-auto leading-relaxed", it.text));
      grid.appendChild(card);
    });
    sec.appendChild(grid);
    return sec;
  }
  function renderBlock(b, narrow) {
    if (b.kind === "image" && b.image) return renderImage(b);
    if (b.kind === "banner") return renderBanner(b, narrow);
    if (b.kind === "announcement") return renderAnnouncement(b, narrow);
    if (b.kind === "cards") return renderCards(b);
    return null;
  }
  // ── Aperçu interne des emplacements (équipe uniquement) ──────────
  // Lien du back-office : page.html?emplacements=1 (mémorisé pour la session,
  // ?emplacements=0 pour quitter). Ne s'active que pour un compte admin/staff ;
  // le public ne voit jamais d'emplacement vide. Pas de compteur en aperçu.
  function previewOn() {
    try {
      var m = /[?&]emplacements=([01])/.exec(location.search);
      if (m) { if (m[1] === "1") sessionStorage.setItem("goevent_zone_preview", "1"); else sessionStorage.removeItem("goevent_zone_preview"); }
      if (sessionStorage.getItem("goevent_zone_preview") !== "1") return false;
      var u = JSON.parse(localStorage.getItem("bt_user") || "null");
      return !!u && (u.role === "admin" || u.role === "staff");
    } catch (e) { return false; }
  }
  var PREVIEW = previewOn();
  var PV_STYLE = "border:2px dashed #f59e0b;border-radius:24px;background:rgba(254,243,199,.55);color:#92400e;" +
    "padding:18px;text-align:center;font:600 13px/1.4 Manrope,system-ui,sans-serif";
  function previewTag(label, key, state) {
    var t = h("div", "", "");
    t.setAttribute("style", "display:inline-block;background:#f59e0b;color:#fff;font:800 11px/1.3 Manrope,system-ui,sans-serif;" +
      "padding:4px 10px;border-radius:999px;margin-bottom:6px");
    t.textContent = "Emplacement · " + (label || key) + (state ? " · " + state : "");
    return t;
  }
  function previewBox(label, key, text, narrow) {
    var box = h("div", "");
    box.setAttribute("style", PV_STYLE + ";min-height:" + (narrow ? "160px" : "120px") +
      ";display:flex;flex-direction:column;align-items:center;justify-content:center");
    box.appendChild(previewTag(label, key, "libre"));
    box.appendChild(h("div", "", text));
    return box;
  }
  function previewBar() {
    var bar = h("div", "");
    bar.setAttribute("style", "position:fixed;left:50%;bottom:16px;transform:translateX(-50%);z-index:9999;background:#111827;color:#fff;" +
      "font:600 13px/1.3 Manrope,system-ui,sans-serif;padding:10px 16px;border-radius:999px;box-shadow:0 10px 30px rgba(0,0,0,.3);" +
      "display:flex;gap:12px;align-items:center;max-width:calc(100vw - 32px)");
    bar.appendChild(h("span", "", "Aperçu des emplacements (visible par l'équipe uniquement)"));
    var q = h("a", "", "Quitter");
    q.setAttribute("href", location.pathname + "?emplacements=0");
    q.setAttribute("style", "color:#fecb00;font-weight:800");
    bar.appendChild(q);
    document.body.appendChild(bar);
  }
  function infeedCard(b) {
    // Avec image : carte blanche façon fiche événement. Sans image : carte pleine à la couleur du thème.
    var theme = THEMES[b.theme] || THEMES.green;
    var card = h("div", "rounded-2xl overflow-hidden shadow-sm flex flex-col h-full " +
      (b.image ? "bg-white border border-gray-100 text-gray-900" : theme.box + " min-h-[260px]"));
    if (b.image) {
      var img = h("img", "w-full aspect-[4/3] object-cover");
      img.setAttribute("src", absolute(b.image)); img.setAttribute("alt", b.title || ""); img.setAttribute("loading", "lazy");
      card.appendChild(img);
    }
    var c = h("div", "p-5 flex-1 flex flex-col " + (b.image ? "" : "md:p-6"));
    c.appendChild(h("span", "text-[10px] font-black uppercase tracking-widest " + (b.image ? "text-gray-400" : "opacity-70"),
      "Sponsorisé" + (b.sponsor ? " · " + b.sponsor : "")));
    if (!b.image) c.appendChild(h("span", "material-symbols-outlined text-4xl mt-4 opacity-90", "campaign"));
    if (b.title) c.appendChild(h("h3", "font-extrabold mt-2 leading-tight " + (b.image ? "text-lg" : "text-2xl"), b.title));
    if (b.body) {
      var p = h("p", "text-sm mt-2 " + (b.image ? "text-gray-500" : theme.muted), b.body);
      p.style.whiteSpace = "pre-line"; c.appendChild(p);
    }
    if (b.link_url) {
      var cta = h("span", "mt-auto pt-5", "");
      cta.appendChild(h("span", "inline-block px-5 py-2.5 rounded-full text-sm font-black " + (b.image ? THEMES.green.box : theme.btn),
        (b.link_label || "En savoir plus") + " →"));
      c.appendChild(cta);
    }
    card.appendChild(c);
    return linkWrap(b, card, "block h-full");
  }
  function markSeen(ids) {
    var fresh = ids.filter(function (id) { return !seen[id]; });
    fresh.forEach(function (id) { seen[id] = 1; });
    beacon("i", fresh);
  }
  // ── Bandeau d'annonce global (zone global.top) ─────────────────
  var TOPBAR_PAGES = ["", "index", "event", "event_detail", "follow", "partenaire", "a_propos", "centre_aide", "cgu",
                      "confidentialite", "remboursements", "feedback", "login", "signup", "simulateur"];
  var TOPBAR_DAYS = 7;
  function topbarAllowed() {
    var p = location.pathname.replace(/\/+$/, "");
    if (/^\/e\/[^/]+$/.test(p)) return true;                        // fiche événement /e/<slug>
    if (/\/simulateur(\/index\.html)?$/.test(p)) return true;
    return TOPBAR_PAGES.indexOf(p.split("/").pop().replace(/\.html$/, "")) >= 0;
  }
  function topbarKey(b) { return "goevent_topbar_closed:" + b.id + ":" + (b.rev || 0); }
  function topbarClosed(b) {
    try { var t = parseInt(localStorage.getItem(topbarKey(b)) || "0", 10); return t > Date.now(); } catch (e) { return false; }
  }
  var shifted = [], bodyPad = null;
  function topbarShift(h0) {
    // Les en-têtes fixes / collants du haut descendent sous le bandeau (pas les tiroirs plein écran)
    shifted.forEach(function (s) { s.el.style.top = s.top; });
    shifted = [];
    if (bodyPad === null) bodyPad = { inline: document.body.style.paddingTop, px: parseFloat(getComputedStyle(document.body).paddingTop) || 0 };
    document.body.style.paddingTop = h0 ? (bodyPad.px + h0) + "px" : bodyPad.inline;
    if (!h0) return;
    var cands = document.querySelectorAll("body > *, #navbar > *, header, nav");
    for (var i = 0; i < cands.length; i++) {
      var n = cands[i];
      if (n.id === "goevent-topbar" || n.closest("#goevent-topbar")) continue;
      var cs = getComputedStyle(n);
      if ((cs.position !== "fixed" && cs.position !== "sticky") || cs.display === "none") continue;
      if (parseFloat(cs.top) !== 0 || n.getBoundingClientRect().height > window.innerHeight * 0.5) continue;
      shifted.push({ el: n, top: n.style.top });
      n.style.top = h0 + "px";
    }
  }
  function renderTopbar(el, blocks) {
    var b = blocks.filter(function (x) { return !topbarClosed(x); })[0];
    el.innerHTML = "";
    if (!b) { el.classList.add("hidden"); topbarShift(0); return; }
    var theme = { green: ["#00502c", "#fff", "#fff", "#00502c"], dark: ["#111827", "#fff", "#fecb00", "#111827"],
                  light: ["#fff", "#111827", "#00502c", "#fff"], gold: ["#fecb00", "#111827", "#111827", "#fecb00"] }[b.theme] ||
                ["#00502c", "#fff", "#fff", "#00502c"];
    var bar = h("div", "");
    bar.id = "goevent-topbar";
    bar.setAttribute("role", "region");
    bar.setAttribute("aria-label", "Annonce");
    bar.setAttribute("style", "position:fixed;top:0;left:0;right:0;z-index:1001;background:" + theme[0] + ";color:" + theme[1] +
      ";font:600 13px/1.3 'Plus Jakarta Sans',Manrope,system-ui,sans-serif;display:flex;align-items:center;gap:8px;" +
      "min-height:38px;padding:0 6px 0 16px;box-shadow:0 1px 0 rgba(0,0,0,.08)" + (b.theme === "light" ? ";border-bottom:1px solid #e5e7eb" : ""));
    var line = h("span", "");
    line.setAttribute("style", "flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-align:center;line-height:20px");
    line.appendChild(h("strong", "", b.title || ""));
    if (b.body) {
      var more = h("span", "goevent-topbar-more", (b.title ? " — " : "") + b.body.replace(/\s+/g, " "));
      more.style.fontWeight = "500";
      line.appendChild(more);
    }
    if (b.link_url && b.link_label) {
      var chip = h("span", "goevent-topbar-more", b.link_label + " →");
      chip.setAttribute("style", "display:inline-block;margin-left:10px;padding:0 10px;line-height:20px;border-radius:999px;font-weight:800;background:" + theme[2] + ";color:" + theme[3]);
      line.appendChild(chip);
    }
    var body = linkWrap(b, line, "");
    body.setAttribute("style", "flex:1;min-width:0;display:flex;color:inherit;text-decoration:none;padding:9px 0");
    var x = h("button", "", "×");
    x.setAttribute("type", "button");
    x.setAttribute("aria-label", "Fermer l'annonce");
    x.setAttribute("style", "flex:none;width:32px;height:32px;border:0;border-radius:999px;background:transparent;color:inherit;" +
      "font:400 22px/1 system-ui,sans-serif;cursor:pointer");
    x.addEventListener("click", function (e) {
      e.preventDefault();
      try { localStorage.setItem(topbarKey(b), String(Date.now() + TOPBAR_DAYS * 86400000)); } catch (err) {}
      renderTopbar(el, blocks);
    });
    bar.appendChild(body);
    bar.appendChild(x);
    if (!document.getElementById("goevent-topbar-css")) {
      var css = h("style", "");
      css.id = "goevent-topbar-css";
      css.textContent = "@media (max-width:639px){#goevent-topbar .goevent-topbar-more{display:none}}";
      document.head.appendChild(css);
    }
    el.appendChild(bar);
    el.classList.remove("hidden");
    var place = function () { topbarShift(bar.getBoundingClientRect().height); };
    place();
    if (!el._topbarResize) { el._topbarResize = true; window.addEventListener("resize", function () { if (el.firstChild) place(); }); }
    if (!PREVIEW) markSeen([b.id]);
  }
  function renderZone(el, key) {
    var z = zonesData && zonesData.zones[key];
    if (!z || z.mode === "infeed") return;
    if (key === "global.top" && (z.blocks || []).length && !PREVIEW) return renderTopbar(el, z.blocks);
    var narrow = el.getAttribute("data-zone-layout") === "narrow";
    var blocks = z.blocks || [];
    if (!blocks.length) {
      if (z.empty === "static") {                       // contenu HTML d'origine conservé
        if (PREVIEW) {
          el.setAttribute("style", "outline:2px dashed #f59e0b;outline-offset:-2px;position:relative");
          var tag = previewTag(z.label, key, "libre : section d'origine affichée");
          tag.style.cssText += ";position:absolute;top:8px;left:8px;z-index:5";
          el.appendChild(tag);
        }
        return;
      }
      el.innerHTML = "";
      if (PREVIEW) {
        var pw = h("div", el.getAttribute("data-zone-pad") || "");
        pw.appendChild(previewBox(z.label, key, "Rien n'est affiché au public tant qu'aucun bloc n'est en ligne.", narrow));
        el.appendChild(pw);
        el.classList.remove("hidden");
      }
      else el.classList.add("hidden");                  // vide = rien au public
      return;
    }
    if (z.mode === "rotate") blocks = [blocks[Math.floor(Math.random() * blocks.length)]];
    el.innerHTML = "";
    var wrap = h("div", "space-y-6 " + (el.getAttribute("data-zone-pad") || ""));
    blocks.forEach(function (b) { var n = renderBlock(b, narrow); if (n) wrap.appendChild(n); });
    if (PREVIEW) wrap.insertBefore(previewTag(z.label, key, blocks.length + " bloc(s)"), wrap.firstChild);
    el.appendChild(wrap);
    el.classList.remove("hidden");
    if (!PREVIEW) markSeen(blocks.map(function (b) { return b.id; }));
  }
  // Grilles d'événements : insère les cartes sponsorisées toutes les N cartes.
  // À rappeler après chaque rendu de la grille (filtres, pagination).
  function infeed(container, key) {
    key = key || container.getAttribute("data-zone-infeed");
    if (!zonesData) { pendingInfeed.push([container, key]); return; }
    var z = zonesData.zones[key];
    var old = container.querySelectorAll("[data-zone-card]");
    for (var i = 0; i < old.length; i++) old[i].parentNode.removeChild(old[i]);
    if (!z) return;
    var every = z.every || 6, cards = [], shown = [];
    for (i = 0; i < container.children.length; i++) cards.push(container.children[i]);
    if (!z.blocks || !z.blocks.length) {
      if (!PREVIEW) return;                             // vide = rien au public
      for (i = every; i <= cards.length; i += every) {
        var ph = previewBox(z.label, key, "Carte sponsorisée insérée ici (toutes les " + every + " cartes).", true);
        ph.setAttribute("data-zone-card", "");
        cards[i - 1].parentNode.insertBefore(ph, cards[i - 1].nextSibling);
      }
      return;
    }
    var start = Math.floor(Math.random() * z.blocks.length), k = 0;
    for (i = every; i <= cards.length; i += every) {
      var b = z.blocks[(start + k++) % z.blocks.length];
      var node = infeedCard(b);
      node.setAttribute("data-zone-card", "");
      cards[i - 1].parentNode.insertBefore(node, cards[i - 1].nextSibling);
      shown.push(b.id);
    }
    if (!PREVIEW) markSeen(shown);
  }
  function zones() {
    var els = document.querySelectorAll("[data-zone]"), infeeds = document.querySelectorAll("[data-zone-infeed]");
    var keys = {};
    for (var i = 0; i < els.length; i++) keys[els[i].getAttribute("data-zone")] = 1;
    for (i = 0; i < infeeds.length; i++) keys[infeeds[i].getAttribute("data-zone-infeed")] = 1;
    var list = Object.keys(keys).sort();
    if (!list.length) return;
    getJSON("/zones?keys=" + encodeURIComponent(list.join(",")), "goevent_zones:" + list.join(",") + ":" + bust()).then(function (data) {
      if (!data || !data.zones) return;
      zonesData = data;
      for (var j = 0; j < els.length; j++) renderZone(els[j], els[j].getAttribute("data-zone"));
      var pend = pendingInfeed; pendingInfeed = [];
      pend.forEach(function (p) { infeed(p[0], p[1]); });
      for (j = 0; j < infeeds.length; j++) if (infeeds[j].children.length) infeed(infeeds[j]);
    });
  }

  function start() {
    if (topbarAllowed() && !document.querySelector('[data-zone="global.top"]')) {
      var tb = document.createElement("div");
      tb.setAttribute("data-zone", "global.top");
      tb.className = "hidden";
      document.body.insertBefore(tb, document.body.firstChild);
    }
    var pages = pagesInDom();
    var cacheKey = "goevent_cms:" + pages + ":" + bust();
    var cached = load(cacheKey);
    if (cached) { values = cached.values || {}; apply(document); }
    getJSON("/content?pages=" + encodeURIComponent(pages), null).then(function (data) {
      if (!data || !data.values) return;
      values = data.values;
      store(cacheKey, data);
      apply(document);
    });
    legalPage();
    faq();
    zones();
    if (PREVIEW && document.querySelector("[data-zone], [data-zone-infeed]")) previewBar();
    if (document.querySelector("[data-price]")) btPricing();
    // Composants rendus plus tard (drawer, encarts simulateur…)
    if (typeof MutationObserver !== "undefined") {
      var pending = false;
      new MutationObserver(function (muts) {
        if (pending || !values) return;
        for (var i = 0; i < muts.length; i++) {
          if (muts[i].addedNodes && muts[i].addedNodes.length) {
            pending = true;
            (window.requestAnimationFrame || setTimeout)(function () {
              pending = false; apply(document);
              if (window.btPricingCache) applyPrices(document, window.btPricingCache);
            });
            return;
          }
        }
      }).observe(document.body, { childList: true, subtree: true });
    }
  }

  window.btCmsValue = function (key, fallback) {
    return values && Object.prototype.hasOwnProperty.call(values, key) && values[key] !== "" ? values[key] : fallback;
  };
  window.btCmsApply = apply;
  window.btPricing = btPricing;
  window.btZonesInfeed = infeed;
  window.btApplyPrices = function (root) { if (window.btPricingCache) applyPrices(root || document, window.btPricingCache); };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
