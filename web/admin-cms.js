/* ════════════════════════════════════════════════════════════════
 * admin-cms.js — Onglet « Contenu du site » du back-office (admin.html)
 *
 *  • Textes & images : tous les textes balisés data-cms des pages publiques,
 *    les images et les éléments communs (footer, coordonnées, simulateur).
 *  • FAQ : questions/réponses du centre d'aide.
 *  • Emplacements : blocs (affiches, bannières partenaires, annonces, cartes)
 *    des zones fixes du site (core/zones.py), dates de diffusion, statistiques.
 *  • Pages légales (admin) : CGU, confidentialité, remboursements, avec historique.
 *  • Tarifs (admin) : source unique des prix affichés ET facturés (core/pricing.py).
 * Accès : capability content_manage (admin + CM) ; pages légales, tarifs et
 * montants affichés (FCFA) : admin uniquement (gardes serveur api/cms.py, api/zones.py).
 * Dépend de bt-api.js (btApi, btToast, _btEscapeHtml, btCompressImage) et de
 * hasCapability() défini dans admin.html.
 * ════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";

  var PAGE_URLS = {
    global: "index.html", index: "index.html", event: "event.html", event_detail: "event.html",
    checkout: "event.html", a_propos: "a_propos.html", partenaire: "partenaire.html",
    centre_aide: "centre_aide.html", feedback: "feedback.html", follow: "follow.html",
    login: "login.html", signup: "signup.html", simulateur: "simulateur/",
    cgu: "cgu.html", confidentialite: "confidentialite.html", remboursements: "remboursements.html",
  };
  var LEGAL = [
    { slug: "cgu", title: "Conditions Générales d'Utilisation" },
    { slug: "confidentialite", title: "Politique de Confidentialité" },
    { slug: "remboursements", title: "Politique de Remboursements" },
  ];

  var S = {
    loaded: false, sub: "texts", fields: [], byKey: {}, pages: [], canAdmin: false,
    page: "global", search: "", dirty: {}, open: {}, faq: [], faqCats: {}, legalSlug: "cgu", legal: null,
    zones: null, zonePage: "index", pricing: null,
  };

  var esc = function (s) { return typeof _btEscapeHtml === "function" ? _btEscapeHtml(s) : String(s == null ? "" : s); };
  var toast = function (m, t) { if (typeof btToast === "function") btToast(m, t || "info"); };
  function el(tag, attrs, children) {
    var n = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      if (k === "text") n.textContent = attrs[k];
      else if (k === "class") n.className = attrs[k];
      else if (k.indexOf("on") === 0) n.addEventListener(k.slice(2), attrs[k]);
      else if (attrs[k] !== null && attrs[k] !== undefined && attrs[k] !== false) n.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { if (c) n.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return n;
  }
  function bust() { try { localStorage.setItem("goevent_cms_bust", String(Date.now())); } catch (e) {} }
  function apiUrl(u) { return u && u.charAt(0) === "/" ? BT_API + u : u; }
  function fmtDate(iso) {
    if (!iso) return "—";
    var d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z");
    return d.toLocaleDateString("fr-FR", { day: "numeric", month: "short", year: "numeric" }) + " " +
      d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  }
  function canLegal() { return typeof hasCapability === "function" && hasCapability("legal_pages_manage"); }

  // ── Styles (éditeur riche, liste des pages) ──────────────────────
  function injectStyles() {
    if (document.getElementById("cms-styles")) return;
    var css =
      ".cms-rich{min-height:180px;max-height:60vh;overflow:auto;background:#fff;border:1.5px solid #e5e7eb;border-radius:.75rem;padding:14px 16px;font-size:14px;line-height:1.6;outline:none}" +
      ".cms-rich:focus{border-color:#00502c}.cms-rich h2{font-size:1.15rem;font-weight:800;color:#00502c;margin:1.1em 0 .4em}" +
      ".cms-rich h3{font-size:1rem;font-weight:700;margin:.9em 0 .3em}.cms-rich p{margin:.45em 0}.cms-rich ul{list-style:disc;padding-left:1.4em}" +
      ".cms-rich ol{list-style:decimal;padding-left:1.4em}.cms-rich a{color:#00502c;text-decoration:underline}" +
      ".cms-rich table{border-collapse:collapse;width:100%;margin:.6em 0}.cms-rich td,.cms-rich th{border:1px solid #d1fae5;padding:6px;font-size:13px}" +
      ".cms-rich .info-box{background:#f0fdf4;border-left:3px solid #00502c;padding:8px 12px;border-radius:6px;margin:.6em 0}" +
      ".cms-tool{padding:6px 10px;border-radius:8px;font-size:12px;font-weight:700;background:#f3f4f6;color:#374151}.cms-tool:hover{background:#e5e7eb}" +
      ".cms-page-btn{width:100%;text-align:left;padding:9px 12px;border-radius:10px;font-size:13px;font-weight:600;color:#374151;display:flex;gap:8px;align-items:center}" +
      ".cms-page-btn:hover{background:#f3f4f6}.cms-page-btn.active{background:#00502c;color:#fff}" +
      ".cms-sub{padding:8px 14px;border-radius:999px;font-size:13px;font-weight:700;background:#f3f4f6;color:#374151}.cms-sub.active{background:#00502c;color:#fff}" +
      ".cms-sum{list-style:none;cursor:pointer;display:flex;align-items:center;gap:10px;padding:14px 16px;user-select:none}.cms-sum::-webkit-details-marker{display:none}" +
      ".cms-sum:hover{background:#f9fafb}details[open]>.cms-sum .cms-chev{transform:rotate(90deg)}.cms-chev{transition:transform .15s}" +
      ".cms-st{font-size:10px;font-weight:900;padding:2px 8px;border-radius:999px;text-transform:uppercase;letter-spacing:.04em}" +
      ".cms-st-live{background:#dcfce7;color:#166534}.cms-st-scheduled{background:#dbeafe;color:#1e40af}.cms-st-ended{background:#f3f4f6;color:#6b7280}.cms-st-inactive{background:#fee2e2;color:#991b1b}" +
      ".cms-swatch{width:28px;height:28px;border-radius:999px;border:3px solid transparent;cursor:pointer}.cms-swatch.active{border-color:#111827}" +
      ".cms-lbl{display:block;font-size:11px;font-weight:700;color:#6b7280;text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px}";
    document.head.appendChild(el("style", { id: "cms-styles", text: css }));
  }

  // ── Éditeur riche minimal (contenteditable + source HTML) ────────
  function richEditor(tools) {
    var area = el("div", { class: "cms-rich", contenteditable: "true" });
    var src = el("textarea", { class: "input-field font-mono text-xs hidden", rows: "16", spellcheck: "false" });
    var bar = el("div", { class: "flex flex-wrap gap-1 mb-2" });
    var defs = {
      h2: ["Titre", function () { document.execCommand("formatBlock", false, "<h2>"); }],
      h3: ["Sous-titre", function () { document.execCommand("formatBlock", false, "<h3>"); }],
      p: ["Paragraphe", function () { document.execCommand("formatBlock", false, "<p>"); }],
      b: ["Gras", function () { document.execCommand("bold"); }],
      i: ["Italique", function () { document.execCommand("italic"); }],
      ul: ["• Liste", function () { document.execCommand("insertUnorderedList"); }],
      ol: ["1. Liste", function () { document.execCommand("insertOrderedList"); }],
      a: ["Lien", function () {
        var url = window.prompt("Adresse du lien (https://…, mailto:… ou page du site, ex. cgu.html)");
        if (!url) return;
        url = url.trim();
        if (/^[a-z][a-z0-9+.-]*:/i.test(url) && !/^(https?:|mailto:|tel:)/i.test(url)) { toast("Lien refusé", "error"); return; }
        document.execCommand("createLink", false, url);
      }],
      clear: ["Effacer le format", function () { document.execCommand("removeFormat"); }],
    };
    tools.forEach(function (t) {
      bar.appendChild(el("button", { type: "button", class: "cms-tool", text: defs[t][0],
        onmousedown: function (e) { e.preventDefault(); }, onclick: function () { area.focus(); defs[t][1](); } }));
    });
    var sourceMode = false;
    var toggle = el("button", { type: "button", class: "cms-tool ml-auto", text: "Code HTML", onclick: function () {
      sourceMode = !sourceMode;
      if (sourceMode) { src.value = area.innerHTML; } else { area.innerHTML = src.value; }
      area.classList.toggle("hidden", sourceMode); src.classList.toggle("hidden", !sourceMode);
      toggle.textContent = sourceMode ? "Mise en forme" : "Code HTML";
    } });
    bar.appendChild(toggle);
    var wrap = el("div", {}, [bar, area, src]);
    return {
      node: wrap,
      get: function () { return sourceMode ? src.value : area.innerHTML; },
      set: function (html) { area.innerHTML = html || ""; src.value = html || ""; },
    };
  }

  // ════════════════════════════════════════════════════════════════
  // SHELL
  // ════════════════════════════════════════════════════════════════
  function shell() {
    var sec = document.getElementById("section-content");
    sec.innerHTML = "";
    sec.appendChild(el("div", { class: "flex flex-wrap justify-between items-start gap-3 mb-5" }, [
      el("div", {}, [
        el("h2", { class: "text-xl font-black text-gray-900", text: "Contenu du site" }),
        el("p", { class: "text-xs text-gray-500 mt-1", text:
          "Textes, images, emplacements (annonces, pubs partenaires), FAQ, pages légales et tarifs du site public. Une modification enregistrée est visible en ligne en moins d'une minute (immédiatement pour vous)." }),
      ]),
    ]));
    var tabs = el("div", { class: "flex gap-2 mb-5 flex-wrap", id: "cms-subtabs" });
    [["texts", "Textes & images"], ["zones", "Emplacements"], ["faq", "FAQ"], ["legal", "Pages légales"], ["pricing", "Tarifs"]].forEach(function (t) {
      if (t[0] === "legal" && !canLegal()) return;
      if (t[0] === "pricing" && !canPricing()) return;
      tabs.appendChild(el("button", { class: "cms-sub", "data-sub": t[0], text: t[1], onclick: function () { switchSub(t[0]); } }));
    });
    sec.appendChild(tabs);
    sec.appendChild(el("div", { id: "cms-panel" }));
    sec.appendChild(el("div", { id: "cms-savebar",
      class: "hidden fixed bottom-4 left-1/2 -translate-x-1/2 z-[900] bg-gray-900 text-white rounded-2xl shadow-2xl px-4 py-3 flex items-center gap-3 text-sm" }, [
      el("span", { id: "cms-dirty-count", class: "font-bold" }),
      el("button", { class: "action-btn text-xs", style: "background:#374151;color:#fff", text: "Annuler", onclick: discardChanges }),
      el("button", { class: "action-btn btn-primary text-xs", id: "cms-save-btn", text: "Enregistrer", onclick: saveChanges }),
    ]));
  }

  function switchSub(sub) {
    if (Object.keys(S.dirty).length && sub !== "texts" &&
        !window.confirm("Des modifications de textes ne sont pas enregistrées. Continuer sans enregistrer ?")) return;
    if (sub !== "texts") S.dirty = {};
    S.sub = sub;
    document.querySelectorAll("#cms-subtabs .cms-sub").forEach(function (b) { b.classList.toggle("active", b.getAttribute("data-sub") === sub); });
    updateSaveBar();
    if (sub === "texts") renderTexts();
    if (sub === "zones") { if (S.zones) renderZones(); loadZones(); }
    if (sub === "faq") loadFaq();
    if (sub === "pricing") loadPricingAdmin();
    if (sub === "legal") loadLegal(S.legalSlug);
  }

  // ════════════════════════════════════════════════════════════════
  // TEXTES & IMAGES
  // ════════════════════════════════════════════════════════════════
  function loadFields() {
    return btApi.get("/admin/cms/fields").then(function (data) {
      S.fields = data.fields || [];
      S.pages = data.pages || [];
      S.canAdmin = !!data.can_admin;
      S.byKey = {};
      S.fields.forEach(function (f) { S.byKey[f.key] = f; });
    });
  }

  function currentValue(f) {
    if (Object.prototype.hasOwnProperty.call(S.dirty, f.key)) return S.dirty[f.key] === null ? f.default : S.dirty[f.key];
    return f.overridden ? f.value : f.default;
  }

  // Option A : choix de la page en haut + sections repliables (fini le défilement jusqu'en bas)
  function pageTitle(page) { return (S.pages.filter(function (p) { return p.page === page; })[0] || {}).title || page; }
  function modifiedCount(list) { return list.filter(function (f) { return f.overridden || Object.prototype.hasOwnProperty.call(S.dirty, f.key); }).length; }

  function renderTexts() {
    var panel = document.getElementById("cms-panel");
    panel.innerHTML = "";
    var select = el("select", { id: "cms-page-select", class: "input-field font-bold", style: "max-width:340px",
      onchange: function (e) { S.page = e.target.value; S.search = ""; search.value = ""; renderFields(); } });
    var search = el("input", { class: "input-field", style: "max-width:340px", placeholder: "Rechercher un texte sur tout le site…", value: S.search,
      oninput: function (e) { S.search = e.target.value; renderFields(); } });
    panel.appendChild(el("div", { class: "card p-3 mb-4 flex flex-wrap items-center gap-2" }, [
      select, search,
      el("span", { class: "flex-1" }),
      el("button", { class: "cms-tool", id: "cms-toggle-all", text: "Tout déplier", onclick: function () {
        var boxes = document.querySelectorAll("#cms-fields details");
        var open = Array.prototype.some.call(boxes, function (d) { return !d.open; });
        boxes.forEach(function (d) { d.open = open; S.open[d.getAttribute("data-sec")] = open; });
        this.textContent = open ? "Tout replier" : "Tout déplier";
      } }),
      el("a", { id: "cms-page-link", class: "cms-tool", target: "_blank", rel: "noopener", text: "Voir la page ↗" }),
    ]));
    panel.appendChild(el("div", { id: "cms-fields", class: "space-y-3" }));
    renderFields();
  }

  function renderNav() {
    var select = document.getElementById("cms-page-select");
    if (!select) return;
    select.innerHTML = "";
    S.pages.forEach(function (p) {
      var list = S.fields.filter(function (f) { return f.page === p.page; });
      var mod = modifiedCount(list);
      select.appendChild(el("option", { value: p.page, selected: p.page === S.page ? "selected" : null,
        text: p.title + " — " + p.count + " texte(s)" + (mod ? " · " + mod + " modifié(s)" : "") }));
    });
    select.disabled = !!S.search.trim();
  }

  function renderFields() {
    var box = document.getElementById("cms-fields");
    if (!box) return;
    box.innerHTML = "";
    var q = S.search.trim().toLowerCase();
    var list = S.fields.filter(function (f) {
      if (!q) return f.page === S.page;
      return (f.label + " " + f.default + " " + (f.value || "") + " " + f.key).toLowerCase().indexOf(q) >= 0;
    });
    var link = document.getElementById("cms-page-link");
    link.classList.toggle("hidden", !!q);
    link.setAttribute("href", PAGE_URLS[S.page] || "index.html");
    renderNav();
    if (q) box.appendChild(el("p", { class: "text-sm font-bold text-gray-700", text: list.length + " résultat(s) pour « " + S.search + " »" }));
    if (!list.length) {
      box.appendChild(el("div", { class: "card p-6 empty-state", text: "Aucun texte trouvé." }));
      return;
    }
    var pageInfo = S.pages.filter(function (p) { return p.page === S.page; })[0];
    if (pageInfo && pageInfo.legal && !q) {
      box.appendChild(el("div", { class: "card p-4 text-xs text-gray-600 bg-amber-50", text:
        "En-tête et liens de la page légale. Le corps du document se modifie dans l'onglet « Pages légales » (administrateur)." }));
    }
    // Regroupement par section (ordre d'apparition sur la page)
    var groups = [], byName = {};
    list.forEach(function (f) {
      var name = q ? pageTitle(f.page) + " · " + f.section : f.section;
      if (!byName[name]) { byName[name] = { name: name, fields: [] }; groups.push(byName[name]); }
      byName[name].fields.push(f);
    });
    groups.forEach(function (g, i) {
      var id = (q ? "q:" : S.page + ":") + g.name;
      var open = q ? true : (Object.prototype.hasOwnProperty.call(S.open, id) ? S.open[id] : (groups.length === 1 || i === 0));
      var mod = modifiedCount(g.fields);
      var body = el("div", { class: "space-y-3 p-3 pt-0" });
      var details = el("details", { class: "card overflow-hidden", "data-sec": id, open: open ? "open" : null }, [
        el("summary", { class: "cms-sum" }, [
          el("span", { class: "material-symbols-outlined text-gray-400 cms-chev", text: "chevron_right" }),
          el("span", { class: "flex-1 font-black text-sm text-gray-900", text: g.name }),
          mod ? el("span", { class: "text-[10px] font-black px-2 py-0.5 rounded-full bg-amber-100 text-amber-800", text: mod + " modifié(s)" }) : null,
          el("span", { class: "text-[11px] font-bold text-gray-400", text: g.fields.length + " texte(s)" }),
        ]),
        body,
      ]);
      // Les champs sont construits à l'ouverture (pages longues : DOM léger)
      var filled = false;
      var fill = function () { if (filled) return; filled = true; g.fields.forEach(function (f) { body.appendChild(fieldCard(f)); }); };
      if (open) fill();
      details.addEventListener("toggle", function () { S.open[id] = details.open; if (details.open) fill(); });
      box.appendChild(details);
    });
  }

  function fieldCard(f) {
    var locked = f.admin_only && !S.canAdmin;
    var isDirty = Object.prototype.hasOwnProperty.call(S.dirty, f.key);
    var modified = isDirty ? S.dirty[f.key] !== null : f.overridden;
    var card = el("div", { class: "card p-4" + (isDirty ? " ring-2 ring-amber-300" : "") });
    var head = el("div", { class: "flex flex-wrap items-center gap-2 mb-2" });
    if (f.page === "global" || f.kind === "image" || f.kind === "url") {
      head.appendChild(el("span", { class: "text-xs font-bold text-gray-600", text: f.page === "global" ? f.label : (f.kind === "image" ? "Image" : "Lien") }));
    }
    if (modified) head.appendChild(el("span", { class: "badge text-[10px] font-black px-2 py-0.5 rounded-full bg-amber-100 text-amber-800", text: "Modifié" }));
    if (f.admin_only) head.appendChild(el("span", { class: "text-[10px] font-black px-2 py-0.5 rounded-full bg-gray-100 text-gray-600",
      text: locked ? "🔒 Réservé à l'administrateur" : "Administrateur" }));
    if (head.childNodes.length) card.appendChild(head);

    var value = currentValue(f);
    var input;
    if (f.kind === "image") {
      var shown = (f.page === "simulateur" && value && !/^(https?:|data:|\/)/.test(value)) ? "simulateur/" + value : apiUrl(value);
      var preview = el("img", { src: shown || "", class: "w-full max-w-xs h-32 object-cover rounded-xl bg-gray-100 mb-2", alt: "" });
      var file = el("input", { type: "file", accept: "image/png,image/jpeg,image/webp", class: "text-xs", disabled: locked ? "disabled" : null,
        onchange: function (e) {
          var fl = e.target.files[0];
          if (!fl) return;
          var compress = typeof btCompressImage === "function"
            ? btCompressImage(fl, { targetBytes: 900 * 1024, maxWidth: 1920, maxHeight: 1920 })
            : Promise.reject(new Error("Compression indisponible"));
          compress.then(function (b64) { S.dirty[f.key] = b64; preview.src = b64; updateSaveBar(); card.classList.add("ring-2", "ring-amber-300"); })
            .catch(function (err) { toast(err.message || "Image refusée", "error"); });
        } });
      card.appendChild(preview);
      card.appendChild(file);
      card.appendChild(el("p", { class: "text-[11px] text-gray-400 mt-1", text: "PNG, JPEG ou WebP. Compressée automatiquement (≈ 1 Mo max)." }));
    } else {
      var multiline = f.kind === "textarea";
      input = el(multiline ? "textarea" : "input", {
        class: "input-field", rows: multiline ? String(Math.min(8, Math.max(2, Math.ceil(String(value).length / 80)))) : null,
        type: multiline ? null : (f.kind === "email" ? "email" : f.kind === "url" ? "url" : f.kind === "phone" ? "tel" : "text"),
        disabled: locked ? "disabled" : null,
        maxlength: { text: 300, textarea: 3000, url: 500, email: 254, phone: 20 }[f.kind] || 300,
        placeholder: f.default ? "" : "(vide = non affiché)",
        oninput: function (e) {
          S.dirty[f.key] = e.target.value.trim() === "" || e.target.value === f.default ? null : e.target.value;
          if (!f.overridden && S.dirty[f.key] === null) delete S.dirty[f.key];
          card.classList.add("ring-2", "ring-amber-300");
          updateSaveBar();
        },
      });
      input.value = value || "";
      card.appendChild(input);
    }
    var foot = el("div", { class: "flex flex-wrap justify-between items-start gap-2 mt-2" });
    if (modified && f.kind !== "image") {
      foot.appendChild(el("p", { class: "text-[11px] text-gray-400 flex-1 min-w-0", text: "Texte d'origine : " + (f.default || "(vide)") }));
    } else {
      foot.appendChild(el("span", { class: "flex-1" }));
    }
    if (modified && !locked) {
      foot.appendChild(el("button", { class: "text-[11px] font-bold text-primary underline", text: "Rétablir l'original", onclick: function () {
        if (f.overridden) S.dirty[f.key] = null; else delete S.dirty[f.key];
        updateSaveBar();
        renderFields();
      } }));
    }
    if (f.admin_only && /FCFA/.test(f.default)) {
      foot.appendChild(el("p", { class: "w-full text-[11px] text-amber-700", text:
        "⚠ Montant écrit en dur : les tarifs se règlent dans l'onglet « Tarifs » ; vérifiez la cohérence avec le prix facturé." }));
    }
    card.appendChild(foot);
    return card;
  }

  function updateSaveBar() {
    var n = Object.keys(S.dirty).length;
    var bar = document.getElementById("cms-savebar");
    if (!bar) return;
    bar.classList.toggle("hidden", !n || S.sub !== "texts");
    document.getElementById("cms-dirty-count").textContent = n + " modification(s) non enregistrée(s)";
  }

  function discardChanges() { S.dirty = {}; updateSaveBar(); renderFields(); }

  function saveChanges() {
    var keys = Object.keys(S.dirty);
    if (!keys.length) return;
    var btn = document.getElementById("cms-save-btn");
    btn.disabled = true; btn.textContent = "Enregistrement…";
    btApi.put("/admin/cms/fields", { values: S.dirty }).then(function (res) {
      S.dirty = {};
      bust();
      toast((res.updated.length + res.reset.length) + " texte(s) enregistré(s).", "success");
      return loadFields().then(renderTexts);
    }).catch(function (e) {
      toast(e.message || "Erreur d'enregistrement", "error");
    }).then(function () {
      btn.disabled = false; btn.textContent = "Enregistrer"; updateSaveBar();
    });
  }

  // ════════════════════════════════════════════════════════════════
  // FAQ
  // ════════════════════════════════════════════════════════════════
  function loadFaq() {
    var panel = document.getElementById("cms-panel");
    panel.innerHTML = '<div class="card p-6 empty-state">Chargement…</div>';
    btApi.get("/admin/cms/faq").then(function (data) {
      S.faq = data.items || [];
      S.faqCats = data.categories || {};
      renderFaq();
    }).catch(function (e) { panel.innerHTML = ""; panel.appendChild(el("div", { class: "card p-6 text-red-500 text-sm", text: e.message || "Erreur" })); });
  }

  function renderFaq() {
    var panel = document.getElementById("cms-panel");
    panel.innerHTML = "";
    panel.appendChild(el("div", { class: "flex flex-wrap justify-between items-center gap-3 mb-4" }, [
      el("p", { class: "text-sm text-gray-600", text: S.faq.length + " question(s). Les questions désactivées ne sont pas affichées sur le site." }),
      el("button", { class: "action-btn btn-primary flex items-center gap-2", onclick: function () { faqModal(null); } }, [
        el("span", { class: "material-symbols-outlined text-base", text: "add" }), "Nouvelle question",
      ]),
    ]));
    if (!S.faq.length) {
      panel.appendChild(el("div", { class: "card p-5 bg-emerald-50 mb-4" }, [
        el("p", { class: "text-sm font-bold text-gray-900 mb-1", text: "La FAQ affichée sur le site vient encore du fichier HTML." }),
        el("p", { class: "text-xs text-gray-600 mb-3", text:
          "Importez les questions actuelles pour les gérer ici. Pensez ensuite à corriger les réponses qui mentionnent encore le paiement cash à l'entrée (supprimé)." }),
        el("button", { class: "action-btn btn-primary text-xs", text: "Importer la FAQ actuelle du site", onclick: importFaq }),
      ]));
      return;
    }
    Object.keys(S.faqCats).forEach(function (cat) {
      var items = S.faq.filter(function (i) { return i.category === cat; });
      if (!items.length) return;
      panel.appendChild(el("p", { class: "text-[11px] font-black uppercase tracking-widest text-gray-400 pt-3 pb-1", text: S.faqCats[cat] + " (" + items.length + ")" }));
      items.forEach(function (it) {
        var idx = S.faq.indexOf(it);
        panel.appendChild(el("div", { class: "card p-4 mb-2 flex flex-wrap items-center gap-3" + (it.is_active ? "" : " opacity-60") }, [
          el("div", { class: "flex-1 min-w-[200px]" }, [
            el("p", { class: "font-bold text-sm text-gray-900", text: it.question }),
            it.is_active ? null : el("span", { class: "text-[10px] font-black text-gray-500", text: "DÉSACTIVÉE" }),
          ]),
          el("button", { class: "action-btn text-xs", style: "background:#f3f4f6;color:#374151", title: "Monter", text: "↑",
            disabled: idx === 0 ? "disabled" : null, onclick: function () { moveFaq(idx, -1); } }),
          el("button", { class: "action-btn text-xs", style: "background:#f3f4f6;color:#374151", title: "Descendre", text: "↓",
            disabled: idx === S.faq.length - 1 ? "disabled" : null, onclick: function () { moveFaq(idx, 1); } }),
          el("button", { class: "action-btn text-xs", style: "background:#ede9fe;color:#5b21b6", text: "Modifier", onclick: function () { faqModal(it); } }),
          el("button", { class: "action-btn text-xs " + (it.is_active ? "btn-danger" : "btn-success"), text: it.is_active ? "Désactiver" : "Activer",
            onclick: function () { saveFaq(it.id, { is_active: !it.is_active }); } }),
          el("button", { class: "action-btn btn-danger text-xs", text: "Supprimer", onclick: function () { deleteFaq(it); } }),
        ]));
      });
    });
  }

  function moveFaq(idx, dir) {
    var j = idx + dir;
    if (j < 0 || j >= S.faq.length) return;
    var tmp = S.faq[idx]; S.faq[idx] = S.faq[j]; S.faq[j] = tmp;
    renderFaq();
    btApi.put("/admin/cms/faq/order", { ids: S.faq.map(function (i) { return i.id; }) })
      .then(bust).catch(function (e) { toast(e.message || "Erreur", "error"); loadFaq(); });
  }

  function saveFaq(id, payload) {
    var req = id ? btApi.patch("/admin/cms/faq/" + id, payload) : btApi.post("/admin/cms/faq", payload);
    return req.then(function () { bust(); toast("FAQ enregistrée.", "success"); loadFaq(); })
      .catch(function (e) { toast(e.message || "Erreur", "error"); throw e; });
  }

  function deleteFaq(it) {
    if (!window.confirm("Supprimer définitivement la question « " + it.question + " » ?")) return;
    btApi.delete("/admin/cms/faq/" + it.id).then(function () { bust(); toast("Question supprimée.", "success"); loadFaq(); })
      .catch(function (e) { toast(e.message || "Erreur", "error"); });
  }

  function faqModal(it) {
    var overlay = el("div", { class: "modal-overlay cms-modal" });
    var editor = richEditor(["b", "i", "ul", "ol", "a", "clear"]);
    editor.set(it ? it.answer_html : "");
    var cat = el("select", { class: "input-field" });
    Object.keys(S.faqCats).forEach(function (k) {
      var o = el("option", { value: k, text: S.faqCats[k] });
      if (it && it.category === k) o.selected = true;
      cat.appendChild(o);
    });
    var question = el("input", { class: "input-field", maxlength: "300", placeholder: "Ex. Comment acheter un billet ?" });
    question.value = it ? it.question : "";
    var active = el("input", { type: "checkbox" });
    active.checked = it ? it.is_active : true;
    var close = function () { overlay.remove(); };
    var save = el("button", { class: "action-btn btn-primary", text: it ? "Enregistrer" : "Ajouter", onclick: function () {
      var payload = { category: cat.value, question: question.value.trim(), answer_html: editor.get(), is_active: active.checked };
      if (payload.question.length < 3) { toast("Question trop courte.", "error"); return; }
      save.disabled = true;
      saveFaq(it ? it.id : null, payload).then(close).catch(function () { save.disabled = false; });
    } });
    overlay.appendChild(el("div", { class: "modal-box", style: "max-width:680px" }, [
      el("div", { class: "flex justify-between items-center mb-5" }, [
        el("h3", { class: "text-lg font-black text-gray-900", text: it ? "Modifier la question" : "Nouvelle question" }),
        el("button", { class: "text-gray-400 hover:text-red-500", onclick: close }, [el("span", { class: "material-symbols-outlined", text: "close" })]),
      ]),
      el("div", { class: "space-y-4" }, [
        el("label", { class: "block text-xs font-bold text-gray-500 uppercase tracking-widest", text: "Catégorie" }), cat,
        el("label", { class: "block text-xs font-bold text-gray-500 uppercase tracking-widest", text: "Question" }), question,
        el("label", { class: "block text-xs font-bold text-gray-500 uppercase tracking-widest", text: "Réponse" }), editor.node,
        el("label", { class: "flex items-center gap-2 text-sm font-semibold text-gray-700" }, [active, "Afficher sur le site"]),
        el("div", { class: "flex justify-end gap-2 pt-2" }, [
          el("button", { class: "action-btn", style: "background:#f3f4f6;color:#374151", text: "Annuler", onclick: close }), save,
        ]),
      ]),
    ]));
    document.body.appendChild(overlay);
    overlay.addEventListener("click", function (e) { if (e.target === overlay) close(); });
  }

  function importFaq() {
    fetch("centre_aide.html", { cache: "no-store" }).then(function (r) { return r.text(); }).then(function (html) {
      var doc = new DOMParser().parseFromString(html, "text/html");
      var items = [];
      doc.querySelectorAll("[data-cms-faq] .faq-item, #faq-container .faq-item").forEach(function (n) {
        var q = n.querySelector("button span");
        var a = n.querySelector(".faq-answer");
        if (!q || !a) return;
        var inner = a.querySelector("p") ? a.querySelector("p").innerHTML : a.innerHTML;
        items.push({ category: n.getAttribute("data-cat") || "autre", question: q.textContent.trim(),
                     answer_html: inner.trim(), is_active: true });
      });
      if (!items.length) throw new Error("Aucune question trouvée dans centre_aide.html");
      if (!window.confirm(items.length + " question(s) trouvée(s). Les importer ?")) return null;
      return btApi.post("/admin/cms/faq/import", { items: items, replace: false });
    }).then(function (res) {
      if (!res) return;
      bust();
      toast(res.imported + " question(s) importée(s).", "success");
      loadFaq();
    }).catch(function (e) { toast(e.message || "Import impossible", "error"); });
  }

  // ════════════════════════════════════════════════════════════════
  // PAGES LÉGALES (admin)
  // ════════════════════════════════════════════════════════════════
  function loadLegal(slug) {
    S.legalSlug = slug;
    var panel = document.getElementById("cms-panel");
    panel.innerHTML = '<div class="card p-6 empty-state">Chargement…</div>';
    btApi.get("/admin/cms/pages/" + slug).then(function (data) { S.legal = data; renderLegal(); })
      .catch(function (e) { panel.innerHTML = ""; panel.appendChild(el("div", { class: "card p-6 text-red-500 text-sm", text: e.message || "Erreur" })); });
  }

  function renderLegal() {
    var d = S.legal, panel = document.getElementById("cms-panel");
    panel.innerHTML = "";
    var pick = el("div", { class: "flex gap-2 flex-wrap mb-4" });
    LEGAL.forEach(function (p) {
      pick.appendChild(el("button", { class: "cms-sub" + (p.slug === S.legalSlug ? " active" : ""), text: p.title,
        onclick: function () { loadLegal(p.slug); } }));
    });
    panel.appendChild(pick);

    var editor = richEditor(["h2", "h3", "p", "b", "i", "ul", "ol", "a", "clear"]);
    var title = el("input", { class: "input-field", maxlength: "200" });
    title.value = d.current ? d.current.title : d.default_title;
    var note = el("input", { class: "input-field", maxlength: "300", placeholder: "Ex. Mise à jour article 6 : suppression du paiement cash" });
    if (d.current) editor.set(d.current.body_html);

    var status = d.current
      ? "Version en ligne publiée le " + fmtDate(d.current.created_at) + "."
      : "Aucune version publiée : le site affiche le texte d'origine du fichier " + S.legalSlug + ".html. Importez-le pour le modifier.";
    var publish = el("button", { class: "action-btn btn-primary", text: "Publier cette version", onclick: function () {
      var body = editor.get();
      if (!note.value.trim() && !window.confirm("Publier sans décrire ce qui change ?")) return;
      if (!window.confirm("Publier cette version de « " + title.value + " » ? Elle sera visible immédiatement par tous les utilisateurs.")) return;
      publish.disabled = true;
      btApi.post("/admin/cms/pages/" + S.legalSlug, { title: title.value.trim(), body_html: body, note: note.value.trim() || null })
        .then(function () { bust(); toast("Version publiée.", "success"); loadLegal(S.legalSlug); })
        .catch(function (e) { toast(e.message || "Erreur", "error"); publish.disabled = false; });
    } });

    panel.appendChild(el("div", { class: "grid gap-5 lg:grid-cols-[1fr_280px]" }, [
      el("div", { class: "card p-5 space-y-4" }, [
        el("p", { class: "text-xs " + (d.current ? "text-gray-500" : "text-amber-700 font-semibold"), text: status }),
        el("div", { class: "flex flex-wrap gap-2" }, [
          el("button", { class: "action-btn text-xs", style: "background:#dbeafe;color:#1e40af", text: "Importer le texte actuel du site", onclick: function () {
            fetch(S.legalSlug + ".html", { cache: "no-store" }).then(function (r) { return r.text(); }).then(function (html) {
              var doc = new DOMParser().parseFromString(html, "text/html");
              var box = doc.querySelector("[data-cms-page]") || doc.querySelector(".legal-content");
              if (!box) throw new Error("Contenu introuvable");
              if (editor.get().trim() && !window.confirm("Remplacer le contenu de l'éditeur ?")) return;
              editor.set(box.innerHTML.trim());
              toast("Texte importé dans l'éditeur (pas encore publié).", "info");
            }).catch(function (e) { toast(e.message || "Import impossible", "error"); });
          } }),
          el("a", { class: "action-btn text-xs", style: "background:#f3f4f6;color:#374151", href: S.legalSlug + ".html", target: "_blank", rel: "noopener", text: "Voir la page ↗" }),
        ]),
        el("label", { class: "block text-xs font-bold text-gray-500 uppercase tracking-widest", text: "Titre de la version" }), title,
        editor.node,
        el("label", { class: "block text-xs font-bold text-gray-500 uppercase tracking-widest", text: "Ce qui change (historique)" }), note,
        el("div", { class: "flex justify-end" }, [publish]),
      ]),
      el("div", { class: "card p-4 h-max" }, [
        el("p", { class: "text-xs font-black uppercase tracking-widest text-gray-400 mb-3", text: "Historique (" + d.revisions.length + ")" }),
        d.revisions.length ? el("div", { class: "space-y-3" }, d.revisions.map(function (r, i) {
          return el("div", { class: "border-b border-gray-100 pb-2" }, [
            el("p", { class: "text-xs font-bold text-gray-800", text: fmtDate(r.created_at) + (i === 0 ? " · en ligne" : "") }),
            el("p", { class: "text-[11px] text-gray-500", text: r.author + (r.note ? " — " + r.note : "") }),
            i === 0 ? null : el("button", { class: "text-[11px] font-bold text-primary underline", text: "Charger dans l'éditeur", onclick: function () {
              btApi.get("/admin/cms/pages/" + S.legalSlug + "/revisions/" + r.id).then(function (rev) {
                editor.set(rev.body_html); title.value = rev.title;
                note.value = "Restauration de la version du " + fmtDate(rev.created_at);
                toast("Ancienne version chargée : publiez-la pour la remettre en ligne.", "info");
              }).catch(function (e) { toast(e.message || "Erreur", "error"); });
            } }),
          ]);
        })) : el("p", { class: "text-xs text-gray-400", text: "Aucune version publiée." }),
      ]),
    ]));
  }

  // ════════════════════════════════════════════════════════════════
  // EMPLACEMENTS (zones : affiches, bannières, annonces, cartes)
  // ════════════════════════════════════════════════════════════════
  var MODES = {
    rotate: ["Rotation", "Un bloc tiré au hasard à chaque affichage : idéal pour plusieurs partenaires."],
    stack: ["Liste", "Tous les blocs s'affichent, dans l'ordre."],
    infeed: ["Dans la liste", "Carte insérée entre les événements."],
  };
  var STATUS = { live: "En ligne", scheduled: "Programmé", ended: "Terminé", inactive: "Désactivé" };
  var THEME_COLORS = { green: "#00502c", dark: "#111827", light: "#f3f4f6", gold: "#f59e0b" };
  var THEME_NAMES = { green: "Vert GoEvent", dark: "Sombre", light: "Clair", gold: "Or" };

  function canPricing() { return typeof hasCapability === "function" && hasCapability("pricing_manage"); }
  function fmtFcfa(v) { return v === null || v === undefined ? "Sur devis" : Math.round(v).toLocaleString("fr-FR") + " FCFA"; }
  function toLocalInput(iso) {
    if (!iso) return "";
    var d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z");
    var pad = function (n) { return (n < 10 ? "0" : "") + n; };
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + "T" + pad(d.getHours()) + ":" + pad(d.getMinutes());
  }
  function fromLocalInput(v) { return v ? new Date(v).toISOString() : null; }

  function loadZones() {
    var panel = document.getElementById("cms-panel");
    if (!S.zones) panel.innerHTML = '<div class="card p-6 empty-state">Chargement…</div>';
    return Promise.all([btApi.get("/admin/zones"), loadPublicPricing()]).then(function (r) {
      S.zones = r[0];
      if (S.sub === "zones") renderZones();
    }).catch(function (e) { panel.innerHTML = ""; panel.appendChild(el("div", { class: "card p-6 text-red-500 text-sm", text: e.message || "Erreur" })); });
  }

  function renderZones() {
    var d = S.zones, panel = document.getElementById("cms-panel");
    panel.innerHTML = "";
    panel.appendChild(el("p", { class: "text-xs text-gray-500 mb-3", text:
      "Les emplacements sont fixes (le design reste propre sur mobile). Dans chacun, ajoutez des affiches, bannières partenaires, annonces ou cartes, avec dates de diffusion et statistiques." }));
    var pick = el("div", { class: "flex gap-2 flex-wrap mb-4" });
    Object.keys(d.pages).forEach(function (pg) {
      var zs = d.zones.filter(function (z) { return z.page === pg; });
      var live = zs.reduce(function (n, z) { return n + z.blocks.filter(function (b) { return b.status === "live"; }).length; }, 0);
      pick.appendChild(el("button", { class: "cms-sub" + (pg === S.zonePage ? " active" : ""), onclick: function () { S.zonePage = pg; renderZones(); } }, [
        d.pages[pg] + " ", el("span", { class: "opacity-70 text-[11px]", text: "(" + zs.length + " empl. · " + live + " en ligne)" }),
      ]));
    });
    panel.appendChild(pick);
    d.zones.filter(function (z) { return z.page === S.zonePage; }).forEach(function (z) { panel.appendChild(zoneCard(z)); });
  }

  function zoneCard(z) {
    var live = z.blocks.filter(function (b) { return b.status === "live"; }).length;
    var price = z.format && S.pricing ? S.pricing.ad_formats[z.format] : undefined;
    var head = el("summary", { class: "cms-sum" }, [
      el("span", { class: "material-symbols-outlined text-gray-400 cms-chev", text: "chevron_right" }),
      el("div", { class: "flex-1 min-w-0" }, [
        el("p", { class: "font-black text-sm text-gray-900", text: z.label }),
        el("p", { class: "text-[11px] text-gray-500", text: z.description }),
      ]),
      el("span", { class: "text-[10px] font-black px-2 py-0.5 rounded-full bg-gray-100 text-gray-600", title: MODES[z.mode][1], text: MODES[z.mode][0] }),
      el("span", { class: "cms-st " + (live ? "cms-st-live" : "cms-st-ended"), text: live ? live + " en ligne" : "Vide" }),
    ]);
    var body = el("div", { class: "p-4 pt-0 space-y-3" });

    // Réglages : si vide / fréquence
    var settings = el("div", { class: "flex flex-wrap items-center gap-3 text-xs bg-gray-50 rounded-xl p-3" });
    if (z.empty === "static") {
      settings.appendChild(el("span", { class: "text-gray-600", text: "Si aucun bloc en ligne : la section d'origine du site reste affichée." }));
    } else {
      var sel = el("select", { class: "input-field text-xs", style: "width:auto;padding:6px 10px", onchange: function (e) {
        saveZoneSettings(z, { empty: e.target.value });
      } }, [
        el("option", { value: "house", text: "Afficher l'encart « Emplacement disponible »", selected: z.empty === "house" ? "selected" : null }),
        el("option", { value: "hide", text: "Masquer l'emplacement", selected: z.empty === "hide" ? "selected" : null }),
      ]);
      settings.appendChild(el("span", { class: "font-bold text-gray-600", text: "Si vide :" }));
      settings.appendChild(sel);
    }
    if (z.every) {
      var every = el("input", { type: "number", min: "3", max: "24", value: String(z.every), class: "input-field text-xs", style: "width:70px;padding:6px 8px",
        onchange: function (e) {
          var n = parseInt(e.target.value, 10);
          if (!(n >= 3 && n <= 24)) { toast("Entre 3 et 24.", "error"); e.target.value = z.every; return; }
          saveZoneSettings(z, { every: n });
        } });
      settings.appendChild(el("span", { class: "font-bold text-gray-600", text: "Une carte toutes les" }));
      settings.appendChild(every);
      settings.appendChild(el("span", { class: "text-gray-600", text: "événements" }));
    }
    if (z.format) {
      settings.appendChild(el("span", { class: "ml-auto text-gray-500", text: "Tarif affiché aux partenaires : " + (price === undefined ? "…" : fmtFcfa(price) + (price ? " / mois" : "")) }));
    }
    body.appendChild(settings);

    if (!z.blocks.length) {
      body.appendChild(el("p", { class: "text-xs text-gray-400 py-2", text: "Aucun bloc. " + (z.empty === "static" ? "Le site affiche la section d'origine." : z.empty === "house" ? "Le site affiche l'encart « Emplacement disponible »." : "L'emplacement est masqué sur le site.") }));
    }
    z.blocks.forEach(function (b, i) { body.appendChild(blockRow(z, b, i)); });
    body.appendChild(el("button", { class: "action-btn btn-primary text-xs flex items-center gap-1", onclick: function () { blockModal(z, null); } }, [
      el("span", { class: "material-symbols-outlined text-base", text: "add" }), "Ajouter un bloc",
    ]));

    var id = "zone:" + z.key;
    var open = Object.prototype.hasOwnProperty.call(S.open, id) ? S.open[id] : true;
    var details = el("details", { class: "card overflow-hidden mb-3", open: open ? "open" : null }, [head, body]);
    details.addEventListener("toggle", function () { S.open[id] = details.open; });
    return details;
  }

  function blockRow(z, b, i) {
    var thumb = b.image
      ? el("img", { src: apiUrl(b.image), alt: "", class: "w-20 h-14 object-cover rounded-lg bg-gray-100 flex-shrink-0" })
      : el("div", { class: "w-20 h-14 rounded-lg flex items-center justify-center flex-shrink-0", style: "background:" + (THEME_COLORS[b.theme] || "#00502c") }, [
          el("span", { class: "material-symbols-outlined " + (b.theme === "light" ? "text-gray-700" : "text-white"), text: b.kind === "cards" ? "view_module" : b.kind === "announcement" ? "campaign" : "image" }),
        ]);
    var ctr = b.impressions ? (100 * b.clicks / b.impressions).toFixed(1).replace(".", ",") + " %" : "—";
    var dates = (b.starts_at || b.ends_at) ? (b.starts_at ? "du " + fmtDate(b.starts_at) : "") + (b.ends_at ? " au " + fmtDate(b.ends_at) : "") : "Sans limite de dates";
    var btn = function (label, style, fn, disabled) {
      return el("button", { class: "action-btn text-xs", style: style, text: label, disabled: disabled ? "disabled" : null, onclick: fn });
    };
    return el("div", { class: "border border-gray-100 rounded-xl p-3 flex flex-wrap items-center gap-3" + (b.status === "live" ? "" : " opacity-75") }, [
      thumb,
      el("div", { class: "flex-1 min-w-[180px]" }, [
        el("div", { class: "flex flex-wrap items-center gap-2" }, [
          el("span", { class: "cms-st cms-st-" + b.status, text: STATUS[b.status] }),
          el("span", { class: "text-[11px] font-bold text-gray-500", text: S.zones.kinds[b.kind] }),
          b.sponsor ? el("span", { class: "text-[11px] text-gray-500", text: "· " + b.sponsor }) : null,
        ]),
        el("p", { class: "font-bold text-sm text-gray-900 mt-1 truncate", text: b.title || (b.kind === "cards" ? b.items.length + " carte(s)" : "(sans titre)") }),
        el("p", { class: "text-[11px] text-gray-400", text: dates + " · " + b.impressions.toLocaleString("fr-FR") + " affichages · " + b.clicks.toLocaleString("fr-FR") + " clics · CTR " + ctr }),
      ]),
      el("div", { class: "flex flex-wrap gap-1" }, [
        btn("↑", "background:#f3f4f6;color:#374151", function () { moveBlock(z, i, -1); }, i === 0),
        btn("↓", "background:#f3f4f6;color:#374151", function () { moveBlock(z, i, 1); }, i === z.blocks.length - 1),
        btn("Modifier", "background:#ede9fe;color:#5b21b6", function () { blockModal(z, b); }),
        el("button", { class: "action-btn text-xs " + (b.is_active ? "btn-danger" : "btn-success"), text: b.is_active ? "Désactiver" : "Activer",
          onclick: function () { patchBlock(b.id, { is_active: !b.is_active }, b.is_active ? "Bloc désactivé." : "Bloc activé."); } }),
        el("button", { class: "action-btn btn-danger text-xs", text: "Supprimer", onclick: function () {
          if (!window.confirm("Supprimer définitivement ce bloc" + (b.title ? " « " + b.title + " »" : "") + " ? Ses statistiques seront perdues.")) return;
          btApi.delete("/admin/zones/blocks/" + b.id).then(function () { bust(); toast("Bloc supprimé.", "success"); loadZones(); })
            .catch(function (e) { toast(e.message || "Erreur", "error"); });
        } }),
      ]),
    ]);
  }

  function saveZoneSettings(z, payload) {
    btApi.put("/admin/zones/" + encodeURIComponent(z.key) + "/settings", payload).then(function (s) {
      z.empty = s.empty; z.every = s.every; bust(); toast("Réglage enregistré.", "success"); renderZones();
    }).catch(function (e) { toast(e.message || "Erreur", "error"); loadZones(); });
  }

  function patchBlock(id, payload, msg) {
    return btApi.patch("/admin/zones/blocks/" + id, payload).then(function () { bust(); toast(msg || "Bloc enregistré.", "success"); return loadZones(); })
      .catch(function (e) { toast(e.message || "Erreur", "error"); throw e; });
  }

  function moveBlock(z, i, dir) {
    var j = i + dir;
    if (j < 0 || j >= z.blocks.length) return;
    var tmp = z.blocks[i]; z.blocks[i] = z.blocks[j]; z.blocks[j] = tmp;
    renderZones();
    btApi.put("/admin/zones/" + encodeURIComponent(z.key) + "/order", { ids: z.blocks.map(function (b) { return b.id; }) })
      .then(bust).catch(function (e) { toast(e.message || "Erreur", "error"); loadZones(); });
  }

  function blockModal(z, b) {
    var d = S.zones;
    var overlay = el("div", { class: "modal-overlay cms-modal" });
    var close = function () { overlay.remove(); };
    var kind = el("select", { class: "input-field", disabled: b ? "disabled" : null });
    z.kinds.forEach(function (k) { kind.appendChild(el("option", { value: k, text: d.kinds[k], selected: b && b.kind === k ? "selected" : null })); });

    var title = el("input", { class: "input-field", maxlength: "160" });
    var text = el("textarea", { class: "input-field", rows: "3", maxlength: "1000" });
    var sponsor = el("input", { class: "input-field", maxlength: "120", placeholder: "Ex. Orange Centrafrique" });
    var linkUrl = el("input", { class: "input-field", maxlength: "500", placeholder: "https://… ou page du site (ex. partenaire.html)" });
    var linkLabel = el("input", { class: "input-field", maxlength: "60", placeholder: "Ex. En savoir plus" });
    var starts = el("input", { type: "datetime-local", class: "input-field" });
    var ends = el("input", { type: "datetime-local", class: "input-field" });
    var active = el("input", { type: "checkbox" });
    title.value = b && b.title || ""; text.value = b && b.body || ""; sponsor.value = b && b.sponsor || "";
    linkUrl.value = b && b.link_url || ""; linkLabel.value = b && b.link_label || "";
    starts.value = toLocalInput(b && b.starts_at); ends.value = toLocalInput(b && b.ends_at);
    active.checked = b ? b.is_active : true;

    // Couleur
    var theme = b && b.theme || "green";
    var swatches = el("div", { class: "flex gap-2 items-center" });
    d.themes.forEach(function (t) {
      swatches.appendChild(el("button", { type: "button", title: THEME_NAMES[t] || t, class: "cms-swatch" + (t === theme ? " active" : ""),
        style: "background:" + THEME_COLORS[t] + (t === "light" ? ";box-shadow:inset 0 0 0 1px #d1d5db" : ""),
        onclick: function () { theme = t; swatches.querySelectorAll(".cms-swatch").forEach(function (s) { s.classList.toggle("active", s.title === (THEME_NAMES[t] || t)); }); } }));
    });

    // Image : undefined = inchangée, null = retirée, data URL = nouvelle
    var newImage;
    var preview = el("img", { alt: "", class: "w-full max-h-56 object-contain rounded-xl bg-gray-100" + (b && b.image ? "" : " hidden"), src: b && b.image ? apiUrl(b.image) : "" });
    var removeImg = el("button", { type: "button", class: "text-[11px] font-bold text-red-600 underline" + (b && b.image ? "" : " hidden"), text: "Retirer l'image",
      onclick: function () { newImage = null; preview.classList.add("hidden"); removeImg.classList.add("hidden"); file.value = ""; } });
    var file = el("input", { type: "file", accept: "image/png,image/jpeg,image/webp", class: "text-xs", onchange: function (e) {
      var fl = e.target.files[0];
      if (!fl) return;
      var compress = typeof btCompressImage === "function"
        ? btCompressImage(fl, { targetBytes: 1000 * 1024, maxWidth: 1920, maxHeight: 1920 })
        : Promise.reject(new Error("Compression indisponible"));
      compress.then(function (b64) { newImage = b64; preview.src = b64; preview.classList.remove("hidden"); removeImg.classList.remove("hidden"); })
        .catch(function (err) { toast(err.message || "Image refusée", "error"); file.value = ""; });
    } });

    // Cartes
    var cards = (b && b.items ? b.items : []).map(function (c) { return { icon: c.icon, title: c.title, text: c.text }; });
    var cardsBox = el("div", { class: "space-y-2" });
    var renderCards = function () {
      cardsBox.innerHTML = "";
      cards.forEach(function (c, i) {
        var icon = el("select", { class: "input-field text-xs", style: "width:auto", onchange: function (e) { c.icon = e.target.value; iconPrev.textContent = c.icon; } });
        d.icons.forEach(function (ic) { icon.appendChild(el("option", { value: ic, text: ic, selected: c.icon === ic ? "selected" : null })); });
        if (!c.icon) c.icon = d.icons[0];
        var iconPrev = el("span", { class: "material-symbols-outlined text-primary", text: c.icon });
        var t = el("input", { class: "input-field text-sm", maxlength: "60", placeholder: "Titre de la carte", oninput: function (e) { c.title = e.target.value; } });
        var x = el("textarea", { class: "input-field text-xs", rows: "2", maxlength: "240", placeholder: "Texte", oninput: function (e) { c.text = e.target.value; } });
        t.value = c.title || ""; x.value = c.text || "";
        cardsBox.appendChild(el("div", { class: "border border-gray-100 rounded-xl p-3 space-y-2" }, [
          el("div", { class: "flex items-center gap-2" }, [
            iconPrev, icon, el("span", { class: "flex-1" }),
            el("button", { type: "button", class: "cms-tool", text: "↑", disabled: i === 0 ? "disabled" : null, onclick: function () { cards.splice(i - 1, 0, cards.splice(i, 1)[0]); renderCards(); } }),
            el("button", { type: "button", class: "cms-tool", text: "↓", disabled: i === cards.length - 1 ? "disabled" : null, onclick: function () { cards.splice(i + 1, 0, cards.splice(i, 1)[0]); renderCards(); } }),
            el("button", { type: "button", class: "cms-tool", style: "color:#b91c1c", text: "Retirer", onclick: function () { cards.splice(i, 1); renderCards(); } }),
          ]),
          t, x,
        ]));
      });
      addCard.disabled = cards.length >= 8;
    };
    var addCard = el("button", { type: "button", class: "cms-tool", text: "+ Ajouter une carte", onclick: function () {
      cards.push({ icon: d.icons[0], title: "", text: "" }); renderCards();
    } });

    var row = function (label, node, hint) {
      return el("div", { "data-row": label }, [el("label", { class: "cms-lbl", text: label }), node,
        hint ? el("p", { class: "text-[11px] text-gray-400 mt-1", text: hint }) : null]);
    };
    var rows = {
      image: row("Image / affiche", el("div", { class: "space-y-2" }, [preview, el("div", { class: "flex items-center gap-3" }, [file, removeImg])]),
        "PNG, JPEG ou WebP, compressée automatiquement. Format conseillé : bannière 1600×400, affiche 1080×1350."),
      title: row("Titre", title),
      text: row("Texte", text),
      sponsor: row("Partenaire (mention « Sponsorisé »)", sponsor, "Laisser vide pour une annonce GoEvent."),
      link: el("div", { class: "grid sm:grid-cols-2 gap-3" }, [row("Lien", linkUrl), row("Texte du bouton", linkLabel)]),
      theme: row("Couleur", swatches),
      cards: row("Cartes (8 max)", el("div", { class: "space-y-2" }, [cardsBox, addCard])),
    };
    var VISIBLE = {
      image: ["image", "title", "sponsor", "link"],
      banner: ["image", "title", "text", "sponsor", "link", "theme"],
      announcement: ["title", "text", "image", "link", "theme"],
      cards: ["title", "text", "cards"],
    };
    var refresh = function () {
      var v = VISIBLE[kind.value];
      Object.keys(rows).forEach(function (k) { rows[k].classList.toggle("hidden", v.indexOf(k) < 0); });
      kindHelp.textContent = {
        image: "Affiche plein cadre (cliquable si un lien est indiqué). Le titre sert de texte alternatif.",
        banner: "Bannière partenaire : image à gauche, titre, texte et bouton sur fond de couleur.",
        announcement: "Annonce GoEvent : titre, texte et bouton, image facultative.",
        cards: "Groupe de cartes à icône (ex. « Comment ça marche ») : ajoutez, retirez ou réordonnez les cartes.",
      }[kind.value];
    };
    var kindHelp = el("p", { class: "text-[11px] text-gray-500 mt-1" });
    kind.addEventListener("change", refresh);

    var save = el("button", { class: "action-btn btn-primary", text: b ? "Enregistrer" : "Ajouter", onclick: function () {
      var k = kind.value, v = VISIBLE[k];
      var payload = {
        title: title.value.trim() || null,
        body: v.indexOf("text") >= 0 ? (text.value.trim() || null) : null,
        sponsor: v.indexOf("sponsor") >= 0 ? (sponsor.value.trim() || null) : null,
        link_url: v.indexOf("link") >= 0 ? (linkUrl.value.trim() || null) : null,
        link_label: v.indexOf("link") >= 0 ? (linkLabel.value.trim() || null) : null,
        theme: theme, is_active: active.checked,
        starts_at: fromLocalInput(starts.value), ends_at: fromLocalInput(ends.value),
      };
      if (k === "cards") {
        var clean = cards.filter(function (c) { return (c.title || "").trim(); }).map(function (c) {
          return { icon: c.icon, title: c.title.trim(), text: (c.text || "").trim() || null };
        });
        if (!clean.length) { toast("Ajoutez au moins une carte avec un titre.", "error"); return; }
        payload.items = clean;
      }
      if (v.indexOf("image") < 0 && b && b.image) newImage = null;
      var hasImage = newImage ? true : newImage === null ? false : !!(b && b.image);
      if (k === "image" && !hasImage) { toast("Ajoutez l'image de l'affiche.", "error"); return; }
      if ((k === "banner" || k === "announcement") && !payload.title && !hasImage) { toast("Indiquez au moins un titre.", "error"); return; }
      if (payload.starts_at && payload.ends_at && payload.ends_at <= payload.starts_at) { toast("La fin doit être après le début.", "error"); return; }
      if (newImage !== undefined) payload.image = newImage;
      save.disabled = true; save.textContent = "Enregistrement…";
      var req = b ? btApi.patch("/admin/zones/blocks/" + b.id, payload)
                  : btApi.post("/admin/zones/" + encodeURIComponent(z.key) + "/blocks", Object.assign({ kind: k }, payload));
      req.then(function () { bust(); toast(b ? "Bloc enregistré." : "Bloc ajouté.", "success"); close(); loadZones(); })
        .catch(function (e) { toast(e.message || "Erreur", "error"); save.disabled = false; save.textContent = b ? "Enregistrer" : "Ajouter"; });
    } });

    overlay.appendChild(el("div", { class: "modal-box", style: "max-width:720px;max-height:92vh;overflow:auto" }, [
      el("div", { class: "flex justify-between items-start mb-5 gap-3" }, [
        el("div", {}, [
          el("h3", { class: "text-lg font-black text-gray-900", text: b ? "Modifier le bloc" : "Nouveau bloc" }),
          el("p", { class: "text-xs text-gray-500", text: z.label }),
        ]),
        el("button", { class: "text-gray-400 hover:text-red-500", onclick: close }, [el("span", { class: "material-symbols-outlined", text: "close" })]),
      ]),
      el("div", { class: "space-y-4" }, [
        el("div", {}, [el("label", { class: "cms-lbl", text: "Type de bloc" }), kind, kindHelp]),
        rows.image, rows.title, rows.text, rows.cards, rows.sponsor, rows.link, rows.theme,
        el("div", { class: "grid sm:grid-cols-2 gap-3" }, [
          row("Début de diffusion", starts, "Vide = immédiatement"),
          row("Fin de diffusion", ends, "Vide = sans fin"),
        ]),
        el("label", { class: "flex items-center gap-2 text-sm font-semibold text-gray-700" }, [active, "Actif"]),
        el("div", { class: "flex justify-end gap-2 pt-2" }, [
          el("button", { class: "action-btn", style: "background:#f3f4f6;color:#374151", text: "Annuler", onclick: close }), save,
        ]),
      ]),
    ]));
    renderCards();
    refresh();
    document.body.appendChild(overlay);
    overlay.addEventListener("click", function (e) { if (e.target === overlay) close(); });
  }

  // ════════════════════════════════════════════════════════════════
  // TARIFS (admin) — source unique : core/pricing.py
  // ════════════════════════════════════════════════════════════════
  var PRICE_GROUPS = [
    ["boost", "Boost organisateur", "Facturé à l'achat par l'organisateur (tableau de bord orga) et affiché sur la page Partenaires."],
    ["fb_pack", "Pack Facebook Ads", "Budget minimum et marge GoEvent appliqués au paiement du pack."],
    ["slot_hero", "Slot Hero (carrousel d'accueil)", "Vendu par contrat (Régie pub) : prérempli dans le formulaire de contrat et affiché aux partenaires."],
    ["ad_formats", "Emplacements publicitaires (par mois)", "Affiché sur la page Partenaires. Vide = « Sur devis »."],
    ["premium", "Services premium", "Affichage uniquement (page Partenaires)."],
  ];

  function loadPublicPricing() {
    return btApi.get("/pricing").then(function (p) { S.pricing = p; window.btPricingCache = p; updateSlotPrices(); return p; })
      .catch(function () { return null; });
  }

  function loadPricingAdmin() {
    var panel = document.getElementById("cms-panel");
    panel.innerHTML = '<div class="card p-6 empty-state">Chargement…</div>';
    btApi.get("/admin/pricing").then(renderPricing)
      .catch(function (e) { panel.innerHTML = ""; panel.appendChild(el("div", { class: "card p-6 text-red-500 text-sm", text: e.message || "Erreur" })); });
  }

  function renderPricing(d) {
    var panel = document.getElementById("cms-panel");
    panel.innerHTML = "";
    var p = d.pricing, inputs = [];
    panel.appendChild(el("div", { class: "card p-4 mb-4 bg-amber-50 text-xs text-amber-900 space-y-1" }, [
      el("p", { class: "font-bold", text: "Prix affiché = prix facturé." }),
      el("p", { text: "Ces montants alimentent à la fois les pages du site (Partenaires, tableau de bord organisateur, simulateur) et le paiement réel du Boost et du Pack Facebook. Une commande déjà payée garde son prix ; le nouveau tarif s'applique aux paiements suivants (≈ 30 s)." }),
    ]));
    PRICE_GROUPS.forEach(function (g) {
      var group = g[0], box = el("div", { class: "grid sm:grid-cols-2 lg:grid-cols-3 gap-3" });
      Object.keys(p[group]).forEach(function (key) {
        var cur = p[group][key], def = d.defaults[group][key];
        var val = cur && typeof cur === "object" ? cur.amount : cur;
        var dv = def && typeof def === "object" ? def.amount : def;
        var pct = group === "fb_pack" && key === "margin_pct";
        var input = el("input", { type: "number", min: pct ? "0" : "1", max: pct ? "100" : "10000000", step: "1", class: "input-field",
          placeholder: group === "ad_formats" ? "Sur devis" : "" });
        input.value = val === null || val === undefined ? "" : String(val);
        input.setAttribute("data-group", group); input.setAttribute("data-key", key);
        input.setAttribute("data-initial", input.value);
        inputs.push(input);
        box.appendChild(el("div", {}, [
          el("label", { class: "cms-lbl", text: d.labels[group + "." + key] || key }),
          el("div", { class: "flex items-center gap-2" }, [input, el("span", { class: "text-xs font-bold text-gray-500", text: pct ? "%" : "FCFA" })]),
          el("p", { class: "text-[11px] text-gray-400 mt-1", text: "Défaut : " + (dv === null ? "Sur devis" : pct ? dv + " %" : fmtFcfa(dv)) }),
        ]));
      });
      panel.appendChild(el("div", { class: "card p-5 mb-3" }, [
        el("p", { class: "font-black text-sm text-gray-900", text: g[1] }),
        el("p", { class: "text-[11px] text-gray-500 mb-3", text: g[2] }),
        box,
      ]));
    });
    panel.appendChild(el("div", { class: "card p-5 mb-3 bg-gray-50" }, [
      el("p", { class: "font-black text-sm text-gray-900", text: "Commission billetterie (lecture seule)" }),
      el("p", { class: "text-sm text-gray-700 mt-1", text: "Acheteur : " + String(p.commission.buyer_pct).replace(".", ",") + " % · Organisateur : " + String(p.commission.organizer_pct).replace(".", ",") + " %" }),
      el("p", { class: "text-[11px] text-gray-500 mt-1", text: "Elle engage les CGU : elle se modifie dans le code (core/config.py) en même temps que les CGU, jamais depuis le back-office." }),
    ]));
    var save = el("button", { class: "action-btn btn-primary", text: "Enregistrer les tarifs", onclick: function () {
      var payload = {}, changed = [], err = null;
      inputs.forEach(function (i) {
        if (i.value === i.getAttribute("data-initial")) return;
        var group = i.getAttribute("data-group"), key = i.getAttribute("data-key");
        var raw = i.value.trim();
        var v = raw === "" ? null : Number(raw);
        if (v === null && group !== "ad_formats") err = "« " + (d.labels[group + "." + key] || key) + " » est obligatoire.";
        else if (v !== null && (!isFinite(v) || Math.round(v) !== v)) err = "Montants entiers uniquement.";
        (payload[group] = payload[group] || {})[key] = v;
        changed.push((d.labels[group + "." + key] || key) + " : " + (i.getAttribute("data-initial") || "Sur devis") + " → " + (raw || "Sur devis"));
      });
      if (err) { toast(err, "error"); return; }
      if (!changed.length) { toast("Aucun changement.", "info"); return; }
      if (!window.confirm("Appliquer ces tarifs (site + paiements) ?\n\n" + changed.join("\n"))) return;
      save.disabled = true;
      btApi.put("/admin/pricing", { pricing: payload }).then(function (res) {
        bust();
        toast(res.changes.length + " tarif(s) mis à jour.", "success");
        S.pricing = res.pricing; window.btPricingCache = res.pricing; updateSlotPrices();
        loadPricingAdmin();
      }).catch(function (e) { toast(e.message || "Erreur", "error"); save.disabled = false; });
    } });
    panel.appendChild(el("div", { class: "flex justify-between items-center gap-3 flex-wrap" }, [
      el("p", { class: "text-[11px] text-gray-400", text: d.updated_at ? "Dernière modification : " + fmtDate(d.updated_at) : "Tarifs par défaut (jamais modifiés)." }),
      save,
    ]));
  }

  // Préréglages Slot Hero du formulaire de contrat (admin.html → Régie pub)
  function updateSlotPrices() {
    if (!S.pricing) return;
    document.querySelectorAll("[data-slot-price]").forEach(function (n) {
      var v = S.pricing.slot_hero[n.getAttribute("data-slot-price")];
      if (typeof v === "number") n.textContent = v.toLocaleString("fr-FR");
    });
  }
  window._slotPrice = function (code, fallback) {
    var v = S.pricing && S.pricing.slot_hero ? S.pricing.slot_hero[code] : null;
    return typeof v === "number" ? v : fallback;
  };

  // ════════════════════════════════════════════════════════════════
  // ENTRÉE (appelée par showTab('content'))
  // ════════════════════════════════════════════════════════════════
  window.cmsInit = function () {
    injectStyles();
    if (S.loaded) { switchSub(S.sub); return; }
    shell();
    document.getElementById("cms-panel").innerHTML = '<div class="card p-6 empty-state">Chargement…</div>';
    loadFields().then(function () { S.loaded = true; switchSub("texts"); })
      .catch(function (e) { document.getElementById("cms-panel").textContent = e.message || "Erreur de chargement"; });
  };

  // Tarifs Slot Hero du formulaire de contrat : chargés dès l'ouverture du back-office
  function preloadPricing() { if (typeof btApi !== "undefined" && typeof BT_API !== "undefined") loadPublicPricing(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", preloadPricing);
  else preloadPricing();

  window.addEventListener("beforeunload", function (e) {
    if (Object.keys(S.dirty).length) { e.preventDefault(); e.returnValue = ""; }
  });
})();
