/**
 * admin-events.js — Onglet « Événements » du back-office (admin.html), lot 5.
 * Événements publics : indicateurs, recherche, statuts, cartes, désactivation motivée.
 * API : GET /admin/events?page=… · POST /admin/events/{id}/deactivate | reactivate (admin).
 */
(function () {
  "use strict";

  var S = { status: "avenir", q: "", page: 1, items: [], data: null, timer: null, busy: false };
  var LIMIT = 20, TZ = "Africa/Bangui";
  var ST = {
    avenir: ["À venir", "bg-emerald-50 text-emerald-700"], encours: ["En cours", "bg-amber-100 text-amber-800"],
    complet: ["Complet", "bg-gray-900 text-white"], passe: ["Passé", "bg-gray-100 text-gray-500"],
    desactive: ["Désactivé", "bg-red-50 text-red-700"],
  };
  var TABS = [["avenir", "À venir"], ["encours", "En cours"], ["complet", "Complets"], ["surveiller", "À surveiller"],
              ["passe", "Passés"], ["desactive", "Désactivés"], ["tous", "Tous"]];

  function toast(m, t) { if (typeof btToast === "function") btToast(m, t || "info"); }
  function isAdmin() { var u = (typeof btAuth !== "undefined" && btAuth.getUser()) || {}; return u.role === "admin"; }
  function el(tag, attrs, children) {
    var n = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      var v = attrs[k];
      if (k === "text") n.textContent = v;
      else if (k === "class") n.className = v;
      else if (k.indexOf("on") === 0 && typeof v === "function") n.addEventListener(k.slice(2), v);
      else if (v !== null && v !== undefined && v !== false) n.setAttribute(k, v);
    });
    (children || []).forEach(function (c) { if (c) n.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return n;
  }
  function num(v) { return Math.round(v || 0).toLocaleString("fr-FR"); }
  function fcfa(v) { return num(v) + " FCFA"; }
  function when(iso) {
    if (!iso) return "—";
    var d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z");
    var s = d.toLocaleDateString("fr-FR", { timeZone: TZ, weekday: "short", day: "numeric", month: "short" }) + " · " +
      d.toLocaleTimeString("fr-FR", { timeZone: TZ, hour: "2-digit", minute: "2-digit" }).replace(":", "h");
    return s.charAt(0).toUpperCase() + s.slice(1);
  }
  function section() { return document.getElementById("section-events"); }

  // ── Rendu ──────────────────────────────────────────────────
  function kpi(label, value, sub) {
    return el("div", { class: "bg-white rounded-2xl p-4 border border-gray-100" }, [
      el("p", { class: "text-[10px] md:text-[11px] font-bold text-gray-500 uppercase tracking-widest", text: label }),
      el("p", { class: "text-lg md:text-2xl font-black mt-1 text-gray-900", text: value }),
      sub ? el("p", { class: "text-xs text-gray-500", text: sub }) : null,
    ]);
  }

  function card(e) {
    var st = ST[e.status] || ST.avenir, pct = e.total_seats > 0 ? Math.min(100, Math.round(e.seats_sold * 100 / e.total_seats)) : 0;
    var actions = [el("a", { class: "px-3 py-2 rounded-xl text-xs font-bold bg-gray-100 text-gray-700 whitespace-nowrap",
      href: "event_detail.html?id=" + e.id, target: "_blank", rel: "noopener", text: "Voir la page ↗" })];
    if (isAdmin()) {
      if (e.status === "desactive") actions.push(el("button", { class: "px-3 py-2 rounded-xl text-xs font-bold bg-emerald-50 text-emerald-700 whitespace-nowrap",
        text: "Réactiver", onclick: function () { reactivate(e); } }));
      else if (e.status !== "passe") actions.push(el("button", { class: "px-3 py-2 rounded-xl text-xs font-bold bg-red-50 text-red-700 whitespace-nowrap",
        text: "Désactiver…", onclick: function () { deactivateSheet(e); } }));
    }
    return el("div", { class: "bg-white rounded-2xl border border-gray-100 shadow-sm p-4 lg:flex lg:items-center lg:gap-5" }, [
      el("div", { class: "min-w-0 lg:flex-1" }, [
        el("div", { class: "flex items-center gap-2 flex-wrap mb-1" }, [
          el("span", { class: "px-2 py-0.5 rounded-full text-[11px] font-bold " + st[1], text: st[0] }),
          e.is_boosted ? el("span", { class: "px-2 py-0.5 rounded-full text-[11px] font-bold bg-orange-50 text-orange-700", text: "Boost actif" }) : null,
          el("span", { class: "text-xs text-gray-500", text: when(e.event_date) }),
        ]),
        el("p", { class: "font-black text-gray-900 text-[15px] lg:text-base truncate", text: e.title || "—" }),
        el("p", { class: "text-xs text-gray-500 truncate", text: (e.organizer || "—") + (e.category ? " · " + e.category : "") }),
        e.deactivated_reason ? el("p", { class: "text-[11px] text-red-700 mt-1", text: "Motif : " + e.deactivated_reason }) : null,
      ]),
      el("div", { class: "mt-3 grid grid-cols-2 gap-3 lg:mt-0 lg:flex lg:items-center lg:gap-6 lg:shrink-0" }, [
        el("div", { class: "lg:w-36" }, [
          el("p", { class: "text-[11px] text-gray-500", text: "Billets" }),
          el("p", { class: "font-black text-sm" }, [num(e.seats_sold) + " / " + num(e.total_seats) + " ",
            el("span", { class: "text-gray-400 font-bold", text: "(" + pct + " %)" })]),
          el("div", { class: "h-1.5 bg-gray-100 rounded-full mt-1" }, [
            el("div", { class: "h-1.5 rounded-full " + (pct >= 100 ? "bg-gray-900" : "bg-emerald-500"), style: "width:" + pct + "%" })]),
        ]),
        el("div", { class: "text-right lg:w-36" }, [
          el("p", { class: "text-[11px] text-gray-500", text: "Recettes brutes" }),
          el("p", { class: "font-black text-sm", text: fcfa(e.revenue_fcfa) }),
        ]),
      ]),
      el("div", { class: "flex gap-2 mt-3 lg:mt-0 lg:shrink-0" }, actions),
    ]);
  }

  function render() {
    var sec = section(), d = S.data;
    if (!sec || !d) return;
    var k = d.kpis || {}, c = d.counts || {};
    sec.innerHTML = "";
    sec.appendChild(el("div", { class: "mb-4" }, [
      el("h2", { class: "text-xl md:text-2xl font-black text-gray-900", text: "Événements publics" }),
      el("p", { class: "text-xs text-gray-500", text: "Billetterie. Les événements privés (invitations) sont dans Invitations." }),
    ]));
    sec.appendChild(el("div", { class: "grid grid-cols-2 lg:grid-cols-4 gap-3 mb-5" }, [
      kpi("À venir", num(k.upcoming), k.upcoming_weekend ? "dont " + k.upcoming_weekend + " ce week-end" : "aucun ce week-end"),
      kpi("Billets vendus (30 j)", num(k.tickets_30d), k.upcoming_seats ? num(k.upcoming_seats) + " places à venir" : ""),
      kpi("Recettes brutes (30 j)", fcfa(k.revenue_30d), "prix des billets, hors frais"),
      kpi("À surveiller", num(k.watch), "complets ou < 10 % à J-7"),
    ]));
    var search = el("input", { type: "search", value: S.q, placeholder: "Titre, organisateur, lieu…",
      class: "w-full bg-white border border-gray-200 rounded-2xl px-4 py-3 text-sm mb-3" });
    search.addEventListener("input", function () {
      clearTimeout(S.timer);
      S.timer = setTimeout(function () { S.q = search.value.trim(); load(true); }, 350);
    });
    sec.appendChild(search);
    sec.appendChild(el("div", { class: "flex gap-2 overflow-x-auto pb-1 mb-3", style: "scrollbar-width:none" }, TABS.map(function (t) {
      var on = S.status === t[0];
      return el("button", { class: "shrink-0 whitespace-nowrap px-3 py-1.5 rounded-full text-xs font-bold " +
        (on ? "bg-[#00502c] text-white" : "bg-white border border-gray-200 text-gray-700"),
        text: t[1] + " (" + num(c[t[0]]) + ")", onclick: function () { S.status = t[0]; load(true); } });
    })));
    var list = el("div", { class: "space-y-2" });
    if (!S.items.length) list.appendChild(el("div", { class: "bg-white rounded-2xl p-8 text-center text-sm text-gray-500",
      text: S.q ? "Aucun événement ne correspond à « " + S.q + " »." : "Aucun événement dans cette liste." }));
    S.items.forEach(function (e) { list.appendChild(card(e)); });
    sec.appendChild(list);
    var shown = S.items.length;
    var foot = el("p", { class: "text-center text-xs text-gray-500 mt-4", text: shown + " sur " + num(d.total) });
    if (shown < d.total) {
      foot.appendChild(document.createTextNode(" · "));
      foot.appendChild(el("button", { class: "font-bold text-[#00502c]", text: "Charger la suite",
        onclick: function () { S.page += 1; load(false); } }));
    }
    sec.appendChild(foot);
  }

  function load(reset) {
    if (S.busy) return;
    if (reset) { S.page = 1; }
    S.busy = true;
    var qs = "page=" + S.page + "&limit=" + LIMIT + "&status=" + encodeURIComponent(S.status) +
      (S.q ? "&q=" + encodeURIComponent(S.q) : "");
    btApi.get("/admin/events?" + qs).then(function (d) {
      S.data = d;
      S.items = reset ? d.items : S.items.concat(d.items);
      render();
    }).catch(function (e) {
      var sec = section();
      if (sec && !S.data) sec.innerHTML = '<div class="bg-white rounded-2xl p-8 text-center text-sm text-red-600">Erreur de chargement des événements.</div>';
      toast(e.message || "Erreur de chargement", "error");
    }).then(function () { S.busy = false; });
  }

  // ── Désactivation / réactivation ───────────────────────────
  function close(ov) { if (ov && ov.parentNode) ov.parentNode.removeChild(ov); }
  function deactivateSheet(e) {
    var reason = el("textarea", { rows: "3", maxlength: "300", class: "w-full border border-gray-200 rounded-xl px-3 py-3 text-sm",
      placeholder: "Ex. visuel non conforme, organisateur injoignable…" });
    var notify = el("input", { type: "checkbox", class: "w-5 h-5 rounded border-gray-300" });
    var err = el("p", { class: "text-xs text-red-600 min-h-4 mt-2" });
    var ov = el("div", { class: "fixed inset-0 bg-black/50 z-[1000] flex items-end md:items-center justify-center" });
    var go = el("button", { class: "py-3 rounded-xl font-bold bg-red-600 text-white", text: "Désactiver" });
    var warn = e.seats_sold > 0
      ? el("div", { class: "mt-3 bg-red-50 border border-red-100 rounded-2xl p-3 text-sm text-red-800" }, [
          el("b", { text: num(e.seats_sold) + " place(s) vendue(s) · " + fcfa(e.revenue_fcfa) + ". " }),
          "Désactiver retire l'événement du site, mais les billets payés restent valables. Pour annuler l'événement et rembourser les acheteurs, passez par Annulations."])
      : el("p", { class: "mt-3 text-sm text-gray-600", text: "Aucun billet vendu : l'événement est simplement retiré du site." });
    var box = el("div", { class: "bg-white w-full md:max-w-md rounded-t-3xl md:rounded-3xl p-5 pb-8 md:pb-5 max-h-[92vh] overflow-y-auto" }, [
      el("div", { class: "w-10 h-1 bg-gray-200 rounded-full mx-auto mb-4 md:hidden" }),
      el("h3", { class: "text-lg font-black", text: "Désactiver « " + e.title + " » ?" }), warn,
      el("label", { class: "block text-[11px] font-bold text-gray-500 uppercase tracking-widest mt-4 mb-1", text: "Motif (obligatoire, journalisé)" }), reason,
      el("label", { class: "flex items-center gap-2 mt-3 text-sm" }, [notify, "Prévenir l'organisateur par e-mail"]), err,
      el("div", { class: "grid grid-cols-2 gap-2 mt-4" }, [
        el("button", { class: "py-3 rounded-xl font-bold bg-gray-100", text: "Annuler", onclick: function () { close(ov); } }), go]),
    ]);
    ov.appendChild(box);
    ov.addEventListener("click", function (ev) { if (ev.target === ov) close(ov); });
    go.addEventListener("click", function () {
      var r = reason.value.trim();
      if (r.length < 5) { err.textContent = "Indiquez un motif (5 caractères minimum)."; return; }
      go.disabled = true;
      btApi.post("/admin/events/" + e.id + "/deactivate", { reason: r, notify_organizer: notify.checked }).then(function (res) {
        close(ov);
        toast(res.message || "Événement désactivé.", "success");
        load(true);
      }).catch(function (ex) { err.textContent = ex.message || "Erreur"; go.disabled = false; });
    });
    document.body.appendChild(ov);
    reason.focus();
  }
  function reactivate(e) {
    if (!window.confirm("Réactiver « " + e.title + " » ? Il réapparaîtra sur le site.")) return;
    btApi.post("/admin/events/" + e.id + "/reactivate", {}).then(function () {
      toast("Événement réactivé.", "success"); load(true);
    }).catch(function (ex) { toast(ex.message || "Erreur", "error"); });
  }

  window.adminEvents = { load: function () { load(true); } };
})();
