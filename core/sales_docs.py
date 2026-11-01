"""
core/sales_docs.py — Documents PDF de l'équipe commerciale (lot 4.5) :
  - devis numéroté A4 (en-tête GoEvent SARLU répété sur chaque page) ;
  - fiche de bienvenue d'un commercial (accès, code, lien personnel + QR).
ReportLab, polices standard (WinAnsi) comme les pass papier : un caractère hors
cp1252 (émoji…) devient « ? ». Aucune donnée n'est envoyée à un service tiers.
"""
import io
from html import escape
from pathlib import Path

from core.config import settings

GREEN = "#0E9F6E"
INK = "#0D1411"
LOGO = Path(__file__).resolve().parent.parent / "web" / "icons" / "event.png"
COMPANY = ("GoEvent SARLU — SARL unipersonnelle au capital de 150 000 FCFA — RCCM CA/BG/2026B2593 — "
           "NIU en cours d'attribution — Avenue D. Dacko, à proximité du marché de Bimbo, Bangui — "
           "goevent.africa@gmail.com — WhatsApp +236 70 06 68 52")
CONDITIONS = ("Paiement intégral à la commande, à GoEvent SARLU (espèces contre reçu ou Orange Money). "
              "Aucun acompte. Les invitations sont activées dès réception du paiement. "
              "Annulation et report : article 6 bis.5 des CGU (goevent.africa/cgu).")


def fmt_phone(p):
    """+23675333444 → +236 75 33 34 44 (affichage)."""
    if not p or not p.startswith("+236") or len(p) != 12:
        return p
    d = p[4:]
    return "+236 %s %s %s %s" % (d[:2], d[2:4], d[4:6], d[6:])


def _t(s) -> str:
    """Texte sûr pour les Paragraph ReportLab : WinAnsi + échappement XML."""
    s = str(s if s is not None else "").encode("cp1252", "replace").decode("cp1252")
    return escape(s).replace("\n", "<br/>")


def _styles():
    from reportlab.lib.colors import HexColor
    from reportlab.lib.styles import ParagraphStyle
    ink, green = HexColor(INK), HexColor(GREEN)
    return {
        "small": ParagraphStyle("small", fontName="Helvetica", fontSize=7.5, leading=10, textColor=ink),
        "body": ParagraphStyle("body", fontName="Helvetica", fontSize=9.5, leading=13, textColor=ink),
        "bold": ParagraphStyle("bold", fontName="Helvetica-Bold", fontSize=9.5, leading=13, textColor=ink),
        "h1": ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=18, leading=22, textColor=green),
        "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=11, leading=15, textColor=green, spaceBefore=8),
        "right": ParagraphStyle("right", fontName="Helvetica", fontSize=9.5, leading=13, textColor=ink, alignment=2),
        "rightb": ParagraphStyle("rightb", fontName="Helvetica-Bold", fontSize=10.5, leading=14, textColor=ink, alignment=2),
    }


def _header(canvas, doc):
    from reportlab.lib.colors import HexColor
    from reportlab.lib.units import mm
    W, H = doc.pagesize
    canvas.saveState()
    if LOGO.exists():
        canvas.drawImage(str(LOGO), 18 * mm, H - 27 * mm, 14 * mm, 14 * mm, mask="auto")
    canvas.setFillColor(HexColor(INK))
    canvas.setFont("Helvetica-Bold", 13)
    canvas.drawString(36 * mm, H - 18 * mm, "GoEvent")
    from reportlab.platypus import Paragraph
    p = Paragraph(_t(COMPANY), _styles()["small"])
    _, h = p.wrap(W - 36 * mm - 18 * mm, 20 * mm)
    p.drawOn(canvas, 36 * mm, H - 20 * mm - h)
    canvas.setStrokeColor(HexColor(GREEN))
    canvas.setLineWidth(1.2)
    canvas.line(18 * mm, H - 31 * mm, W - 18 * mm, H - 31 * mm)
    canvas.setFont("Helvetica", 7.5)
    canvas.drawRightString(W - 18 * mm, 10 * mm, "Page %d" % doc.page)
    canvas.restoreState()


