/* ════════════════════════════════════════════════════════════════
 * admin-invitations.js — Onglet « Invitations » du back-office (admin.html)
 *
 * Offre accompagnée : l'équipe GoEvent crée l'événement privé, importe la
 * liste, envoie les pass par WhatsApp (lien wa.me, un clic par invité),
 * suit les arrivées et remet au client un lien de suivi (chiffres seuls).
 * Accès : capability invitations_manage (admin + commercial). Activation d'un
 * pack : admin uniquement (invitations_activate), après paiement reçu sur un
 * moyen AU NOM DE GoEvent SARLU, référence obligatoire (garde serveur).
 * Catalogue (formules, packs, options, codes, réglages, historique) : lecture
 * admin + commercial, modification admin seul. Devis = même calcul serveur que
 * l'activation (POST …/packs/quote) : affiché = facturé.
 * Dépend de bt-api.js (btApi, btToast, _btEscapeHtml) et de hasCapability().
 * ════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";

  var S = { meta: null, view: "list", eventId: null, ev: null, guests: null, q: "", status: "", page: 1,
            queue: [], timer: null, importRows: null };
  function formulaName(code) {
    var f = S.meta && (S.meta.formulas || []).filter(function (x) { return x.code === code; })[0];
    return f ? f.name : (code || "—");
  }
  var TZ = "Africa/Bangui";
  // Dates de validité : saisies au jour (heure de Bangui), stockées en UTC
  function dateInput(iso) {
    if (!iso) return "";
    var d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z");
    return d.toLocaleDateString("fr-CA", { timeZone: TZ });
  }
  function dateIso(v, end) { return v ? v + (end ? "T23:59:59+01:00" : "T00:00:00+01:00") : null; }
  function dayLabel(iso) { return iso ? new Date(/[zZ]$/.test(iso) ? iso : iso + "Z").toLocaleDateString("fr-FR", { timeZone: TZ, day: "numeric", month: "short", year: "numeric" }) : ""; }
  var STATUS = { pending: ["À envoyer", "bg-gray-100 text-gray-700"], sent: ["Envoyé", "bg-blue-100 text-blue-800"],
                 partial: ["Entré en partie", "bg-amber-100 text-amber-800"], complete: ["Entré", "bg-green-100 text-green-800"],
                 revoked: ["Annulé", "bg-red-100 text-red-700"], to_print: ["À imprimer", "bg-orange-100 text-orange-800"],
                 printed: ["Imprimé", "bg-blue-100 text-blue-800"] };

  var toast = function (m, t) { if (typeof btToast === "function") btToast(m, t || "info"); };
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
  function fcfa(v) { return v == null ? "—" : Math.round(v).toLocaleString("fr-FR") + " FCFA"; }
  function dt(iso) {
    if (!iso) return "—";
    var d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z");
    return d.toLocaleDateString("fr-FR", { day: "numeric", month: "short" }) + " " + d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  }
  function toLocalInput(iso) {
    if (!iso) return "";
    var d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z"), p = function (n) { return (n < 10 ? "0" : "") + n; };
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + "T" + p(d.getHours()) + ":" + p(d.getMinutes());
  }
  function copy(text, label) {
    (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject()).then(function () { toast((label || "Lien") + " copié.", "success"); })
      .catch(function () { window.prompt("Copiez :", text); });
  }
  function canActivate() { return S.meta && S.meta.can_activate; }
  function sec() { return document.getElementById("section-invitations"); }
  function lbl(t) { return el("label", { class: "block text-[11px] font-bold text-gray-500 uppercase tracking-widest mb-1", text: t }); }
  function field(label, node, hint) {
    return el("div", {}, [lbl(label), node, hint ? el("p", { class: "text-[11px] text-gray-400 mt-1", text: hint }) : null]);
  }
  function modal(title, body, actions) {
    var overlay = el("div", { class: "modal-overlay inv-modal" });
    var close = function () { overlay.remove(); };
    overlay.appendChild(el("div", { class: "modal-box", style: "max-width:640px;max-height:92vh;overflow:auto" }, [
      el("div", { class: "flex justify-between items-center mb-4" }, [
        el("h3", { class: "text-lg font-black text-gray-900", text: title }),
        el("button", { class: "text-gray-400 hover:text-red-500 text-xl font-bold", text: "×", onclick: close }),
      ]),
      body,
      el("div", { class: "flex justify-end gap-2 pt-4" }, [el("button", { class: "action-btn", style: "background:#f3f4f6;color:#374151", text: "Annuler", onclick: close })].concat(actions(close))),
    ]));
    document.body.appendChild(overlay);
    overlay.addEventListener("click", function (e) { if (e.target === overlay) close(); });
    return close;
  }
  function stopTimer() { if (S.timer) { clearInterval(S.timer); S.timer = null; } }

  // ════════════════════════════════════════════════════════════════
  // LISTE DES ÉVÉNEMENTS PRIVÉS
  // ════════════════════════════════════════════════════════════════
  function loadList() {
    stopTimer();
    S.view = "list";
    var root = sec();
    root.innerHTML = '<div class="card p-6 empty-state">Chargement…</div>';
    btApi.get("/admin/invitations/events").then(function (d) {
      root.innerHTML = "";
      root.appendChild(el("div", { class: "flex flex-wrap justify-between items-start gap-3 mb-5" }, [
        el("div", {}, [
          el("h2", { class: "text-xl font-black text-gray-900", text: "Invitations digitales" }),
          el("p", { class: "text-xs text-gray-500 mt-1", text: "Événements privés (mariages, dots, galas…) : liste d'invités, pass QR nominatifs envoyés par WhatsApp, contrôle à l'entrée, suivi des arrivées." }),
        ]),
        el("div", { class: "flex flex-wrap gap-2" }, [
          el("button", { class: "action-btn", style: "background:#ede9fe;color:#5b21b6", text: "Catalogue des packs", onclick: function () { openCatalog("formulas"); } }),
          el("button", { class: "action-btn btn-primary", text: "+ Nouvel événement privé", onclick: eventModal }),
        ]),
      ]));
      if (d.summary) {
        var sm = d.summary;
        root.appendChild(el("p", { class: "text-xs text-gray-600 mb-3", text: "Encaissé (packs actifs payés) : " + fcfa(sm.paid_total_fcfa) + " · " +
          sm.paid_count + " pack(s) payé(s)" + (sm.offered_count ? " · " + sm.offered_count + " pack(s) offert(s), non comptés en recette" : "") }));
      }
      if (!d.events.length) {
        root.appendChild(el("div", { class: "card p-6 empty-state", text: "Aucun événement privé pour l'instant." }));
        return;
      }
      var grid = el("div", { class: "grid gap-3 md:grid-cols-2" });
      d.events.forEach(function (e) {
        var s = e.stats;
        var packTxt = e.quota ? e.quota + " pers. · " + (e.formula_name || "") : "Aucun pack activé";
        grid.appendChild(el("button", { class: "card p-4 text-left hover:shadow-md transition-all", onclick: function () { openEvent(e.id); } }, [
          el("div", { class: "flex justify-between items-start gap-2" }, [
            el("p", { class: "font-black text-gray-900", text: e.title }),
            el("span", { class: "text-[10px] font-black px-2 py-0.5 rounded-full " + (e.quota ? "bg-green-100 text-green-800" : "bg-amber-100 text-amber-800"), text: packTxt }),
          ]),
          el("p", { class: "text-xs text-gray-500 mt-1", text: e.event_date_label + " · " + (e.location || "") }),
          el("p", { class: "text-xs text-gray-500", text: "Client : " + e.client_name + (e.purged ? " · liste supprimée" : "") }),
          el("p", { class: "text-sm font-bold text-gray-800 mt-2", text: s.entered + " / " + s.expected + " entrés · " + s.passes_sent + "/" + s.passes + " pass envoyés" }),
        ]));
      });
      root.appendChild(grid);
    }).catch(function (e) { root.innerHTML = ""; root.appendChild(el("div", { class: "card p-6 text-red-600 text-sm", text: e.message || "Erreur" })); });
  }

  function eventModal(ev) {
    ev = ev && ev.id ? ev : null;
    var f = {
      title: el("input", { class: "input-field", maxlength: "150", placeholder: "Ex. Mariage de Paul & Marie" }),
      date: el("input", { class: "input-field", type: "datetime-local" }),
      location: el("input", { class: "input-field", maxlength: "200", placeholder: "Salle, quartier, ville" }),
      client: el("input", { class: "input-field", maxlength: "120", placeholder: "Ex. Famille Ngaba" }),
      phone: el("input", { class: "input-field", maxlength: "30", placeholder: "+236 …" }),
      host: el("input", { class: "input-field", maxlength: "120", placeholder: "Ex. Paul & Marie (affiché « … vous invite »)" }),
      service: el("select", { class: "input-field" }, (S.meta.formulas || []).map(function (fo) {
        return el("option", { value: fo.code, text: fo.name + (fo.description ? " — " + fo.description : "") });
      })),
      desc: el("textarea", { class: "input-field", rows: "2", maxlength: "2000", placeholder: "Note interne (facultatif)" }),
    };
    if (ev) {
      f.title.value = ev.title; f.date.value = toLocalInput(ev.event_date); f.location.value = ev.location || "";
      f.client.value = ev.client_name; f.phone.value = ev.client_phone || ""; f.host.value = ev.host_label || "";
      if (!(S.meta.formulas || []).some(function (fo) { return fo.code === ev.service_level; }))
        f.service.appendChild(el("option", { value: ev.service_level, text: (ev.formula_name || ev.service_level) + " (archivée)" }));
      f.service.value = ev.service_level; f.desc.value = ev.description || "";
    } else {
      var acc = (S.meta.formulas || []).filter(function (fo) { return fo.code === "accompagne"; })[0];
      if (acc) f.service.value = acc.code;
    }
    var body = el("div", { class: "space-y-3" }, [
      field("Titre", f.title), el("div", { class: "grid sm:grid-cols-2 gap-3" }, [field("Date et heure", f.date), field("Lieu", f.location)]),
      el("div", { class: "grid sm:grid-cols-2 gap-3" }, [field("Client", f.client), field("Téléphone du client", f.phone)]),
      field("Hôte affiché sur le pass", f.host, "Vide = nom du client."), field("Formule prévue", f.service, "Le pack réellement payé (activation) fait foi."), field("Description", f.desc),
    ]);
    modal(ev ? "Modifier l'événement" : "Nouvel événement privé", body, function (close) {
      var b = el("button", { class: "action-btn btn-primary", text: ev ? "Enregistrer" : "Créer", onclick: function () {
        if (!f.date.value) { toast("Indiquez la date.", "error"); return; }
        var payload = { title: f.title.value.trim(), event_date: new Date(f.date.value).toISOString(), location: f.location.value.trim(),
          client_name: f.client.value.trim(), client_phone: f.phone.value.trim() || null, host_label: f.host.value.trim() || null,
          service_level: f.service.value, description: f.desc.value.trim() || null };
        b.disabled = true;
        (ev ? btApi.patch("/admin/invitations/events/" + ev.id, payload) : btApi.post("/admin/invitations/events", payload))
          .then(function (r) { close(); toast(ev ? "Événement enregistré." : "Événement privé créé.", "success"); openEvent(r.id); })
          .catch(function (e) { b.disabled = false; toast(e.message || "Erreur", "error"); });
      } });
      return [b];
    });
  }

  // ════════════════════════════════════════════════════════════════
  // FICHE ÉVÉNEMENT
  // ════════════════════════════════════════════════════════════════
  function openEvent(id) {
    stopTimer();
    S.view = "event"; S.eventId = id; S.q = ""; S.status = ""; S.page = 1; S.importRows = null;
    sec().innerHTML = '<div class="card p-6 empty-state">Chargement…</div>';
    btApi.get("/admin/invitations/events/" + id).then(function (ev) { S.ev = ev; renderEvent(); loadGuests(); })
      .catch(function (e) { toast(e.message || "Erreur", "error"); loadList(); });
    S.timer = setInterval(function () { if (S.view === "event" && !document.hidden) refreshLive(); }, 30000);
  }
  function reloadEvent() {
    return btApi.get("/admin/invitations/events/" + S.eventId).then(function (ev) { S.ev = ev; renderEvent(); renderGuests(); });
  }

  function kpi(label, value, sub, cls) {
    return el("div", { class: "card p-4" }, [
      el("p", { class: "text-[10px] font-black uppercase tracking-widest text-gray-400", text: label }),
      el("p", { class: "text-2xl font-black " + (cls || "text-gray-900"), text: value }),
      sub ? el("p", { class: "text-[11px] text-gray-500", text: sub }) : null,
    ]);
  }
  function block(title, children, id) {
    return el("div", { class: "card p-5 mb-4", id: id || null }, [el("h3", { class: "font-black text-gray-900 mb-3", text: title })].concat(children));
  }

  function renderEvent() {
    var ev = S.ev, root = sec(), s = ev.stats;
    root.innerHTML = "";
    root.appendChild(el("button", { class: "text-sm font-bold text-primary mb-3", text: "← Tous les événements privés", onclick: loadList }));
    root.appendChild(el("div", { class: "flex flex-wrap justify-between items-start gap-3 mb-4" }, [
      el("div", {}, [
        el("h2", { class: "text-xl font-black text-gray-900", text: ev.title }),
        el("p", { class: "text-xs text-gray-500 mt-1", text: ev.event_date_label + " · " + (ev.location || "") + " · Client : " + ev.client_name +
          (ev.client_phone ? " (" + ev.client_phone + ")" : "") + " · " + (ev.formula_name || ev.service_level) }),
      ]),
      el("button", { class: "action-btn text-xs", style: "background:#ede9fe;color:#5b21b6", text: "Modifier", onclick: function () { eventModal(ev); } }),
    ]));
    if (ev.purged) {
      root.appendChild(el("div", { class: "card p-4 mb-4 bg-gray-50 text-sm text-gray-600", text: "Liste d'invités supprimée (conservation de " + S.meta.retention_days + " jours dépassée) : seuls les chiffres restent." }));
    }
    var packTxt = ev.quota ? ev.quota + " personnes" : "Aucun";
    root.appendChild(el("div", { class: "grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4", id: "inv-kpis" }, [
      kpi("Entrés", s.entered + " / " + s.expected, s.rate + " % · " + s.remaining + " restant(s)", "text-primary"),
      kpi("Pass envoyés", s.passes_sent + " / " + s.passes, s.passes_arrived + " pass utilisés"),
      kpi("Pack payé", packTxt, ev.quota ? ev.listed + " personne(s) dans la liste" : "Envoi et scan bloqués", ev.quota ? "text-gray-900" : "text-amber-600"),
      kpi("Conflits hors ligne", String(s.conflicts), s.conflicts ? "Pass accepté(s) sur 2 téléphones" : "Aucun", s.conflicts ? "text-red-600" : "text-gray-900"),
    ]));
    root.appendChild(packBlock());
    if (!ev.purged) root.appendChild(guestsBlock());
    root.appendChild(liveBlock());
    root.appendChild(messageBlock());
    root.appendChild(agentsBlock());
    root.appendChild(dataBlock());
  }

  function refreshLive() {
    btApi.get("/admin/invitations/events/" + S.eventId + "/arrivals").then(function (d) {
      S.ev.stats = d.stats;
      var k = document.getElementById("inv-kpis");
      if (k) {
        var s = d.stats;
        k.replaceWith(el("div", { class: "grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4", id: "inv-kpis" }, [
          kpi("Entrés", s.entered + " / " + s.expected, s.rate + " % · " + s.remaining + " restant(s)", "text-primary"),
          kpi("Pass envoyés", s.passes_sent + " / " + s.passes, s.passes_arrived + " pass utilisés"),
          kpi("Pack payé", S.ev.quota ? S.ev.quota + " personnes" : "Aucun", S.ev.quota ? S.ev.listed + " personne(s) dans la liste" : "Envoi et scan bloqués",
              S.ev.quota ? "text-gray-900" : "text-amber-600"),
          kpi("Conflits hors ligne", String(s.conflicts), s.conflicts ? "Pass accepté(s) sur 2 téléphones" : "Aucun", s.conflicts ? "text-red-600" : "text-gray-900"),
        ]));
      }
      renderArrivals(d.arrivals);
    }).catch(function () {});
  }

  // ── Pack & paiement (commandes figées) ────────────────────────────
  function orderLines(p) {
    var lines = [];
    if (p.legacy) return [fcfa(p.amount_fcfa) + " (pack activé avant le catalogue)"];
    lines.push("Pack " + (p.pack_name ? "« " + p.pack_name + " » " : "") + p.size + " pers. : " + fcfa(p.unit_price) + (p.custom ? " (sur mesure : " + (p.custom_reason || "") + ")" : ""));
    (p.options || []).forEach(function (o) { lines.push(o.name + (o.unit === "flat" ? "" : " × " + o.qty) + " : " + fcfa(o.total)); });
    if (p.discount) lines.push("Remise " + (p.discount.kind === "code" ? "code " + p.discount.code : "manuelle (" + (p.discount.reason || "") + ")") + " : −" + fcfa(p.discount.amount));
    if (p.vat && p.vat.enabled) lines.push("HT " + fcfa(p.vat.ht) + " · TVA " + (p.vat.rate_bp / 100).toString().replace(".", ",") + " % " + fcfa(p.vat.vat));
    return lines;
  }
  function packBlock() {
    var ev = S.ev, items = [];
    if (!ev.packs.length) items.push(el("p", { class: "text-sm text-amber-700 font-semibold mb-3", text: "Aucun pack activé : vous pouvez préparer la liste, mais l'envoi des pass, le scan et le lien de suivi restent bloqués." }));
    ev.packs.forEach(function (p) {
      var ch = p.offered ? "Pack offert" : ((S.meta.channels.filter(function (c) { return c.code === p.payment_channel; })[0] || {}).label || p.payment_channel);
      items.push(el("div", { class: "border border-gray-100 rounded-xl p-3 mb-2 flex flex-wrap items-start gap-3 " + (p.status === "actif" ? "" : "opacity-60") }, [
        el("div", { class: "flex-1 min-w-[220px]" }, [
          el("div", { class: "flex flex-wrap items-center gap-2" }, [
            el("p", { class: "font-bold text-sm", text: (p.formula_name || formulaName(p.service)) + " · " + p.size + " personnes · " + (p.offered ? "OFFERT" : fcfa(p.amount_fcfa)) + (p.status === "actif" ? "" : " · ANNULÉ") }),
            p.offered ? el("span", { class: "text-[10px] font-black px-2 py-0.5 rounded-full bg-purple-100 text-purple-800", text: "Pack offert" }) : null,
          ]),
          el("ul", { class: "text-[11px] text-gray-600 mt-1 list-disc pl-4" }, orderLines(p).map(function (t) { return el("li", { text: t }); })),
          el("p", { class: "text-[11px] text-gray-500 mt-1", text: p.offered ? "Motif : " + (p.offered_reason || "—") + " · " + dt(p.activated_at)
            : ch + " · réf. " + p.payment_ref + " · reçu " + fcfa(p.amount_received) + " · " + dt(p.activated_at) }),
          p.agents_included ? el("p", { class: "text-[11px] text-gray-500", text: p.agents_included + " agent(s) GoEvent inclus" }) : null,
          p.cancel_reason ? el("p", { class: "text-[11px] text-red-600", text: "Annulé : " + p.cancel_reason }) : null,
        ]),
        p.status === "actif" && canActivate() ? el("button", { class: "action-btn btn-danger text-xs", text: "Annuler le pack", onclick: function () {
          var reason = window.prompt("Motif de l'annulation (obligatoire) :");
          if (!reason || reason.trim().length < 3) return;
          btApi.post("/admin/invitations/packs/" + p.id + "/cancel", { reason: reason.trim() }).then(function () { toast("Pack annulé.", "success"); reloadEvent(); })
            .catch(function (e) { toast(e.message || "Erreur", "error"); });
        } }) : null,
      ]));
    });
    var actions = el("div", { class: "flex flex-wrap gap-2" });
    if (canActivate()) actions.appendChild(el("button", { class: "action-btn btn-primary text-xs", text: "Activer un pack (paiement reçu)", onclick: function () { orderModal(true); } }));
    actions.appendChild(el("button", { class: "action-btn text-xs", style: "background:#ede9fe;color:#5b21b6", text: "Préparer un devis", onclick: function () { orderModal(false); } }));
    items.push(actions);
    if (!canActivate()) items.push(el("p", { class: "text-[11px] text-gray-500 mt-2", text: "L'activation d'un pack est réservée à l'administrateur, après réception du paiement." }));
    return block("Pack & paiement", items);
  }

  // Devis (commercial + admin) et activation (admin) : le récapitulatif vient du serveur (même calcul)
  function orderModal(activate) {
    var m = S.meta, admin = canActivate();
    var formula = el("select", { class: "input-field" }), pack = el("select", { class: "input-field" });
    (m.formulas || []).forEach(function (fo) { formula.appendChild(el("option", { value: String(fo.id), text: fo.name })); });
    var cur = (m.formulas || []).filter(function (fo) { return fo.code === S.ev.service_level; })[0];
    if (cur) formula.value = String(cur.id);
    var cSize = el("input", { class: "input-field", type: "number", min: "1", max: "5000", placeholder: "Personnes" });
    var cPrice = el("input", { class: "input-field", type: "number", min: "0", placeholder: "FCFA" });
    var cReason = el("input", { class: "input-field", maxlength: "300", placeholder: "Ex. mariage 1 200 invités, tarif négocié" });
    var customBox = el("div", { class: "hidden grid sm:grid-cols-3 gap-3 p-3 rounded-xl bg-gray-50" }, [field("Taille", cSize), field("Prix du pack", cPrice), field("Motif (obligatoire)", cReason)]);
    var optBox = el("div", { class: "space-y-2" }), optInputs = [];
    var dMode = el("select", { class: "input-field" }, [el("option", { value: "", text: "Aucune remise" }), el("option", { value: "code", text: "Code de remise" })]
      .concat(admin ? [el("option", { value: "manual", text: "Remise manuelle (admin)" })] : []));
    var dCode = el("input", { class: "input-field uppercase", maxlength: "40", placeholder: "Ex. PIONNIER" });
    var mKind = el("select", { class: "input-field" }, [el("option", { value: "pct", text: "%" }), el("option", { value: "fixed", text: "FCFA" })]);
    var mValue = el("input", { class: "input-field", type: "number", min: "1" }), mReason = el("input", { class: "input-field", maxlength: "300", placeholder: "Motif (obligatoire)" });
    var codeBox = el("div", { class: "hidden" }, [field("Code", dCode, "Remise sur le pack uniquement (jamais sur les options).")]);
    var manualBox = el("div", { class: "hidden grid sm:grid-cols-3 gap-3" }, [field("Type", mKind), field("Valeur", mValue), field("Motif", mReason)]);
    var offered = el("input", { type: "checkbox" }), offeredReason = el("input", { class: "input-field", maxlength: "300", placeholder: "Ex. partenariat, mariage du personnel" });
    var offeredBox = el("div", { class: "hidden" }, [field("Motif du pack offert (obligatoire)", offeredReason)]);
    var recap = el("div", { class: "p-3 rounded-xl bg-gray-50 text-sm" }, [el("p", { class: "text-gray-400", text: "Choisissez un pack…" })]);
    var channel = el("select", { class: "input-field" }), ref = el("input", { class: "input-field", maxlength: "80" }), refLbl = lbl("Référence");
    var amount = el("input", { class: "input-field", type: "number", min: "0" }), note = el("input", { class: "input-field", maxlength: "300", placeholder: "Facultatif" });
    var confirm = el("input", { type: "checkbox" });
    var active = (m.channels || []).filter(function (c) { return c.active; });
    active.forEach(function (c) { channel.appendChild(el("option", { value: c.code, text: c.label })); });
    var payBox = el("div", { class: "space-y-3" }, [
      el("div", { class: "p-3 rounded-xl bg-amber-50 text-xs text-amber-900", text: "N'activez un pack qu'après avoir reçu le paiement sur un moyen AU NOM DE GoEvent SARLU. La référence ne pourra plus servir à activer un autre pack." }),
      field("Moyen de paiement", channel), el("div", {}, [refLbl, ref]), field("Montant reçu (FCFA)", amount, "Au moins le total à payer."), field("Note", note),
      el("label", { class: "flex items-start gap-2 text-sm font-semibold text-gray-800" }, [confirm, "Je confirme que ce paiement a été reçu sur un moyen de paiement au nom de GoEvent SARLU."]),
    ]);
    var lastQuote = null, timer = null;

    function fillPacks() {
      var fid = parseInt(formula.value, 10);
      pack.innerHTML = "";
      (m.packs || []).filter(function (p) { return p.formula_id === fid; }).forEach(function (p) {
        pack.appendChild(el("option", { value: String(p.id), text: p.size + " pers." + (p.name ? " · " + p.name : "") + " · " + (p.on_quote ? "sur devis" : fcfa(p.price_fcfa)) }));
      });
      if (admin) pack.appendChild(el("option", { value: "custom", text: "Sur mesure (taille et prix libres)…" }));
      optBox.innerHTML = ""; optInputs = [];
      var opts = (m.options || []).filter(function (o) { return o.formula_ids.indexOf(fid) >= 0; });
      if (!opts.length) optBox.appendChild(el("p", { class: "text-[11px] text-gray-400", text: "Aucune option pour cette formule." }));
      opts.forEach(function (o) {
        var input = o.unit === "flat" ? el("input", { type: "checkbox" }) : el("input", { class: "input-field", type: "number", min: "0", max: String(o.max_qty), value: "0", style: "max-width:90px" });
        input.addEventListener("input", schedule); input.addEventListener("change", schedule);
        optInputs.push({ o: o, input: input });
        optBox.appendChild(el("label", { class: "flex items-center gap-2 text-sm" }, [input,
          el("span", { text: o.name + " — " + fcfa(o.price_fcfa) + (o.unit === "flat" ? " (forfait)" : " / " + (o.unit_label || (o.unit === "per_page" ? "page" : "unité")) + ", max " + o.max_qty) })]));
      });
    }
    function payload() {
      var body = { formula_id: parseInt(formula.value, 10), options: [], offered: offered.checked };
      if (pack.value === "custom") body.custom = { size: parseInt(cSize.value, 10) || 0, price_fcfa: cPrice.value === "" ? -1 : parseInt(cPrice.value, 10), reason: cReason.value.trim() };
      else body.pack_id = parseInt(pack.value, 10) || null;
      optInputs.forEach(function (x) {
        var q = x.o.unit === "flat" ? (x.input.checked ? 1 : 0) : (parseInt(x.input.value, 10) || 0);
        if (q > 0) body.options.push({ option_id: x.o.id, qty: q });
      });
      if (offered.checked) body.offered_reason = offeredReason.value.trim();
      else if (dMode.value === "code" && dCode.value.trim()) body.discount_code = dCode.value.trim();
      else if (dMode.value === "manual") body.manual_discount = { kind: mKind.value, value: parseInt(mValue.value, 10) || 0, reason: mReason.value.trim() };
      return body;
    }
    function row(label, value, cls) { return el("div", { class: "flex justify-between gap-3 " + (cls || "") }, [el("span", { text: label }), el("span", { class: "font-bold whitespace-nowrap", text: value })]); }
    function renderQuote(q) {
      lastQuote = q; recap.innerHTML = "";
      recap.appendChild(row((q.formula.name) + " · " + (q.pack.name ? "« " + q.pack.name + " » " : "") + q.pack.size + " pers.", fcfa(q.pack.unit_price)));
      q.options.forEach(function (o) { recap.appendChild(row(o.name + (o.unit === "flat" ? "" : " × " + o.qty), fcfa(o.total), "text-gray-600")); });
      if (q.discount) recap.appendChild(row("Remise " + (q.discount.kind === "code" ? q.discount.code : "manuelle") + (q.discount.type === "pct" ? " (−" + q.discount.value + " % du pack)" : ""), "−" + fcfa(q.discount.amount), "text-green-700"));
      if (q.offered) recap.appendChild(row("Pack offert", "−" + fcfa(q.subtotal), "text-purple-700"));
      if (q.vat.enabled) { recap.appendChild(row("Total HT", fcfa(q.vat.ht), "text-gray-600")); recap.appendChild(row("TVA " + (q.vat.rate_bp / 100).toString().replace(".", ",") + " %", fcfa(q.vat.vat), "text-gray-600")); }
      recap.appendChild(row(q.vat.enabled ? "Total TTC à payer" : "Total à payer", fcfa(q.amount_due), "text-base border-t border-gray-200 pt-2 mt-2"));
      if (q.agents_included) recap.appendChild(el("p", { class: "text-[11px] text-gray-600 mt-2", text: q.agents_included + " agent(s) GoEvent inclus dans ce pack · " + q.agents_assigned + " déjà assigné(s) à l'événement" }));
      q.warnings.forEach(function (w) { recap.appendChild(el("p", { class: "text-xs font-bold text-amber-800 bg-amber-50 rounded-lg p-2 mt-2", text: "⚠ " + w })); });
      q.blocking.forEach(function (w) { recap.appendChild(el("p", { class: "text-xs font-bold text-red-700 bg-red-50 rounded-lg p-2 mt-2", text: "⛔ " + w })); });
      if (activate && !amount.dataset.touched) amount.value = String(q.amount_due);
    }
    function refresh() {
      if (!pack.value) { recap.innerHTML = ""; recap.appendChild(el("p", { class: "text-gray-400", text: "Aucun pack disponible pour cette formule." })); return; }
      if (pack.value === "custom" && (!cSize.value || cPrice.value === "")) { recap.innerHTML = ""; recap.appendChild(el("p", { class: "text-gray-400", text: "Indiquez la taille et le prix du pack sur mesure." })); lastQuote = null; return; }
      btApi.post("/admin/invitations/events/" + S.eventId + "/packs/quote", payload()).then(renderQuote).catch(function (e) {
        lastQuote = null; recap.innerHTML = ""; recap.appendChild(el("p", { class: "text-sm font-bold text-red-600", text: e.message || "Devis impossible" }));
      });
    }
    function schedule() { clearTimeout(timer); timer = setTimeout(refresh, 300); }
    function sync() {
      customBox.classList.toggle("hidden", pack.value !== "custom");
      codeBox.classList.toggle("hidden", offered.checked || dMode.value !== "code");
      manualBox.classList.toggle("hidden", offered.checked || dMode.value !== "manual");
      dMode.disabled = offered.checked;
      offeredBox.classList.toggle("hidden", !offered.checked);
      payBox.classList.toggle("hidden", !activate || offered.checked);
      var c = active.filter(function (x) { return x.code === channel.value; })[0];
      refLbl.textContent = c ? c.ref_label + " (obligatoire)" : "Référence";
    }
    formula.addEventListener("change", function () { fillPacks(); sync(); schedule(); });
    [pack, dMode, offered, channel].forEach(function (x) { x.addEventListener("change", function () { sync(); schedule(); }); });
    [cSize, cPrice, cReason, dCode, mValue, mReason, offeredReason].forEach(function (x) { x.addEventListener("input", schedule); });
    mKind.addEventListener("change", schedule);
    amount.addEventListener("input", function () { amount.dataset.touched = "1"; });
    fillPacks(); sync(); refresh();

    var body = el("div", { class: "space-y-3" }, [
      el("div", { class: "grid sm:grid-cols-2 gap-3" }, [field("Formule", formula), field("Pack", pack)]),
      customBox,
      el("div", {}, [lbl("Options"), optBox]),
      el("div", { class: "grid sm:grid-cols-2 gap-3" }, [field("Remise (une seule par commande)", dMode), codeBox]),
      manualBox,
      admin ? el("label", { class: "flex items-center gap-2 text-sm font-semibold text-purple-800" }, [offered, "Pack offert (total 0, sans paiement — admin, motif obligatoire)"]) : null,
      offeredBox,
      el("div", {}, [lbl("Récapitulatif"), recap]),
      activate ? payBox : null,
    ]);
    modal(activate ? "Activer un pack" : "Devis", body, function (close) {
      if (!activate) return [el("button", { class: "action-btn btn-primary", text: "Copier le devis", onclick: function () {
        if (!lastQuote) { toast("Devis incomplet.", "error"); return; }
        var q = lastQuote, t = ["Devis GoEvent — Invitations digitales", S.ev.title + " (" + S.ev.event_date_label + ")", "",
          q.formula.name + " — " + q.pack.size + " personnes : " + fcfa(q.pack.unit_price)];
        q.options.forEach(function (o) { t.push(o.name + (o.unit === "flat" ? "" : " × " + o.qty) + " : " + fcfa(o.total)); });
        if (q.discount) t.push("Remise : −" + fcfa(q.discount.amount));
        if (q.vat.enabled) t.push("Total HT : " + fcfa(q.vat.ht) + " · TVA : " + fcfa(q.vat.vat));
        t.push((q.vat.enabled ? "Total TTC : " : "Total : ") + fcfa(q.amount_due));
        if (q.agents_included) t.push(q.agents_included + " agent(s) GoEvent inclus");
        t.push("", "Paiement à GoEvent SARLU (espèces contre reçu ou Orange Money).");
        copy(t.join("\n"), "Devis");
      } })];
      var b = el("button", { class: "action-btn btn-primary", text: "Activer", onclick: function () {
        if (!lastQuote) { toast("Le récapitulatif doit être valide avant l'activation.", "error"); return; }
        if (lastQuote.blocking.length) { toast(lastQuote.blocking[0], "error"); return; }
        var body2 = payload();
        if (!offered.checked) {
          if (!confirm.checked) { toast("Cochez la confirmation de réception du paiement.", "error"); return; }
          if (ref.value.trim().length < 3) { toast("Référence du paiement obligatoire.", "error"); return; }
          body2.payment_channel = channel.value; body2.payment_ref = ref.value.trim();
          body2.amount_received = parseInt(amount.value, 10) || 0; body2.confirmed_goevent_account = true;
        }
        body2.note = note.value.trim() || null;
        if (offered.checked && !window.confirm("Activer ce pack OFFERT (total 0, aucune recette) ?")) return;
        b.disabled = true;
        btApi.post("/admin/invitations/events/" + S.eventId + "/packs", body2)
          .then(function () { close(); toast(offered.checked ? "Pack offert activé." : "Pack activé.", "success"); reloadEvent(); })
          .catch(function (e) { b.disabled = false; toast(e.message || "Erreur", "error"); });
      } });
      return [b];
    });
  }

  // ── Invités : import, liste, envoi WhatsApp ──────────────────────
  function guestsBlock() {
    var paste = el("textarea", { class: "input-field font-mono text-xs", rows: "5", id: "inv-paste",
      placeholder: "Collez ici les colonnes depuis Excel (ou un CSV) :\nNom ⇥ Téléphone ⇥ Places ⇥ Table ⇥ Catégorie ⇥ Papier\nJean Kossi ⇥ 75 00 00 01 ⇥ 2 ⇥ T1 ⇥ Famille ⇥ non" });
    var file = el("input", { type: "file", accept: ".xlsx,.docx,.csv,.txt,.doc,.xls", class: "text-xs", onchange: function (e) {
      var f = e.target.files[0];
      if (!f) return;
      var name = f.name.toLowerCase();
      if (/\.(csv|txt)$/.test(name)) {
        var r = new FileReader();
        r.onload = function () { paste.value = String(r.result || ""); runImport(paste.value, true); };
        r.readAsText(f, "utf-8");
        return;
      }
      if (/\.(doc|xls)$/.test(name)) {
        toast("Ancien format (.doc / .xls) : ouvrez le fichier et enregistrez-le en .docx ou .xlsx, ou copiez-collez la liste.", "error");
        e.target.value = "";
        return;
      }
      if (f.size > 2 * 1024 * 1024) { toast("Fichier trop lourd (2 Mo maximum).", "error"); e.target.value = ""; return; }
      // .xlsx / .docx : lus par le serveur (contenu renvoyé, rien n'est stocké), puis vérification habituelle
      var fd = new FormData();
      fd.append("file", f);
      fetch(BT_API + "/admin/invitations/events/" + S.eventId + "/guests/read-file", {
        method: "POST", body: fd, headers: { Authorization: "Bearer " + btAuth.getToken() },
      }).then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (d) {
          if (!res.ok) throw new Error(typeof d.detail === "string" ? d.detail : "Lecture du fichier impossible.");
          return d;
        });
      }).then(function (d) {
        paste.value = d.text;
        toast(d.rows + " ligne(s) lue(s) dans le fichier.", "success");
        runImport(paste.value, true);
      }).catch(function (err) { toast(err.message || "Lecture du fichier impossible.", "error"); })
        .then(function () { e.target.value = ""; });
    } });
    var report = el("div", { id: "inv-import-report" });
    var importBox = el("details", { class: "mb-4", open: S.ev.listed ? null : "open" }, [
      el("summary", { class: "cursor-pointer font-bold text-sm text-primary mb-2", text: "Importer des invités" }),
      el("p", { class: "text-[11px] text-gray-500 mb-2", text: "Fichier Excel (.xlsx), Word (.docx, tableau), CSV, ou copier-coller. Colonnes dans cet ordre : Nom, Téléphone (8 chiffres = +236, ou format international), Places (1 à 20, défaut 1), Table, Catégorie, Papier (« oui » = pass imprimé ; vide = papier seulement si pas de numéro) — ou une ligne de titres (« Nom », « Téléphone »…). Anciens formats .doc / .xls : enregistrez-les d'abord en .docx / .xlsx." }),
      paste,
      el("div", { class: "flex flex-wrap items-center gap-3 mt-2" }, [file,
        el("button", { class: "action-btn btn-primary text-xs", text: "Vérifier la liste", onclick: function () { runImport(paste.value, true); } })]),
      report,
    ]);
    var search = el("input", { class: "input-field", style: "max-width:260px", placeholder: "Rechercher (nom, numéro, table)", value: S.q,
      oninput: function (e) { S.q = e.target.value; S.page = 1; clearTimeout(S._t); S._t = setTimeout(loadGuests, 300); } });
    var filter = el("select", { class: "input-field", style: "max-width:200px", onchange: function (e) { S.status = e.target.value; S.page = 1; loadGuests(); } },
      [["", "Tous"], ["pending", "À envoyer"], ["sent", "Envoyés"], ["paper", "Pass papier"], ["arrived", "Entrés"], ["not_arrived", "Pas encore arrivés"], ["revoked", "Annulés"]].map(function (o) {
        return el("option", { value: o[0], text: o[1], selected: S.status === o[0] ? "selected" : null });
      }));
    var sendBtn = el("button", { class: "action-btn text-xs", id: "inv-send-next", style: "background:#25d366;color:#fff", onclick: sendNext });
    return block("Invités", [importBox,
      el("div", { class: "flex flex-wrap items-center gap-2 mb-3" }, [search, filter, el("span", { class: "flex-1" }), sendBtn]),
      el("p", { class: "text-[11px] text-gray-500 mb-2", text: "« Envoyer au suivant » ouvre WhatsApp avec le message prêt pour le prochain invité non servi : appuyez sur Envoyer dans WhatsApp, revenez ici, recommencez." }),
      el("p", { id: "inv-nophone", class: "hidden text-xs font-semibold text-amber-800 bg-amber-50 rounded-xl p-3 mb-2" }),
      el("div", { id: "inv-paper", class: "hidden flex flex-wrap items-center gap-2 text-xs font-semibold text-orange-900 bg-orange-50 rounded-xl p-3 mb-2" }),
      el("div", { id: "inv-guests" })]);
  }

  // Analyse du texte collé : tabulations (Excel), point-virgule ou virgule (CSV), guillemets
  function parseRows(text) {
    var lines = String(text || "").replace(/\r/g, "").split("\n").filter(function (l) { return l.trim(); });
    if (!lines.length) return [];
    var sep = lines[0].indexOf("\t") >= 0 ? "\t" : (lines[0].split(";").length > lines[0].split(",").length ? ";" : ",");
    function split(line) {
      if (sep === "\t") return line.split("\t");
      var out = [], cur = "", q = false;
      for (var i = 0; i < line.length; i++) {
        var c = line[i];
        if (c === '"') { if (q && line[i + 1] === '"') { cur += '"'; i++; } else q = !q; }
        else if (c === sep && !q) { out.push(cur); cur = ""; }
        else cur += c;
      }
      out.push(cur);
      return out;
    }
    var cols = { full_name: 0, phone: 1, seats: 2, table_label: 3, category: 4, paper: 5 }, start = 0;
    var head = split(lines[0]).map(function (h) { return h.trim().toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, ""); });
    if (head.some(function (h) { return /^(nom|name|invite)/.test(h); })) {
      var find = function (re) { for (var i = 0; i < head.length; i++) if (re.test(head[i])) return i; return -1; };
      cols = { full_name: find(/^(nom|name|invite)/), phone: find(/^(tel|phone|numero|n°|no |whatsapp|gsm|portable|mobile|contact|cel)/), seats: find(/^(place|nb|nombre|pers)/),
               table_label: find(/^table/), category: find(/^(cat|groupe|type)/), paper: find(/^(papier|paper|imprim)/) };
      start = 1;
    }
    return lines.slice(start).map(function (l, i) {
      var c = split(l), get = function (k) { return cols[k] >= 0 && c[cols[k]] != null ? String(c[cols[k]]).trim() : null; };
      return { line: i + start + 1, full_name: get("full_name"), phone: get("phone"), seats: get("seats"), table_label: get("table_label"), category: get("category"), paper: get("paper") || null };
    });
  }

  function runImport(text, dry) {
    var rows = dry ? parseRows(text) : S.importRows;
    if (!rows || !rows.length) { toast("Aucune ligne à importer.", "error"); return; }
    var box = document.getElementById("inv-import-report");
    btApi.post("/admin/invitations/events/" + S.eventId + "/guests/import", { rows: rows, dry_run: dry }).then(function (r) {
      if (!dry) {
        toast(r.created + " invité(s) importé(s).", "success");
        S.importRows = null;
        var p = document.getElementById("inv-paste"); if (p) p.value = "";
        reloadEvent().then(loadGuests);
        return;
      }
      S.importRows = rows;
      box.innerHTML = "";
      var q = r.quota;
      box.appendChild(el("div", { class: "mt-3 p-3 rounded-xl " + (r.errors.length || q.exceeds ? "bg-amber-50" : "bg-green-50") }, [
        el("p", { class: "text-sm font-bold text-gray-900", text: r.valid.length + " invité(s) valide(s), " + q.adding + " personne(s) · " + r.errors.length + " ligne(s) en erreur" }),
        el("p", { class: "text-xs text-gray-600", text: q.pack ? "Pack : " + q.pack + " personnes · liste après import : " + q.after + (q.exceeds ? " → DÉPASSE LE PACK" : "")
                                                               : "Aucun pack payé : l'import est possible, l'envoi restera bloqué jusqu'à l'activation." }),
        (function () {
          var paper = r.valid.filter(function (v) { return v.paper; }).length;
          var noph = r.valid.filter(function (v) { return !v.phone && !v.paper; }).length;
          return el("div", {}, [
            paper ? el("p", { class: "text-xs font-bold text-orange-800 mt-1", text: "🖨 " + paper + " pass papier (à imprimer, remis en main propre) — " +
              "par défaut pour les invités sans numéro. Si c'est une erreur, vérifiez que la colonne Téléphone est bien la 2e (ou titrée « Téléphone »)." }) : null,
            noph ? el("p", { class: "text-xs font-bold text-amber-800 mt-1", text: "⚠ " + noph + " invité(s) sans numéro et « papier = non » : leur pass ne partira ni par WhatsApp ni à l'impression." }) : null,
          ]);
        })(),
        r.errors.length ? el("ul", { class: "text-xs text-red-700 mt-2 list-disc pl-5 max-h-40 overflow-auto" }, r.errors.map(function (e) {
          return el("li", { text: "Ligne " + e.line + (e.name ? " (" + e.name + ")" : "") + " : " + e.message });
        })) : null,
        r.valid.length && !q.exceeds ? el("button", { class: "action-btn btn-primary text-xs mt-3", text: "Importer les " + r.valid.length + " invité(s) valides",
          onclick: function () { runImport(null, false); } }) : null,
      ]));
    }).catch(function (e) { toast(e.message || "Erreur", "error"); });
  }

  function loadGuests() {
    if (!S.eventId) return;
    var qs = "?page=" + S.page + "&limit=50" + (S.q ? "&q=" + encodeURIComponent(S.q) : "") + (S.status ? "&status=" + S.status : "");
    btApi.get("/admin/invitations/events/" + S.eventId + "/guests" + qs).then(function (d) { S.guests = d; S.queue = d.send_queue; S.noPhone = d.pending_without_phone || 0;
      S.paperPending = d.paper_pending || 0; S.paperTotal = d.paper_total || 0; renderGuests(); })
      .catch(function (e) { toast(e.message || "Erreur", "error"); });
  }

  function renderGuests() {
    var box = document.getElementById("inv-guests"), d = S.guests;
    var btn = document.getElementById("inv-send-next");
    if (btn) {
      // « Tous les pass sont envoyés » seulement s'il ne reste vraiment rien à envoyer
      btn.textContent = S.queue.length ? "Envoyer au suivant sur WhatsApp (" + S.queue.length + ")"
        : S.noPhone ? "Aucun invité avec numéro à envoyer" : "Tous les pass sont envoyés";
      btn.disabled = !S.queue.length || !S.ev.quota;
      btn.title = S.ev.quota ? "" : "Activez un pack pour envoyer les pass";
    }
    var warn = document.getElementById("inv-nophone");
    if (warn) {
      warn.textContent = S.noPhone ? "⚠ " + S.noPhone + " invité(s) sans numéro : leur pass n'est PAS envoyé. Ajoutez le numéro (« Modifier ») " +
        "pour l'envoyer par WhatsApp, ou transmettez le lien vous-même (« Copier le lien »)." : "";
      warn.classList.toggle("hidden", !S.noPhone);
    }
    var paper = document.getElementById("inv-paper");
    if (paper) {
      paper.innerHTML = "";
      paper.classList.toggle("hidden", !S.paperTotal);
      if (S.paperTotal) {
        paper.appendChild(el("span", { class: "flex-1 min-w-[200px]", text: "🖨 " + S.paperTotal + " pass papier" +
          (S.paperPending ? " dont " + S.paperPending + " à imprimer" : ", tous imprimés") + " (A4, 8 pass découpables par page, QR générés chez GoEvent)." }));
        if (S.paperPending) paper.appendChild(act("Imprimer les " + S.paperPending + " nouveaux", "background:#ea580c;color:#fff", function () { downloadPaper("pending", this); }));
        paper.appendChild(act("Réimprimer tous les pass papier", "background:#fff;color:#9a3412", function () { downloadPaper("paper", this); }));
      }
    }
    if (!box || !d) return;
    box.innerHTML = "";
    if (!d.guests.length) { box.appendChild(el("p", { class: "text-sm text-gray-400 py-4 text-center", text: "Aucun invité." })); return; }
    var table = el("div", { class: "divide-y divide-gray-100" });
    d.guests.forEach(function (g) { table.appendChild(guestRow(g)); });
    box.appendChild(table);
    var pages = Math.ceil(d.total / d.limit);
    if (pages > 1) {
      box.appendChild(el("div", { class: "flex justify-center items-center gap-3 mt-3 text-sm" }, [
        el("button", { class: "action-btn text-xs", text: "←", disabled: S.page <= 1 ? "disabled" : null, onclick: function () { S.page--; loadGuests(); } }),
        el("span", { text: "Page " + S.page + " / " + pages + " · " + d.total + " invités" }),
        el("button", { class: "action-btn text-xs", text: "→", disabled: S.page >= pages ? "disabled" : null, onclick: function () { S.page++; loadGuests(); } }),
      ]));
    }
  }

  // PDF des pass papier (fetch authentifié → téléchargement). Les pass papier inclus passent à « Imprimé ».
  function downloadPaper(scope, btn) {
    if (!S.ev.quota) { toast("Activez un pack avant d'imprimer les pass.", "error"); return; }
    if (btn) btn.disabled = true;
    fetch(BT_API + "/admin/invitations/events/" + S.eventId + "/paper.pdf?scope=" + scope, {
      headers: { Authorization: "Bearer " + btAuth.getToken() },
    }).then(function (res) {
      if (!res.ok) return res.json().catch(function () { return {}; }).then(function (d) {
        throw new Error(typeof d.detail === "string" ? d.detail : "PDF indisponible.");
      });
      return res.blob();
    }).then(function (blob) {
      var url = URL.createObjectURL(blob);
      var a = el("a", { href: url, download: "pass-papier-" + S.eventId + ".pdf" });
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(function () { URL.revokeObjectURL(url); }, 30000);
      toast("PDF prêt : imprimez en A4 (taille réelle), puis découpez le long des pointillés.", "success");
      reloadEvent().then(loadGuests);
    }).catch(function (e) { toast(e.message || "PDF indisponible.", "error"); })
      .then(function () { if (btn) btn.disabled = false; });
  }

  function act(label, style, fn) { return el("button", { class: "action-btn text-[11px]", style: style, text: label, onclick: fn }); }
  function guestRow(g) {
    var st = STATUS[g.status] || STATUS.pending;
    var actions = el("div", { class: "flex flex-wrap gap-1" });
    if (g.status !== "revoked") {
      if (g.wa_url) actions.appendChild(act("WhatsApp", "background:#dcfce7;color:#166534", function () { sendGuest(g); }));
      if (g.pass_url) actions.appendChild(act("Copier le lien", "background:#f3f4f6;color:#374151", function () { copy(g.pass_url, "Lien du pass"); }));
      if (g.seats_used < g.seats) actions.appendChild(act("Entrée manuelle", "background:#fef3c7;color:#92400e", function () { manualModal(g); }));
      actions.appendChild(act("Modifier", "background:#ede9fe;color:#5b21b6", function () { guestModal(g); }));
      actions.appendChild(act("Nouveau lien", "background:#f3f4f6;color:#374151", function () {
        if (!window.confirm("Générer un nouveau lien pour " + g.full_name + " ? L'ancien pass ne fonctionnera plus : il faudra le renvoyer.")) return;
        btApi.post("/admin/invitations/guests/" + g.id + "/regenerate").then(function () { toast("Nouveau lien généré : pensez à le renvoyer.", "success"); loadGuests(); })
          .catch(function (e) { toast(e.message || "Erreur", "error"); });
      }));
      actions.appendChild(act("Annuler le pass", "background:#fee2e2;color:#991b1b", function () {
        if (!window.confirm("Annuler le pass de " + g.full_name + " ? Il sera refusé à l'entrée.")) return;
        btApi.post("/admin/invitations/guests/" + g.id + "/revoke").then(function () { toast("Pass annulé.", "success"); reloadEvent().then(loadGuests); })
          .catch(function (e) { toast(e.message || "Erreur", "error"); });
      }));
    } else {
      actions.appendChild(act("Restaurer", "background:#dcfce7;color:#166534", function () {
        btApi.post("/admin/invitations/guests/" + g.id + "/restore").then(function () { toast("Pass restauré.", "success"); reloadEvent().then(loadGuests); })
          .catch(function (e) { toast(e.message || "Erreur", "error"); });
      }));
    }
    if (!g.seats_used) actions.appendChild(act("Supprimer", "background:#fee2e2;color:#991b1b", function () {
      if (!window.confirm("Supprimer définitivement " + g.full_name + " de la liste ?")) return;
      btApi.delete("/admin/invitations/guests/" + g.id).then(function () { toast("Invité supprimé.", "success"); reloadEvent().then(loadGuests); })
        .catch(function (e) { toast(e.message || "Erreur", "error"); });
    }));
    return el("div", { class: "py-3 flex flex-wrap items-center gap-3" }, [
      el("div", { class: "flex-1 min-w-[200px]" }, [
        el("div", { class: "flex flex-wrap items-center gap-2" }, [
          el("span", { class: "font-bold text-sm text-gray-900", text: g.full_name }),
          el("span", { class: "text-[10px] font-black px-2 py-0.5 rounded-full " + st[1], text: st[0] }),
        ]),
        el("p", { class: "text-[11px] text-gray-500", text: [g.paper ? "pass papier" : null, g.phone || "sans numéro", g.seats + " place(s)" + (g.seats_used ? " · " + g.seats_used + " entrée(s)" : ""),
          g.table_label ? "table " + g.table_label : null, g.category].filter(Boolean).join(" · ") }),
      ]),
      actions,
    ]);
  }

  // Ouvre WhatsApp (clic utilisateur → pas de blocage de fenêtre) puis marque « envoyé »
  function sendGuest(g) {
    if (!S.ev.quota) { toast("Activez un pack avant d'envoyer les pass.", "error"); return; }
    var w = window.open(g.wa_url, "_blank");
    if (w) w.opener = null;
    else { toast("Le navigateur a bloqué l'ouverture de WhatsApp : autorisez les fenêtres pour ce site.", "error"); return; }
    btApi.post("/admin/invitations/guests/" + g.id + "/sent").then(function () {
      S.queue = S.queue.filter(function (id) { return id !== g.id; });
      loadGuests();
    }).catch(function (e) { toast(e.message || "Erreur", "error"); });
  }
  function sendNext() {
    if (!S.queue.length) return;
    btApi.get("/admin/invitations/guests/" + S.queue[0]).then(sendGuest).catch(function (e) { toast(e.message || "Erreur", "error"); });
  }

  function guestModal(g) {
    var f = { name: el("input", { class: "input-field", maxlength: "120" }), phone: el("input", { class: "input-field", maxlength: "40" }),
              seats: el("input", { class: "input-field", type: "number", min: String(Math.max(1, g.seats_used)), max: "20" }),
              table: el("input", { class: "input-field", maxlength: "40" }), cat: el("input", { class: "input-field", maxlength: "40" }),
              paper: el("input", { type: "checkbox" }) };
    f.paper.checked = !!g.paper;
    f.name.value = g.full_name; f.phone.value = g.phone || ""; f.seats.value = g.seats; f.table.value = g.table_label || ""; f.cat.value = g.category || "";
    var body = el("div", { class: "space-y-3" }, [field("Nom", f.name), field("Téléphone", f.phone, "Le changer ne renvoie pas le pass : utilisez WhatsApp après."),
      el("div", { class: "grid grid-cols-3 gap-3" }, [field("Places", f.seats), field("Table", f.table), field("Catégorie", f.cat)]),
      el("label", { class: "flex items-center gap-2 text-sm font-semibold text-gray-800" }, [f.paper, "Pass papier (imprimé et remis en main propre, pas d'envoi WhatsApp)"])]);
    modal("Modifier l'invité", body, function (close) {
      var b = el("button", { class: "action-btn btn-primary", text: "Enregistrer", onclick: function () {
        btApi.patch("/admin/invitations/guests/" + g.id, { full_name: f.name.value.trim(), phone: f.phone.value.trim() || null,
          seats: parseInt(f.seats.value, 10) || 1, table_label: f.table.value.trim() || null, category: f.cat.value.trim() || null,
          paper: f.paper.checked })
          .then(function () { close(); toast("Invité enregistré.", "success"); reloadEvent().then(loadGuests); })
          .catch(function (e) { toast(e.message || "Erreur", "error"); });
      } });
      return [b];
    });
  }

  function manualModal(g) {
    var remaining = g.seats - g.seats_used;
    var count = el("input", { class: "input-field", type: "number", min: "1", max: String(remaining), value: String(remaining) });
    var reason = el("input", { class: "input-field", maxlength: "200", placeholder: "Ex. téléphone déchargé, identité vérifiée" });
    modal("Entrée manuelle : " + g.full_name, el("div", { class: "space-y-3" }, [field("Personnes qui entrent (" + remaining + " restante(s))", count), field("Motif (obligatoire)", reason)]), function (close) {
      var b = el("button", { class: "action-btn btn-primary", text: "Valider l'entrée", onclick: function () {
        var n = parseInt(count.value, 10);
        if (!(n >= 1 && n <= remaining)) { toast("Nombre de personnes : entre 1 et " + remaining + ".", "error"); return; }
        if (reason.value.trim().length < 3) { toast("Indiquez le motif de l'entrée manuelle (3 caractères minimum).", "error"); reason.focus(); return; }
        b.disabled = true;
        btApi.post("/admin/invitations/guests/" + g.id + "/checkin", { count: n, reason: reason.value.trim() })
          .then(function () { close(); toast("Entrée enregistrée.", "success"); reloadEvent().then(loadGuests); })
          .catch(function (e) { b.disabled = false; toast(e.message || "Erreur", "error"); });
      } });
      return [b];
    });
  }

  // ── Suivi en direct + lien de suivi client ────────────────────────
  function liveBlock() {
    var ev = S.ev, items = [];
    var track = el("div", { class: "p-3 rounded-xl bg-gray-50 mb-3" });
    if (ev.quota && ev.features && ev.features.track_link) {
      track.appendChild(el("p", { class: "text-sm font-bold text-gray-900", text: "Lien de suivi pour le client" }));
      track.appendChild(el("p", { class: "text-[11px] text-gray-500 mb-2", text: "Page publique : titre, date et chiffres (attendus, entrés, restants, taux) actualisés toutes les 30 s. Aucun nom, aucun numéro, pas le lieu." }));
      if (ev.track_url) {
        track.appendChild(el("div", { class: "flex flex-wrap gap-2 items-center" }, [
          el("code", { class: "text-xs bg-white px-2 py-1 rounded border border-gray-200 break-all", text: ev.track_url }),
          act("Copier", "background:#f3f4f6;color:#374151", function () { copy(ev.track_url, "Lien de suivi"); }),
          act("Régénérer", "background:#ede9fe;color:#5b21b6", function () {
            if (!window.confirm("Régénérer le lien ? L'ancien ne fonctionnera plus.")) return;
            btApi.post("/admin/invitations/events/" + ev.id + "/track-link").then(function () { toast("Nouveau lien de suivi.", "success"); reloadEvent(); });
          }),
          act("Révoquer", "background:#fee2e2;color:#991b1b", function () {
            btApi.delete("/admin/invitations/events/" + ev.id + "/track-link").then(function () { toast("Lien de suivi révoqué.", "success"); reloadEvent(); });
          }),
        ]));
      } else {
        track.appendChild(act("Créer le lien de suivi", "background:#dcfce7;color:#166534", function () {
          btApi.post("/admin/invitations/events/" + ev.id + "/track-link").then(function () { toast("Lien de suivi créé.", "success"); reloadEvent(); })
            .catch(function (e) { toast(e.message || "Erreur", "error"); });
        }));
      }
    } else {
      track.appendChild(el("p", { class: "text-[11px] text-gray-500", text: "Lien de suivi client : inclus dans les formules qui le prévoient (aucun pack actif ne l'inclut)." }));
    }
    items.push(track);
    items.push(el("div", { id: "inv-arrivals" }, [el("p", { class: "text-xs text-gray-400", text: "Chargement des arrivées…" })]));
    setTimeout(refreshLive, 0);
    return block("Suivi en direct", items);
  }
  function renderArrivals(list) {
    var box = document.getElementById("inv-arrivals");
    if (!box) return;
    box.innerHTML = "";
    if (!list.length) { box.appendChild(el("p", { class: "text-xs text-gray-400", text: "Aucune arrivée pour l'instant." })); return; }
    box.appendChild(el("p", { class: "text-[11px] font-black uppercase tracking-widest text-gray-400 mb-1", text: "Dernières arrivées" }));
    list.forEach(function (a) {
      box.appendChild(el("p", { class: "text-xs " + (a.conflict ? "text-red-700 font-bold" : "text-gray-700"), text: dt(a.at) + " · " + a.name +
        (a.table ? " (table " + a.table + ")" : "") + " · " + a.count + " pers. · " + ({ online: "scan", offline: "scan hors ligne", manual: "manuel" }[a.source] || a.source) +
        (a.conflict ? " · CONFLIT" : "") }));
    });
  }

  // ── Message WhatsApp ──────────────────────────────────────────────
  function messageBlock() {
    var ev = S.ev, m = S.meta;
    var ta = el("textarea", { class: "input-field text-sm", rows: "6", maxlength: "1000" });
    ta.value = ev.message_template || m.default_message;
    var save = el("button", { class: "action-btn btn-primary text-xs", text: "Enregistrer le message", onclick: function () {
      btApi.patch("/admin/invitations/events/" + ev.id, { message_template: ta.value }).then(function () { toast("Message enregistré.", "success"); reloadEvent().then(loadGuests); })
        .catch(function (e) { toast(e.message || "Erreur", "error"); });
    } });
    var reset = el("button", { class: "action-btn text-xs", style: "background:#f3f4f6;color:#374151", text: "Revenir au message par défaut", onclick: function () {
      btApi.patch("/admin/invitations/events/" + ev.id, { reset_message: true }).then(function () { toast("Message par défaut rétabli.", "success"); reloadEvent().then(loadGuests); });
    } });
    return block("Message WhatsApp", [
      el("p", { class: "text-[11px] text-gray-500 mb-2", text: "Variables : " + m.message_vars.map(function (v) { return "{" + v + "}"; }).join(" ") + " — {lien} est obligatoire." + (ev.message_is_default ? " (message par défaut)" : "") }),
      ta, el("div", { class: "flex flex-wrap gap-2 mt-2" }, [save, ev.message_is_default ? null : reset]),
    ]);
  }

  // ── Agents de contrôle ────────────────────────────────────────────
  function agentsBlock() {
    var ev = S.ev, list = el("div", { class: "space-y-1 mb-3" });
    if (!ev.agents.length) list.appendChild(el("p", { class: "text-xs text-gray-400", text: "Aucun agent assigné." }));
    ev.agents.forEach(function (a) {
      list.appendChild(el("div", { class: "flex items-center gap-2 text-sm" }, [
        el("span", { class: "flex-1", text: a.full_name + " · " + a.phone_number }),
        act("Retirer", "background:#fee2e2;color:#991b1b", function () {
          btApi.delete("/admin/invitations/events/" + ev.id + "/agents/" + a.id).then(function () { toast("Agent retiré.", "success"); reloadEvent(); });
        }),
      ]));
    });
    var name = el("input", { class: "input-field", placeholder: "Nom", maxlength: "120" }), phone = el("input", { class: "input-field", placeholder: "Téléphone (+236…)", maxlength: "30" });
    var pin = el("input", { class: "input-field", placeholder: "PIN (6 chiffres)", maxlength: "6", inputmode: "numeric" });
    var onsite = ev.features && ev.features.onsite_agents;
    var over = onsite && ev.quota && ev.agents.length > (ev.agents_included || 0);
    return block("Agents de contrôle", [
      el("p", { class: "text-[11px] text-gray-500 mb-2", text: onsite ? "Agents GoEvent sur place. Ils se connectent sur goevent.africa avec ce numéro et ce PIN, puis scannent avec « Scanner »." :
        "Formule sans agents GoEvent : créez les comptes des personnes du client qui scanneront à l'entrée (numéro + PIN à leur transmettre)." }),
      onsite && ev.quota ? el("p", { class: "text-xs mb-2 " + (over ? "font-bold text-amber-800 bg-amber-50 rounded-lg p-2" : "text-gray-600"),
        text: (ev.agents_included || 0) + " agent(s) inclus · " + ev.agents.length + " assigné(s)" + (over ? " : au-delà de l'inclus, proposez l'option « agent supplémentaire »." : "") }) : null,
      list,
      el("div", { class: "grid sm:grid-cols-4 gap-2" }, [name, phone, pin, el("button", { class: "action-btn btn-primary text-xs", text: "Ajouter l'agent", onclick: function () {
        btApi.post("/admin/invitations/events/" + ev.id + "/agents", { full_name: name.value.trim(), phone_number: phone.value.trim(), pin_code: pin.value.trim() })
          .then(function () { toast("Agent ajouté.", "success"); reloadEvent(); })
          .catch(function (e) { toast(e.message || "Erreur", "error"); });
      } })]),
    ]);
  }

  // ── Données personnelles ──────────────────────────────────────────
  function dataBlock() {
    var ev = S.ev, items = [el("p", { class: "text-xs text-gray-600", text: ev.purged ? "Liste anonymisée." :
      "Noms et numéros des invités supprimés automatiquement le " + dt(ev.purge_after) + " (" + S.meta.retention_days + " jours après l'événement). Ils ne servent qu'à cet événement : jamais d'export d'audience ni de publicité." })];
    if (!ev.purged && canActivate()) items.push(el("button", { class: "action-btn btn-danger text-xs mt-2", text: "Supprimer la liste maintenant", onclick: function () {
      if (!window.confirm("Anonymiser dès maintenant tous les invités de cet événement ? Les pass ne fonctionneront plus. Action irréversible.")) return;
      btApi.post("/admin/invitations/purge?event_id=" + ev.id).then(function (r) { toast(r.anonymized + " invité(s) anonymisé(s).", "success"); openEvent(ev.id); })
        .catch(function (e) { toast(e.message || "Erreur", "error"); });
    } }));
    return block("Données personnelles", items);
  }

  // ════════════════════════════════════════════════════════════════
  // CATALOGUE DES PACKS (lecture admin + commercial, modification admin)
  // ════════════════════════════════════════════════════════════════
  var CAT = { tab: "formulas", data: null, archived: false, packFormula: "" };
  var UNIT_TXT = { flat: "forfait", per_unit: "par unité", per_page: "par page" };
  var ACTION_TXT = { create: "Création", update: "Modification", archive: "Archivage", restore: "Restauration", "delete": "Suppression", seed: "Reprise" };
  var ENTITY_TXT = { formula: "Formule", pack: "Pack", option: "Option", discount: "Code", settings: "Réglages" };
  var FIELD_TXT = { name: "nom", description: "description", included: "inclus", badge: "badge", public: "visible", onsite_agents: "agents sur place",
    track_link: "lien de suivi", team_sends: "envoi par l'équipe", agents_step: "invités par agent", sort_order: "ordre", status: "statut",
    size: "taille", price_fcfa: "prix", on_quote: "sur devis", valid_from: "début", valid_until: "fin", unit: "unité", unit_label: "libellé d'unité",
    max_qty: "quantité max", agents_per_unit: "agents par unité", formula_ids: "formules", pack_ids: "packs", kind: "type", value: "valeur",
    max_uses: "utilisations max", note: "note", code: "code", formula_id: "formule", manual_discount_max_pct: "plafond remise manuelle (%)",
    vat_enabled: "TVA activée", vat_rate_bp: "taux TVA (pb)", prices_include_vat: "prix TTC" };

  function canEditCat() { return CAT.data && CAT.data.can_edit; }
  function fname(id) { var f = CAT.data.formulas.filter(function (x) { return x.id === id; })[0]; return f ? f.name : "?"; }
  function chip(text, cls) { return el("span", { class: "text-[10px] font-black px-2 py-0.5 rounded-full " + cls, text: text }); }
  function statusChip(o) { return o.status === "archived" ? chip("Archivé", "bg-gray-200 text-gray-600") : null; }
  function catCall(promise, okMsg) {
    return promise.then(function () { toast(okMsg, "success"); return loadCatalog(); })
      .catch(function (e) { toast(e.message || "Erreur", "error"); throw e; });
  }
  function rowActions(kind, o, editFn) {
    if (!canEditCat()) return null;
    var base = "/admin/invitations/catalog/" + kind + "/" + o.id;
    var box = el("div", { class: "flex flex-wrap gap-1" });
    box.appendChild(act("Modifier", "background:#ede9fe;color:#5b21b6", function () { editFn(o); }));
    if (o.status === "active") box.appendChild(act("Archiver", "background:#f3f4f6;color:#374151", function () {
      if (window.confirm("Archiver ? L'élément sera masqué partout mais gardé dans l'historique des commandes.")) catCall(btApi.post(base + "/archive"), "Archivé.").catch(function () {});
    }));
    else box.appendChild(act("Restaurer", "background:#dcfce7;color:#166534", function () { catCall(btApi.post(base + "/restore"), "Restauré.").catch(function () {}); }));
    if (!o.used) box.appendChild(act("Supprimer", "background:#fee2e2;color:#991b1b", function () {
      if (window.confirm("Supprimer définitivement (jamais utilisé dans une commande) ?")) catCall(btApi.delete(base), "Supprimé.").catch(function () {});
    }));
    return box;
  }
  function checks(items, selected) {
    var box = el("div", { class: "flex flex-wrap gap-3" }), inputs = [];
    items.forEach(function (it) {
      var c = el("input", { type: "checkbox", value: String(it.id) });
      c.checked = selected.indexOf(it.id) >= 0;
      inputs.push(c);
      box.appendChild(el("label", { class: "flex items-center gap-1 text-sm" }, [c, it.label]));
    });
    box.values = function () { return inputs.filter(function (c) { return c.checked; }).map(function (c) { return parseInt(c.value, 10); }); };
    return box;
  }
  function numOrNull(input) { return input.value === "" ? null : parseInt(input.value, 10); }

  function openCatalog(tab) {
    stopTimer();
    S.view = "catalog"; CAT.tab = tab || CAT.tab;
    sec().innerHTML = '<div class="card p-6 empty-state">Chargement…</div>';
    loadCatalog();
  }
  function loadCatalog() {
    return btApi.get("/admin/invitations/catalog").then(function (d) {
      CAT.data = d;
      return btApi.get("/admin/invitations/meta").then(function (m) { S.meta = m; renderCatalog(); });
    }).catch(function (e) { toast(e.message || "Erreur", "error"); });
  }
  function renderCatalog() {
    var root = sec(), d = CAT.data;
    root.innerHTML = "";
    root.appendChild(el("button", { class: "text-sm font-bold text-primary mb-3", text: "← Invitations", onclick: loadList }));
    root.appendChild(el("div", { class: "flex flex-wrap justify-between items-start gap-3 mb-3" }, [
      el("div", {}, [
        el("h2", { class: "text-xl font-black text-gray-900", text: "Catalogue des packs" }),
        el("p", { class: "text-xs text-gray-500 mt-1", text: (d.can_edit ? "" : "Lecture seule (modification : administrateur). ") +
          "Ce catalogue alimente les devis, l'activation et la future page publique : prix affiché = prix facturé. Un pack déjà activé garde son prix et son contenu." }),
      ]),
      el("label", { class: "flex items-center gap-2 text-xs font-bold text-gray-600" }, [
        (function () { var c = el("input", { type: "checkbox", onchange: function (e) { CAT.archived = e.target.checked; renderCatalog(); } }); c.checked = CAT.archived; return c; })(),
        "Afficher les éléments archivés"]),
    ]));
    var tabs = [["formulas", "Formules"], ["packs", "Packs"], ["options", "Options"], ["discounts", "Codes de remise"], ["settings", "Réglages"], ["history", "Historique"]];
    root.appendChild(el("div", { class: "flex flex-wrap gap-2 mb-4" }, tabs.map(function (t) {
      return el("button", { class: "action-btn text-xs", style: CAT.tab === t[0] ? "background:#00502c;color:#fff" : "background:#f3f4f6;color:#374151",
        text: t[1], onclick: function () { CAT.tab = t[0]; renderCatalog(); } });
    })));
    var body = el("div", { id: "inv-cat-body" });
    root.appendChild(body);
    ({ formulas: catFormulas, packs: catPacks, options: catOptions, discounts: catDiscounts, settings: catSettings, history: catHistory })[CAT.tab](body);
  }
  function visible(list) { return list.filter(function (o) { return CAT.archived || o.status === "active"; }); }

  // ── Formules
  function catFormulas(box) {
    if (canEditCat()) box.appendChild(el("button", { class: "action-btn btn-primary text-xs mb-3", text: "+ Nouvelle formule", onclick: function () { formulaModal(null); } }));
    var grid = el("div", { class: "grid gap-3 md:grid-cols-2" });
    visible(CAT.data.formulas).forEach(function (f) {
      var feats = [f.onsite_agents ? "Agents GoEvent sur place" + (f.agents_step ? " (1 / " + f.agents_step + " invités)" : "") : null,
                   f.track_link ? "Lien de suivi client" : null, f.team_sends ? "Envoi des pass par l'équipe" : null].filter(Boolean);
      grid.appendChild(el("div", { class: "card p-4 " + (f.status === "archived" ? "opacity-60" : "") }, [
        el("div", { class: "flex flex-wrap items-center gap-2" }, [
          el("p", { class: "font-black text-gray-900", text: f.name }), el("code", { class: "text-[10px] text-gray-400", text: f.code }),
          f.badge ? chip(f.badge, "bg-amber-100 text-amber-800") : null, f.public ? null : chip("Masquée au public", "bg-gray-100 text-gray-600"), statusChip(f),
        ]),
        f.description ? el("p", { class: "text-xs text-gray-600 mt-1", text: f.description }) : null,
        f.included.length ? el("ul", { class: "text-xs text-gray-700 mt-2 list-disc pl-4" }, f.included.map(function (t) { return el("li", { text: t }); })) : null,
        el("p", { class: "text-[11px] text-gray-500 mt-2", text: feats.length ? feats.join(" · ") : "Aucun service GoEvent sur place (le client gère)" }),
        el("div", { class: "mt-3" }, [rowActions("formulas", f, formulaModal)]),
      ]));
    });
    box.appendChild(grid);
  }
  function formulaModal(f) {
    var x = {
      code: el("input", { class: "input-field", maxlength: "20", placeholder: "ex. prestige (minuscules, immuable)" }),
      name: el("input", { class: "input-field", maxlength: "60" }), desc: el("input", { class: "input-field", maxlength: "300" }),
      included: el("textarea", { class: "input-field text-sm", rows: "4", placeholder: "Une puce par ligne (12 max)" }),
      badge: el("input", { class: "input-field", maxlength: "30", placeholder: "Ex. Populaire" }), order: el("input", { class: "input-field", type: "number", min: "0" }),
      pub: el("input", { type: "checkbox" }), onsite: el("input", { type: "checkbox" }), track: el("input", { type: "checkbox" }), team: el("input", { type: "checkbox" }),
      step: el("input", { class: "input-field", type: "number", min: "1", max: "5000", placeholder: "ex. 300" }),
    };
    if (f) {
      x.code.value = f.code; x.code.disabled = true; x.name.value = f.name; x.desc.value = f.description || ""; x.included.value = f.included.join("\n");
      x.badge.value = f.badge || ""; x.order.value = f.sort_order; x.pub.checked = f.public; x.onsite.checked = f.onsite_agents; x.track.checked = f.track_link;
      x.team.checked = f.team_sends; x.step.value = f.agents_step || "";
    } else { x.pub.checked = true; x.order.value = String(CAT.data.formulas.length + 1); }
    var cb = function (c, t) { return el("label", { class: "flex items-center gap-2 text-sm font-semibold text-gray-800" }, [c, t]); };
    var body = el("div", { class: "space-y-3" }, [
      el("div", { class: "grid sm:grid-cols-2 gap-3" }, [field("Code technique", x.code, "Immuable après création."), field("Nom affiché", x.name)]),
      field("Description courte", x.desc), field("Inclus (puces)", x.included),
      el("div", { class: "grid sm:grid-cols-2 gap-3" }, [field("Badge (facultatif)", x.badge), field("Ordre d'affichage", x.order)]),
      cb(x.pub, "Visible sur la page publique"),
      el("div", { class: "p-3 rounded-xl bg-gray-50 space-y-2" }, [el("p", { class: "text-[11px] font-black uppercase tracking-widest text-gray-400", text: "Comportements techniques" }),
        cb(x.onsite, "Agents GoEvent sur place"), field("1 agent inclus par tranche de … invités", x.step), cb(x.track, "Lien de suivi client"),
        cb(x.team, "Envoi des pass par l'équipe GoEvent")]),
    ]);
    modal(f ? "Modifier la formule" : "Nouvelle formule", body, function (close) {
      var b = el("button", { class: "action-btn btn-primary", text: "Enregistrer", onclick: function () {
        var payload = { name: x.name.value.trim(), description: x.desc.value.trim() || null,
          included: x.included.value.split("\n").map(function (t) { return t.trim(); }).filter(Boolean),
          badge: x.badge.value.trim() || null, sort_order: numOrNull(x.order) || 0, public: x.pub.checked,
          onsite_agents: x.onsite.checked, track_link: x.track.checked, team_sends: x.team.checked, agents_step: numOrNull(x.step) };
        if (!f) payload.code = x.code.value.trim();
        b.disabled = true;
        catCall(f ? btApi.patch("/admin/invitations/catalog/formulas/" + f.id, payload) : btApi.post("/admin/invitations/catalog/formulas", payload), "Formule enregistrée.")
          .then(close).catch(function () { b.disabled = false; });
      } });
      return [b];
    });
  }

  // ── Packs
  function catPacks(box) {
    var bar = el("div", { class: "flex flex-wrap items-center gap-2 mb-3" });
    var sel = el("select", { class: "input-field", style: "max-width:240px", onchange: function (e) { CAT.packFormula = e.target.value; renderCatalog(); } },
      [el("option", { value: "", text: "Toutes les formules" })].concat(CAT.data.formulas.map(function (f) {
        return el("option", { value: String(f.id), text: f.name, selected: CAT.packFormula === String(f.id) ? "selected" : null });
      })));
    bar.appendChild(sel);
    if (canEditCat()) bar.appendChild(el("button", { class: "action-btn btn-primary text-xs", text: "+ Nouveau pack", onclick: function () { packCatModal(null); } }));
    box.appendChild(bar);
    var list = visible(CAT.data.packs).filter(function (p) { return !CAT.packFormula || String(p.formula_id) === CAT.packFormula; });
    if (!list.length) { box.appendChild(el("p", { class: "text-sm text-gray-400", text: "Aucun pack." })); return; }
    var table = el("div", { class: "card divide-y divide-gray-100" });
    list.forEach(function (p) {
      var period = p.valid_from || p.valid_until ? (p.valid_from ? "du " + dayLabel(p.valid_from) : "") + (p.valid_until ? " au " + dayLabel(p.valid_until) : "") : "";
      table.appendChild(el("div", { class: "p-3 flex flex-wrap items-center gap-3 " + (p.status === "archived" ? "opacity-60" : "") }, [
        el("div", { class: "flex-1 min-w-[220px]" }, [
          el("div", { class: "flex flex-wrap items-center gap-2" }, [
            el("span", { class: "font-bold text-sm", text: fname(p.formula_id) + " · " + p.size + " personnes" + (p.name ? " · « " + p.name + " »" : "") }),
            el("span", { class: "text-sm font-black text-primary", text: p.on_quote ? "Sur devis" : fcfa(p.price_fcfa) }),
            p.public ? null : chip("Masqué au public", "bg-gray-100 text-gray-600"),
            p.status === "active" && !p.available ? chip("Hors période", "bg-amber-100 text-amber-800") : null, statusChip(p),
            p.used ? chip("Déjà vendu", "bg-blue-50 text-blue-700") : null,
          ]),
          period ? el("p", { class: "text-[11px] text-gray-500", text: "Offre valable " + period }) : null,
        ]),
        rowActions("packs", p, packCatModal),
      ]));
    });
    box.appendChild(table);
  }
  function packCatModal(p) {
    var formula = el("select", { class: "input-field" }, CAT.data.formulas.filter(function (f) { return f.status === "active" || (p && f.id === p.formula_id); }).map(function (f) {
      return el("option", { value: String(f.id), text: f.name });
    }));
    var x = { size: el("input", { class: "input-field", type: "number", min: "1", max: "5000" }), price: el("input", { class: "input-field", type: "number", min: "0" }),
      quote: el("input", { type: "checkbox" }), name: el("input", { class: "input-field", maxlength: "60", placeholder: "Ex. Petit comité (facultatif)" }),
      pub: el("input", { type: "checkbox" }), order: el("input", { class: "input-field", type: "number", min: "0" }),
      from: el("input", { class: "input-field", type: "date" }), until: el("input", { class: "input-field", type: "date" }) };
    if (p) {
      formula.value = String(p.formula_id); formula.disabled = true; x.size.value = p.size; x.price.value = p.price_fcfa === null ? "" : p.price_fcfa;
      x.quote.checked = p.on_quote; x.name.value = p.name || ""; x.pub.checked = p.public; x.order.value = p.sort_order;
      x.from.value = dateInput(p.valid_from); x.until.value = dateInput(p.valid_until);
    } else { x.pub.checked = true; if (CAT.packFormula) formula.value = CAT.packFormula; }
    var body = el("div", { class: "space-y-3" }, [
      el("div", { class: "grid sm:grid-cols-2 gap-3" }, [field("Formule", formula), field("Taille (personnes)", x.size, "1 à 5 000. Un pass « + 2 » compte 3 personnes.")]),
      el("div", { class: "grid sm:grid-cols-2 gap-3" }, [field("Prix (FCFA)", x.price), field("Nom commercial", x.name)]),
      el("label", { class: "flex items-center gap-2 text-sm font-semibold text-gray-800" }, [x.quote, "Sur devis (pas de prix affiché ; activation en « sur mesure »)"]),
      el("label", { class: "flex items-center gap-2 text-sm font-semibold text-gray-800" }, [x.pub, "Visible sur la page publique"]),
      el("div", { class: "grid sm:grid-cols-3 gap-3" }, [field("Ordre", x.order), field("Valable du (facultatif)", x.from), field("au (facultatif)", x.until)]),
      p && p.used ? el("p", { class: "text-[11px] text-blue-700", text: "Pack déjà vendu : les commandes passées gardent leur prix et leur contenu." }) : null,
    ]);
    modal(p ? "Modifier le pack" : "Nouveau pack", body, function (close) {
      var b = el("button", { class: "action-btn btn-primary", text: "Enregistrer", onclick: function () {
        var payload = { size: numOrNull(x.size), price_fcfa: x.quote.checked ? null : numOrNull(x.price), on_quote: x.quote.checked,
          name: x.name.value.trim() || null, public: x.pub.checked, valid_from: dateIso(x.from.value, false), valid_until: dateIso(x.until.value, true) };
        if (x.order.value !== "") payload.sort_order = numOrNull(x.order);
        if (!p) payload.formula_id = parseInt(formula.value, 10);
        b.disabled = true;
        catCall(p ? btApi.patch("/admin/invitations/catalog/packs/" + p.id, payload) : btApi.post("/admin/invitations/catalog/packs", payload), "Pack enregistré.")
          .then(close).catch(function () { b.disabled = false; });
      } });
      return [b];
    });
  }

  // ── Options
  function catOptions(box) {
    if (canEditCat()) box.appendChild(el("button", { class: "action-btn btn-primary text-xs mb-3", text: "+ Nouvelle option", onclick: function () { optionModal(null); } }));
    var list = visible(CAT.data.options);
    if (!list.length) { box.appendChild(el("p", { class: "text-sm text-gray-400", text: "Aucune option. Ex. agent supplémentaire (par soirée), impression des pass papier (par page), design personnalisé (forfait)." })); return; }
    var table = el("div", { class: "card divide-y divide-gray-100" });
    list.forEach(function (o) {
      table.appendChild(el("div", { class: "p-3 flex flex-wrap items-center gap-3 " + (o.status === "archived" ? "opacity-60" : "") }, [
        el("div", { class: "flex-1 min-w-[220px]" }, [
          el("div", { class: "flex flex-wrap items-center gap-2" }, [
            el("span", { class: "font-bold text-sm", text: o.name }),
            el("span", { class: "text-sm font-black text-primary", text: fcfa(o.price_fcfa) + " " + (o.unit === "flat" ? "(forfait)" : "/ " + (o.unit_label || UNIT_TXT[o.unit].replace("par ", ""))) }),
            o.public ? null : chip("Masquée au public", "bg-gray-100 text-gray-600"), statusChip(o), o.used ? chip("Déjà vendue", "bg-blue-50 text-blue-700") : null,
          ]),
          el("p", { class: "text-[11px] text-gray-500", text: "Formules : " + o.formula_ids.map(fname).join(", ") + (o.unit !== "flat" ? " · max " + o.max_qty : "") +
            (o.agents_per_unit ? " · ajoute " + o.agents_per_unit + " agent(s) par unité" : "") }),
        ]),
        rowActions("options", o, optionModal),
      ]));
    });
    box.appendChild(table);
  }
  function optionModal(o) {
    var x = { name: el("input", { class: "input-field", maxlength: "80" }), desc: el("input", { class: "input-field", maxlength: "300" }),
      price: el("input", { class: "input-field", type: "number", min: "0" }),
      unit: el("select", { class: "input-field" }, Object.keys(UNIT_TXT).map(function (k) { return el("option", { value: k, text: UNIT_TXT[k] }); })),
      label: el("input", { class: "input-field", maxlength: "30", placeholder: "Ex. agent / soirée, page" }),
      max: el("input", { class: "input-field", type: "number", min: "1", max: "1000" }), agents: el("input", { class: "input-field", type: "number", min: "0", max: "20" }),
      pub: el("input", { type: "checkbox" }), order: el("input", { class: "input-field", type: "number", min: "0" }) };
    var forms = checks(CAT.data.formulas.filter(function (f) { return f.status === "active" || (o && o.formula_ids.indexOf(f.id) >= 0); })
      .map(function (f) { return { id: f.id, label: f.name }; }), o ? o.formula_ids : []);
    if (o) {
      x.name.value = o.name; x.desc.value = o.description || ""; x.price.value = o.price_fcfa; x.unit.value = o.unit; x.label.value = o.unit_label || "";
      x.max.value = o.max_qty; x.agents.value = o.agents_per_unit; x.pub.checked = o.public; x.order.value = o.sort_order;
    } else { x.pub.checked = true; x.max.value = "1"; x.agents.value = "0"; x.order.value = "0"; }
    var body = el("div", { class: "space-y-3" }, [
      el("div", { class: "grid sm:grid-cols-2 gap-3" }, [field("Nom", x.name), field("Prix (FCFA)", x.price)]),
      field("Description (facultatif)", x.desc),
      el("div", { class: "grid sm:grid-cols-3 gap-3" }, [field("Unité", x.unit), field("Libellé de l'unité", x.label), field("Quantité max", x.max, "Forfait : 1.")]),
      el("div", { class: "grid sm:grid-cols-2 gap-3" }, [field("Agents ajoutés par unité", x.agents, "1 pour « agent supplémentaire » : compté dans les agents inclus."), field("Ordre", x.order)]),
      el("div", {}, [lbl("Formules concernées"), forms]),
      el("label", { class: "flex items-center gap-2 text-sm font-semibold text-gray-800" }, [x.pub, "Visible sur la page publique"]),
      el("p", { class: "text-[11px] text-gray-500", text: "Les options ne sont jamais remisées (codes et remises manuelles portent sur le pack uniquement)." }),
    ]);
    modal(o ? "Modifier l'option" : "Nouvelle option", body, function (close) {
      var b = el("button", { class: "action-btn btn-primary", text: "Enregistrer", onclick: function () {
        var payload = { name: x.name.value.trim(), description: x.desc.value.trim() || null, price_fcfa: numOrNull(x.price), unit: x.unit.value,
          unit_label: x.label.value.trim() || null, max_qty: numOrNull(x.max) || 1, agents_per_unit: numOrNull(x.agents) || 0,
          public: x.pub.checked, sort_order: numOrNull(x.order) || 0, formula_ids: forms.values() };
        if (!payload.formula_ids.length) { toast("Choisissez au moins une formule.", "error"); return; }
        b.disabled = true;
        catCall(o ? btApi.patch("/admin/invitations/catalog/options/" + o.id, payload) : btApi.post("/admin/invitations/catalog/options", payload), "Option enregistrée.")
          .then(close).catch(function () { b.disabled = false; });
      } });
      return [b];
    });
  }

  // ── Codes de remise
  function discountTxt(d) { return d.kind === "pct" ? "−" + d.value + " % du pack" : "−" + fcfa(d.value) + " sur le pack"; }
  function catDiscounts(box) {
    if (canEditCat()) box.appendChild(el("button", { class: "action-btn btn-primary text-xs mb-3", text: "+ Nouveau code", onclick: function () { discountModal(null); } }));
    var list = visible(CAT.data.discounts);
    if (!list.length) { box.appendChild(el("p", { class: "text-sm text-gray-400", text: "Aucun code de remise." })); return; }
    var table = el("div", { class: "card divide-y divide-gray-100" });
    list.forEach(function (d) {
      var scope = (d.formula_ids.length ? d.formula_ids.map(fname).join(", ") : "Toutes les formules") + (d.pack_ids.length ? " · " + d.pack_ids.length + " pack(s) précis" : "");
      var period = (d.valid_from ? "du " + dayLabel(d.valid_from) : "") + (d.valid_until ? " au " + dayLabel(d.valid_until) : "");
      table.appendChild(el("div", { class: "p-3 flex flex-wrap items-center gap-3 " + (d.status === "archived" ? "opacity-60" : "") }, [
        el("div", { class: "flex-1 min-w-[220px]" }, [
          el("div", { class: "flex flex-wrap items-center gap-2" }, [el("code", { class: "font-black text-sm", text: d.code }),
            el("span", { class: "text-sm font-bold text-green-700", text: discountTxt(d) }), statusChip(d)]),
          el("p", { class: "text-[11px] text-gray-500", text: "Utilisations : " + d.uses + (d.max_uses ? " / " + d.max_uses : " (illimité)") + " · " + scope + (period ? " · " + period : "") +
            (d.note ? " · " + d.note : "") }),
        ]),
        rowActions("discounts", d, discountModal),
      ]));
    });
    box.appendChild(table);
  }
  function discountModal(d) {
    var x = { code: el("input", { class: "input-field uppercase", maxlength: "40", placeholder: "Ex. PIONNIER" }),
      kind: el("select", { class: "input-field" }, [el("option", { value: "pct", text: "Pourcentage du pack" }), el("option", { value: "fixed", text: "Montant fixe (FCFA)" })]),
      value: el("input", { class: "input-field", type: "number", min: "1" }), max: el("input", { class: "input-field", type: "number", min: "1", placeholder: "Illimité" }),
      from: el("input", { class: "input-field", type: "date" }), until: el("input", { class: "input-field", type: "date" }),
      note: el("input", { class: "input-field", maxlength: "200", placeholder: "Facultatif" }) };
    var forms = checks(CAT.data.formulas.map(function (f) { return { id: f.id, label: f.name }; }), d ? d.formula_ids : []);
    var packs = checks(CAT.data.packs.filter(function (p) { return p.status === "active" || (d && d.pack_ids.indexOf(p.id) >= 0); }).map(function (p) {
      return { id: p.id, label: fname(p.formula_id) + " " + p.size }; }), d ? d.pack_ids : []);
    if (d) {
      x.code.value = d.code; x.code.disabled = true; x.kind.value = d.kind; x.value.value = d.value; x.max.value = d.max_uses || "";
      x.from.value = dateInput(d.valid_from); x.until.value = dateInput(d.valid_until); x.note.value = d.note || "";
    }
    var body = el("div", { class: "space-y-3" }, [
      el("div", { class: "grid sm:grid-cols-3 gap-3" }, [field("Code", x.code, "Immuable après création."), field("Type", x.kind), field("Valeur", x.value)]),
      el("div", { class: "grid sm:grid-cols-3 gap-3" }, [field("Utilisations max", x.max, "Une annulation libère l'utilisation."), field("Valable du", x.from), field("au", x.until)]),
      el("div", {}, [lbl("Formules concernées (aucune = toutes)"), forms]),
      el("div", {}, [lbl("Packs précis (aucun = tous ; jamais sur un pack sur mesure)"), packs]),
      field("Note", x.note),
      el("p", { class: "text-[11px] text-gray-500", text: "Une seule remise par commande ; elle porte sur le pack uniquement et ne dépasse jamais son prix." }),
    ]);
    modal(d ? "Modifier le code" : "Nouveau code de remise", body, function (close) {
      var b = el("button", { class: "action-btn btn-primary", text: "Enregistrer", onclick: function () {
        var payload = { kind: x.kind.value, value: numOrNull(x.value), max_uses: numOrNull(x.max), valid_from: dateIso(x.from.value, false),
          valid_until: dateIso(x.until.value, true), formula_ids: forms.values(), pack_ids: packs.values(), note: x.note.value.trim() || null };
        if (!d) payload.code = x.code.value.trim().toUpperCase();
        b.disabled = true;
        catCall(d ? btApi.patch("/admin/invitations/catalog/discounts/" + d.id, payload) : btApi.post("/admin/invitations/catalog/discounts", payload), "Code enregistré.")
          .then(close).catch(function () { b.disabled = false; });
      } });
      return [b];
    });
  }

  // ── Réglages
  function catSettings(box) {
    var st = CAT.data.settings, ro = !canEditCat();
    var cap = el("input", { class: "input-field", type: "number", min: "0", max: "100", style: "max-width:120px" }); cap.value = st.manual_discount_max_pct;
    var vatOn = el("input", { type: "checkbox" }); vatOn.checked = st.vat_enabled;
    var rate = el("input", { class: "input-field", type: "number", min: "0", max: "50", step: "0.01", style: "max-width:120px" }); rate.value = st.vat_rate_bp / 100;
    var mode = el("select", { class: "input-field", style: "max-width:320px" }, [el("option", { value: "ttc", text: "Les prix du catalogue sont TTC" }), el("option", { value: "ht", text: "Les prix du catalogue sont HT (TVA ajoutée)" })]);
    mode.value = st.prices_include_vat ? "ttc" : "ht";
    [cap, vatOn, rate, mode].forEach(function (i) { i.disabled = ro; });
    box.appendChild(el("div", { class: "card p-5 space-y-4" }, [
      field("Plafond de la remise manuelle (% du pack)", cap, "Remise manuelle : administrateur seul, motif obligatoire."),
      el("div", { class: "p-3 rounded-xl bg-gray-50 space-y-3" }, [
        el("label", { class: "flex items-center gap-2 text-sm font-semibold text-gray-800" }, [vatOn, "Appliquer la TVA"]),
        field("Taux de TVA (%)", rate), field("Prix saisis", mode),
        el("p", { class: "text-[11px] text-gray-500", text: "Désactivée : « TVA non applicable », total = prix. Activée : HT, TVA et TTC sont calculés au FCFA près et figés dans chaque commande." }),
      ]),
      ro ? null : el("button", { class: "action-btn btn-primary", text: "Enregistrer les réglages", onclick: function () {
        var payload = { manual_discount_max_pct: parseInt(cap.value, 10) || 0, vat_enabled: vatOn.checked, vat_rate: parseFloat(rate.value) || 0, prices_include_vat: mode.value === "ttc" };
        if (vatOn.checked && !window.confirm("Activer la TVA à " + payload.vat_rate + " % ? Les prochains devis et activations l'appliqueront.")) return;
        catCall(btApi.put("/admin/invitations/catalog/settings", payload), "Réglages enregistrés.").catch(function () {});
      } }),
    ]));
  }

  // ── Historique
  function fmtVal(v) {
    if (v === null || v === undefined || v === "") return "—";
    if (v === true) return "oui"; if (v === false) return "non";
    if (Array.isArray(v)) return v.length ? v.join(", ") : "—";
    if (typeof v === "string" && /^\d{4}-\d\d-\d\dT/.test(v)) return dayLabel(v);
    return String(v);
  }
  function catHistory(box) {
    box.innerHTML = '<div class="card p-6 empty-state">Chargement…</div>';
    btApi.get("/admin/invitations/catalog/history?limit=200").then(function (d) {
      box.innerHTML = "";
      if (!d.history.length) { box.appendChild(el("p", { class: "text-sm text-gray-400", text: "Aucune modification." })); return; }
      var list = el("div", { class: "card divide-y divide-gray-100" });
      d.history.forEach(function (h) {
        var keys = Object.keys(h.changes || {});
        list.appendChild(el("div", { class: "p-3 text-xs" }, [
          el("p", { class: "font-bold text-gray-800", text: dt(h.at) + " · " + (h.user || "—") + " · " + (ACTION_TXT[h.action] || h.action) + " · " + (ENTITY_TXT[h.entity] || h.entity) + " « " + (h.label || "") + " »" }),
          keys.length && h.action !== "seed" && h.action !== "create" ? el("ul", { class: "text-gray-600 mt-1 list-disc pl-4" }, keys.map(function (k) {
            return el("li", { text: (FIELD_TXT[k] || k) + " : " + fmtVal(h.changes[k][0]) + " → " + fmtVal(h.changes[k][1]) });
          })) : null,
        ]));
      });
      box.appendChild(list);
    }).catch(function (e) { box.innerHTML = ""; toast(e.message || "Erreur", "error"); });
  }

  // ════════════════════════════════════════════════════════════════
  // ENTRÉE (appelée par showTab('invitations'))
  // ════════════════════════════════════════════════════════════════
  window.invInit = function () {
    var start = function () { if (S.view === "event" && S.eventId) openEvent(S.eventId); else if (S.view === "catalog") openCatalog(); else loadList(); };
    if (S.meta) { start(); return; }
    sec().innerHTML = '<div class="card p-6 empty-state">Chargement…</div>';
    btApi.get("/admin/invitations/meta").then(function (m) { S.meta = m; start(); })
      .catch(function (e) { sec().textContent = e.message || "Erreur de chargement"; });
  };
  window.invLeave = stopTimer;
  // Depuis l'onglet Tarifs : à appeler AVANT showTab('invitations') (invInit ouvre alors le catalogue)
  window.invOpenCatalog = function () { S.view = "catalog"; CAT.tab = "packs"; };
})();
