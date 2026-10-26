/* ════════════════════════════════════════════════════════════════
 * admin-invitations.js — Onglet « Invitations » du back-office (admin.html)
 *
 * Offre accompagnée : l'équipe GoEvent crée l'événement privé, importe la
 * liste, envoie les pass par WhatsApp (lien wa.me, un clic par invité),
 * suit les arrivées et remet au client un lien de suivi (chiffres seuls).
 * Accès : capability invitations_manage (admin + commercial). Activation d'un
 * pack : admin uniquement (invitations_activate), après paiement reçu sur un
 * moyen AU NOM DE GoEvent SARLU, référence obligatoire (garde serveur).
 * Dépend de bt-api.js (btApi, btToast, _btEscapeHtml) et de hasCapability().
 * ════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";

  var S = { meta: null, view: "list", eventId: null, ev: null, guests: null, q: "", status: "", page: 1,
            queue: [], timer: null, importRows: null };
  var SERVICE = { autonome: "Autonome", accompagne: "Accompagné" };
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
        el("button", { class: "action-btn btn-primary", text: "+ Nouvel événement privé", onclick: eventModal }),
      ]));
      if (!d.events.length) {
        root.appendChild(el("div", { class: "card p-6 empty-state", text: "Aucun événement privé pour l'instant." }));
        return;
      }
      var grid = el("div", { class: "grid gap-3 md:grid-cols-2" });
      d.events.forEach(function (e) {
        var s = e.stats;
        var packTxt = e.quota ? e.quota + " pers. · " + SERVICE[e.active_service] : "Aucun pack payé";
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
      service: el("select", { class: "input-field" }, [el("option", { value: "accompagne", text: "Accompagné (agent GoEvent sur place + lien de suivi)" }),
                                                        el("option", { value: "autonome", text: "Autonome (le client scanne lui-même)" })]),
      desc: el("textarea", { class: "input-field", rows: "2", maxlength: "2000", placeholder: "Note interne (facultatif)" }),
    };
    if (ev) {
      f.title.value = ev.title; f.date.value = toLocalInput(ev.event_date); f.location.value = ev.location || "";
      f.client.value = ev.client_name; f.phone.value = ev.client_phone || ""; f.host.value = ev.host_label || "";
      f.service.value = ev.service_level; f.desc.value = ev.description || "";
    }
    var body = el("div", { class: "space-y-3" }, [
      field("Titre", f.title), el("div", { class: "grid sm:grid-cols-2 gap-3" }, [field("Date et heure", f.date), field("Lieu", f.location)]),
      el("div", { class: "grid sm:grid-cols-2 gap-3" }, [field("Client", f.client), field("Téléphone du client", f.phone)]),
      field("Hôte affiché sur le pass", f.host, "Vide = nom du client."), field("Niveau de service", f.service), field("Description", f.desc),
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
          (ev.client_phone ? " (" + ev.client_phone + ")" : "") + " · " + SERVICE[ev.service_level] }),
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

  // ── Pack & paiement ───────────────────────────────────────────────
  function packBlock() {
    var ev = S.ev, items = [];
    if (!ev.packs.length) items.push(el("p", { class: "text-sm text-amber-700 font-semibold mb-3", text: "Aucun pack payé : vous pouvez préparer la liste, mais l'envoi des pass, le scan et le lien de suivi restent bloqués." }));
    ev.packs.forEach(function (p) {
      var ch = (S.meta.channels.filter(function (c) { return c.code === p.payment_channel; })[0] || {}).label || p.payment_channel;
      items.push(el("div", { class: "border border-gray-100 rounded-xl p-3 mb-2 flex flex-wrap items-center gap-3 " + (p.status === "actif" ? "" : "opacity-60") }, [
        el("div", { class: "flex-1 min-w-[200px]" }, [
          el("p", { class: "font-bold text-sm", text: "Pack " + p.size + " personnes · " + SERVICE[p.service] + " · " + fcfa(p.amount_fcfa) + (p.status === "actif" ? "" : " · ANNULÉ") }),
          el("p", { class: "text-[11px] text-gray-500", text: ch + " · réf. " + p.payment_ref + " · reçu " + fcfa(p.amount_received) + " · " + dt(p.activated_at) +
            (p.cancel_reason ? " · annulé : " + p.cancel_reason : "") }),
        ]),
        p.status === "actif" && canActivate() ? el("button", { class: "action-btn btn-danger text-xs", text: "Annuler le pack", onclick: function () {
          var reason = window.prompt("Motif de l'annulation (obligatoire) :");
          if (!reason || reason.trim().length < 3) return;
          btApi.post("/admin/invitations/packs/" + p.id + "/cancel", { reason: reason.trim() }).then(function () { toast("Pack annulé.", "success"); reloadEvent(); })
            .catch(function (e) { toast(e.message || "Erreur", "error"); });
        } }) : null,
      ]));
    });
    if (canActivate()) items.push(el("button", { class: "action-btn btn-primary text-xs", text: "Activer un pack (paiement reçu)", onclick: packModal }));
    else items.push(el("p", { class: "text-[11px] text-gray-500", text: "L'activation d'un pack est réservée à l'administrateur, après réception du paiement." }));
    return block("Pack & paiement", items);
  }

  function packModal() {
    var m = S.meta, active = m.channels.filter(function (c) { return c.active; });
    var size = el("select", { class: "input-field" }), service = el("select", { class: "input-field" }), channel = el("select", { class: "input-field" });
    m.pack_sizes.forEach(function (n) { size.appendChild(el("option", { value: String(n), text: n + " personnes" })); });
    m.services.forEach(function (s) { service.appendChild(el("option", { value: s, text: SERVICE[s], selected: s === S.ev.service_level ? "selected" : null })); });
    active.forEach(function (c) { channel.appendChild(el("option", { value: c.code, text: c.label })); });
    var ref = el("input", { class: "input-field", maxlength: "80" }), refLbl = lbl(""), amount = el("input", { class: "input-field", type: "number", min: "1" });
    var note = el("input", { class: "input-field", maxlength: "300", placeholder: "Facultatif" });
    var confirm = el("input", { type: "checkbox" }), price = el("p", { class: "text-sm font-black text-gray-900" });
    var listed = S.ev.listed;
    var minNeeded = Math.max(0, listed - S.ev.quota);
    function upd() {
      var p = m.prices[service.value + "_" + size.value];
      price.textContent = "Prix du pack : " + fcfa(p);
      amount.value = p;
      var c = active.filter(function (x) { return x.code === channel.value; })[0];
      refLbl.textContent = c ? c.ref_label + " (obligatoire)" : "Référence";
    }
    [size, service, channel].forEach(function (x) { x.addEventListener("change", upd); });
    upd();
    var body = el("div", { class: "space-y-3" }, [
      el("div", { class: "p-3 rounded-xl bg-amber-50 text-xs text-amber-900", text: "N'activez un pack qu'après avoir reçu le paiement sur un moyen AU NOM DE GoEvent SARLU. La référence est enregistrée et ne pourra plus servir à activer un autre pack." }),
      el("div", { class: "grid sm:grid-cols-2 gap-3" }, [field("Taille", size, minNeeded ? "Au moins " + minNeeded + " personnes de plus (liste actuelle : " + listed + ")." : null), field("Service", service)]),
      price,
      field("Moyen de paiement", channel, m.channels.some(function (c) { return !c.active; }) ? "Désactivés : " + m.channels.filter(function (c) { return !c.active; }).map(function (c) { return c.label; }).join(", ") : null),
      el("div", {}, [refLbl, ref]),
      field("Montant reçu (FCFA)", amount),
      field("Note", note),
      el("label", { class: "flex items-start gap-2 text-sm font-semibold text-gray-800" }, [confirm, "Je confirme que ce paiement a été reçu sur un moyen de paiement au nom de GoEvent SARLU."]),
    ]);
    modal("Activer un pack", body, function (close) {
      var b = el("button", { class: "action-btn btn-primary", text: "Activer", onclick: function () {
        if (!confirm.checked) { toast("Cochez la confirmation de réception du paiement.", "error"); return; }
        if (ref.value.trim().length < 3) { toast("Référence du paiement obligatoire.", "error"); return; }
        b.disabled = true;
        btApi.post("/admin/invitations/events/" + S.eventId + "/packs", { size: parseInt(size.value, 10), service: service.value,
          payment_channel: channel.value, payment_ref: ref.value.trim(), amount_received: parseInt(amount.value, 10) || 0,
          confirmed_goevent_account: true, note: note.value.trim() || null })
          .then(function () { close(); toast("Pack activé.", "success"); reloadEvent(); })
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
    if (ev.active_service === "accompagne") {
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
      track.appendChild(el("p", { class: "text-[11px] text-gray-500", text: "Lien de suivi client : inclus dans l'offre Accompagné (aucun pack Accompagné actif)." }));
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
    return block("Agents de contrôle", [
      el("p", { class: "text-[11px] text-gray-500 mb-2", text: ev.service_level === "accompagne" ? "Agents GoEvent sur place. Ils se connectent sur goevent.africa avec ce numéro et ce PIN, puis scannent avec « Scanner »." :
        "Offre Autonome : créez les comptes des personnes du client qui scanneront à l'entrée (numéro + PIN à leur transmettre)." }),
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
  // ENTRÉE (appelée par showTab('invitations'))
  // ════════════════════════════════════════════════════════════════
  window.invInit = function () {
    var start = function () { if (S.view === "event" && S.eventId) openEvent(S.eventId); else loadList(); };
    if (S.meta) { start(); return; }
    sec().innerHTML = '<div class="card p-6 empty-state">Chargement…</div>';
    btApi.get("/admin/invitations/meta").then(function (m) { S.meta = m; start(); })
      .catch(function (e) { sec().textContent = e.message || "Erreur de chargement"; });
  };
  window.invLeave = stopTimer;
})();