def quote_pdf(q, owner=None) -> bytes:
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from core.quotes import fcfa, fr_date

    st = _styles()
    s = q.snapshot or {}
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=38 * mm,
                            bottomMargin=18 * mm, title="Devis %s" % q.number, author="GoEvent SARLU")
    story = [
        Paragraph(_t("DEVIS N° %s" % q.number), st["h1"]),
        Paragraph(_t("Date d'émission : %s · Valable jusqu'au %s" % (fr_date(q.created_at.date()), fr_date(q.valid_until))),
                  st["body"]),
        Spacer(1, 6 * mm),
        Paragraph("Client", st["h2"]),
    ]
    client = [("Nom", q.client_name), ("Entreprise / organisation", q.client_company), ("Téléphone", fmt_phone(q.client_phone)),
              ("Événement", q.event_type), ("Date prévue", fr_date(q.event_date)), ("Lieu", q.location),
              ("Nombre d'invités", q.guests_count)]
    rows = [[Paragraph(_t(k), st["bold"]), Paragraph(_t(v), st["body"])] for k, v in client if v not in (None, "")]
    t = Table(rows, colWidths=[48 * mm, None])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    story += [t, Spacer(1, 4 * mm), Paragraph("Détail", st["h2"])]

    pack, formula = s.get("pack") or {}, s.get("formula") or {}
    lines = [[Paragraph("<b>Désignation</b>", st["body"]), Paragraph("<b>Qté</b>", st["right"]),
              Paragraph("<b>P.U.</b>", st["right"]), Paragraph("<b>Total</b>", st["right"])]]
    label = "Formule %s — pack %s personnes" % (formula.get("name", ""), pack.get("size", ""))
    if pack.get("name") and pack.get("name") != "Sur mesure":
        label += " « %s »" % pack["name"]
    if pack.get("custom"):
        label += " (sur mesure)"
    lines.append([Paragraph(_t(label), st["body"]), Paragraph("1", st["right"]),
                  Paragraph(_t(fcfa(pack.get("unit_price"))), st["right"]), Paragraph(_t(fcfa(pack.get("unit_price"))), st["right"])])
    for o in s.get("options") or []:
        unit = "" if o["unit"] == "flat" else " (%s)" % (o.get("unit_label") or ("page" if o["unit"] == "per_page" else "unité"))
        lines.append([Paragraph(_t(o["name"] + unit), st["body"]), Paragraph(str(o["qty"]), st["right"]),
                      Paragraph(_t(fcfa(o["unit_price"])), st["right"]), Paragraph(_t(fcfa(o["total"])), st["right"])])
    d = s.get("discount")
    if d:
        lab = "Remise" + (" (code %s)" % d["code"] if d.get("kind") == "code" and d.get("code") else "")
        if d.get("type") == "pct":
            lab += " — %d %% du pack" % d["value"]
        lines.append([Paragraph(_t(lab), st["body"]), "", "", Paragraph(_t("– " + fcfa(d["amount"])), st["right"])])
    if s.get("offered"):
        lines.append([Paragraph("Pack offert", st["body"]), "", "", Paragraph(_t("– " + fcfa(s.get("subtotal"))), st["right"])])
    vat = s.get("vat") or {}
    tail = []
    if vat.get("enabled"):
        tail += [("Total HT", fcfa(vat["ht"])),
                 ("TVA %s %%" % str(vat["rate_bp"] / 100).replace(".", ","), fcfa(vat["vat"])),
                 ("Total TTC", fcfa(vat["ttc"]))]
    else:
        tail.append(("Total", fcfa(s.get("amount_due"))))
    n_body = len(lines)
    for k, v in tail:
        lines.append([Paragraph(_t(k), st["rightb"] if k.startswith("Total") else st["right"]), "", "",
                      Paragraph(_t(v), st["rightb"])])
    tbl = Table(lines, colWidths=[None, 14 * mm, 32 * mm, 34 * mm], repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HexColor("#E8F6F0")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, HexColor(GREEN)),
        ("LINEBELOW", (0, 1), (-1, n_body - 1), 0.3, HexColor("#D6DDD8")),
        ("LINEABOVE", (0, n_body), (-1, n_body), 0.8, HexColor(INK)),
        ("SPAN", (0, n_body), (2, n_body)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ] + [("SPAN", (0, i), (2, i)) for i in range(n_body, len(lines))]))
    story.append(tbl)
    if s.get("agents_included"):
        story.append(Paragraph(_t("%d agent(s) GoEvent inclus le jour de l'événement." % s["agents_included"]), st["body"]))
    story += [Spacer(1, 6 * mm), Paragraph("Conditions", st["h2"]), Paragraph(_t(CONDITIONS), st["body"]),
              Spacer(1, 10 * mm)]
    sign = Table([[Paragraph(_t("Référence commerciale : %s" % q.sales_code) if q.sales_code else "", st["body"]),
                   Paragraph(_t("Bon pour accord — date et signature du client"), st["bold"])],
                  ["", ""]], colWidths=[None, 80 * mm], rowHeights=[None, 26 * mm])
    sign.setStyle(TableStyle([("BOX", (1, 0), (1, 1), 0.6, HexColor(INK)), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(sign)
    doc.build(story, onFirstPage=_header, onLaterPages=_header)
    return buf.getvalue()


def welcome_text(user, pin: str) -> str:
    base = settings.PUBLIC_SITE_URL.rstrip("/")
    lines = ["Bienvenue dans l'équipe GoEvent, %s !" % (user.full_name or ""), "",
             "Connexion à l'espace équipe : %s/admin-login.html" % base,
             "Numéro de connexion : %s" % user.phone_number,
             "Code PIN provisoire : %s (à changer dès la première connexion)" % pin]
    if user.sales_code:
        lines += ["", "Votre code commercial : %s" % user.sales_code,
                  "Votre lien personnel : %s/invitations?ref=%s" % (base, user.sales_code)]
    if user.commission_rate:
        lines.append("Commission : %s %% du prix du pack (après remise), versée au plus tard le 5 du mois suivant "
                     "l'événement." % str(round(user.commission_rate * 100, 2)).replace(".", ",").rstrip("0").rstrip(","))
    lines += ["", "Règles :",
              "1. Le client paie uniquement GoEvent SARLU (reçu GoEvent ou Orange Money GoEvent), jamais vous.",
              "2. Tout devis passe par la plateforme (numéro DEV-…).",
              "3. Les données des clients et des invités sont confidentielles."]
    return "\n".join(lines)


def welcome_pdf(user, pin: str) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from core.email import qr_png

    st = _styles()
    base = settings.PUBLIC_SITE_URL.rstrip("/")
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=38 * mm,
                            bottomMargin=18 * mm, title="Fiche de bienvenue", author="GoEvent SARLU")
    story = [Paragraph(_t("Bienvenue, %s" % (user.full_name or "")), st["h1"]),
             Paragraph(_t("Votre accès à l'espace équipe GoEvent."), st["body"]), Spacer(1, 5 * mm),
             Paragraph("Connexion", st["h2"])]
    acc = [[Paragraph("Adresse", st["bold"]), Paragraph(_t("%s/admin-login.html" % base), st["body"])],
           [Paragraph("Numéro", st["bold"]), Paragraph(_t(fmt_phone(user.phone_number)), st["body"])],
           [Paragraph("Code PIN provisoire", st["bold"]), Paragraph(_t(" ".join(pin)), st["h1"])],
           ["", Paragraph(_t("À remplacer par votre propre code dès la première connexion."), st["body"])]]
    t = Table(acc, colWidths=[48 * mm, None])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(t)
    if user.sales_code:
        link = "%s/invitations?ref=%s" % (base, user.sales_code)
        story += [Paragraph("Votre code commercial", st["h2"])]
        qr = Image(io.BytesIO(qr_png(link, box_size=8)), 38 * mm, 38 * mm)
        info = [Paragraph(_t(user.sales_code), st["h1"]), Paragraph(_t("Lien personnel : %s" % link), st["body"]),
                Paragraph(_t("Les prospects qui demandent un devis depuis ce lien vous sont attribués. "
                             "Le QR code peut être imprimé sur vos cartes et flyers."), st["body"])]
        tq = Table([[info, qr]], colWidths=[None, 42 * mm])
        tq.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.append(tq)
    if user.commission_rate:
        story += [Paragraph("Commission", st["h2"]), Paragraph(_t(
            "%s %% du prix du pack après remise (options exclues), pour chaque pack payé dont l'événement a eu lieu "
            "dans le mois ; versée au plus tard le 5 du mois suivant." %
            str(round(user.commission_rate * 100, 2)).replace(".", ",").rstrip("0").rstrip(",")), st["body"])]
    story += [Paragraph("Règles", st["h2"])]
    for r in ("Le client paie uniquement GoEvent SARLU (espèces contre reçu GoEvent ou Orange Money GoEvent), jamais vous.",
              "Tout devis passe par la plateforme : numéro DEV-…, prix du catalogue, validité 15 jours.",
              "Les données des clients et des listes d'invités sont confidentielles : ni copie, ni transmission."):
        story.append(Paragraph(_t("• " + r), st["body"]))
    doc.build(story, onFirstPage=_header, onLaterPages=_header)
    return buf.getvalue()
