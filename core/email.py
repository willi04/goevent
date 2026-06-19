"""
core/email.py
Envoi d'emails transactionnels via SMTP Gmail :
- Billet + QR code après paiement
- Code de réinitialisation du PIN
"""
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from core.config import settings


def envoyer_billet_email(email_client, nom_client, ticket, event, amount):
    if not settings.SENDER_EMAIL or not settings.SENDER_PASSWORD:
        print("⚠️ Email credentials missing, skipping email")
        return

    msg = MIMEMultipart()
    msg["From"] = f"GoEvent <{settings.SENDER_EMAIL}>"
    msg["To"] = email_client
    msg["Subject"] = f"🎟️ Confirmation & Billet : {event.title}"

    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=250x250&data={ticket.qr_hash}"
    label_prix = "🎟️ ENTRÉE GRATUITE" if amount <= 0 else f"💰 Montant : {amount} FCFA"

    html_body = f"""
    <html>
    <body style="font-family: 'Arial', sans-serif; background-color: #f4f4f4; padding: 20px; text-align: center;">
        <div style="background-color: #ffffff; max-width: 450px; margin: 0 auto; padding: 30px; border-radius: 20px; border: 1px solid #e2e2e2; box-shadow: 0 10px 25px rgba(0,0,0,0.05);">
            <h2 style="color: #00502c; font-size: 24px; margin-bottom: 5px;">GoEvent</h2>
            <p style="color: #888; font-size: 12px; text-transform: uppercase; letter-spacing: 2px;">Reçu de paiement</p>

            <h3 style="color: #1a1c1c; font-size: 20px; margin-top: 20px;">{event.title}</h3>
            <p style="color: #666; margin: 5px 0;">📍 {event.location}</p>
            <p style="color: #00502c; font-weight: bold; font-size: 18px; margin: 15px 0;">{label_prix}</p>

            <hr style="border: none; border-top: 2px dashed #e2e2e2; margin: 25px 0;">

            <p style="color: #00502c; font-weight: bold; margin-bottom: 15px;">Voici votre QR Code d'accès :</p>
            <img src="{qr_url}" alt="QR Code Billet" style="width: 200px; height: 200px; border-radius: 10px; border: 4px solid #f9f9f9;">
            <p style="color: #aaa; font-size: 10px; margin-top: 10px; letter-spacing: 1px;">ID: {ticket.qr_hash}</p>

            <div style="background-color: #fff4f4; border-left: 4px solid #ba1a1a; padding: 10px; margin-top: 25px; text-align: left;">
                <p style="color: #ba1a1a; font-size: 12px; margin: 0; font-weight: bold;">⚠️ IMPORTANT POUR LE JOUR-J</p>
                <p style="color: #ba1a1a; font-size: 11px; margin: 5px 0 0 0;">Faites une capture d'écran de ce QR Code maintenant au cas où vous n'auriez pas de connexion internet à l'entrée.</p>
            </div>
        </div>
    </body>
    </html>
    """

    msg.attach(MIMEText(html_body, "html"))

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(settings.SENDER_EMAIL, settings.SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"📧 Email envoyé avec succès à {email_client}")
    except Exception as e:
        print(f"❌ Erreur lors de l'envoi de l'email : {e}")


def envoyer_mail_code(email_client, code_secret):
    if not settings.SENDER_EMAIL or not settings.SENDER_PASSWORD:
        print("⚠️ Email credentials missing")
        return

    msg = MIMEMultipart()
    msg["From"] = f"GoEvent <{settings.SENDER_EMAIL}>"
    msg["To"] = email_client
    msg["Subject"] = "🔒 Votre code de sécurité GoEvent"

    html_body = f"""
    <html>
    <body style="font-family: 'Arial', sans-serif; background-color: #f4f4f4; padding: 20px; text-align: center;">
        <div style="background-color: #ffffff; max-width: 400px; margin: 0 auto; padding: 30px; border-radius: 20px; box-shadow: 0 5px 15px rgba(0,0,0,0.05);">
            <h2 style="color: #00502c; font-size: 24px; margin-bottom: 5px;">GoEvent</h2>
            <p style="color: #888; font-size: 10px; text-transform: uppercase; letter-spacing: 2px;">Sécurité du compte</p>

            <p style="color: #333; margin-top: 30px;">Bonjour,</p>
            <p style="color: #666; font-size: 14px; line-height: 1.5;">Vous avez demandé à réinitialiser votre code PIN. Voici votre code de vérification à saisir dans l'application :</p>

            <div style="background-color: #f0fdf4; border: 1px solid #bbf7d0; padding: 20px; margin: 25px auto; border-radius: 12px;">
                <h1 style="color: #00502c; font-size: 32px; letter-spacing: 8px; margin: 0; font-family: monospace;">{code_secret}</h1>
            </div>

            <p style="color: #888; font-size: 11px; line-height: 1.5;">Ce code est valide pendant <b>15 minutes</b>.<br>Si vous n'avez pas fait cette demande, veuillez ignorer cet email.</p>
        </div>
    </body>
    </html>
    """

    msg.attach(MIMEText(html_body, "html"))

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(settings.SENDER_EMAIL, settings.SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"🔒 Code de sécurité envoyé à {email_client}")
    except Exception as e:
        print(f"❌ Erreur envoi code de sécurité : {e}")

def envoyer_mail_annulation(email_client, nom_client, event_title, amount, is_refund):
    """
    Email de notification d'annulation.
    is_refund=True  → message avec annonce de remboursement sous 7 jours
    is_refund=False → message simple (cas réservation cash non payée)
    """
    if not settings.SENDER_EMAIL or not settings.SENDER_PASSWORD:
        print("⚠️ Email credentials missing, skipping email")
        return

    msg = MIMEMultipart()
    msg["From"] = f"GoEvent <{settings.SENDER_EMAIL}>"
    msg["To"] = email_client
    msg["Subject"] = f"❌ Événement annulé : {event_title}"

    if is_refund:
        refund_block = f"""
            <div style="background-color: #f0fdf4; border-left: 4px solid #00502c; padding: 15px; margin: 25px 0; text-align: left;">
                <p style="color: #00502c; font-size: 13px; margin: 0; font-weight: bold;">💰 REMBOURSEMENT</p>
                <p style="color: #1a1c1c; font-size: 14px; margin: 8px 0 0 0;">
                    Le montant de <strong>{int(amount)} FCFA</strong> vous sera reversé sur votre compte Orange Money
                    dans un délai de <strong>7 jours ouvrés</strong>.
                </p>
                <p style="color: #6b7280; font-size: 11px; margin: 8px 0 0 0;">
                    Note : les frais de service (4%) ne sont pas remboursés conformément à nos CGU.
                </p>
            </div>
        """
    else:
        refund_block = """
            <div style="background-color: #fff7ed; border-left: 4px solid #f97316; padding: 15px; margin: 25px 0; text-align: left;">
                <p style="color: #c2410c; font-size: 13px; margin: 0; font-weight: bold;">ℹ️ INFORMATION</p>
                <p style="color: #1a1c1c; font-size: 14px; margin: 8px 0 0 0;">
                    Votre réservation cash est annulée. Aucun paiement n'avait été effectué, aucun remboursement n'est nécessaire.
                </p>
            </div>
        """

    html_body = f"""
    <html>
    <body style="font-family: 'Arial', sans-serif; background-color: #f4f4f4; padding: 20px; text-align: center;">
        <div style="background-color: #ffffff; max-width: 500px; margin: 0 auto; padding: 30px; border-radius: 20px; border: 1px solid #e2e2e2; box-shadow: 0 10px 25px rgba(0,0,0,0.05);">
            <h2 style="color: #00502c; font-size: 24px; margin-bottom: 5px;">GoEvent</h2>
            <p style="color: #888; font-size: 12px; text-transform: uppercase; letter-spacing: 2px;">Avis d'annulation</p>

            <h3 style="color: #1a1c1c; font-size: 20px; margin-top: 25px;">Bonjour {nom_client or "cher utilisateur"},</h3>

            <p style="color: #4b5563; font-size: 14px; line-height: 1.6; margin-top: 15px;">
                Nous avons le regret de vous informer que l'événement suivant, pour lequel vous aviez réservé une place, a été annulé par l'organisateur :
            </p>

            <div style="background-color: #f9fafb; padding: 15px; border-radius: 10px; margin: 20px 0;">
                <p style="color: #1a1c1c; font-size: 16px; font-weight: bold; margin: 0;">{event_title}</p>
            </div>

            {refund_block}

            <p style="color: #6b7280; font-size: 12px; line-height: 1.5; margin-top: 25px;">
                Pour toute question, contactez notre support à <a href="mailto:support@goevent.africa" style="color: #00502c;">support@goevent.africa</a>.
            </p>

            <p style="color: #aaa; font-size: 11px; margin-top: 20px;">
                — L'équipe GoEvent
            </p>
        </div>
    </body>
    </html>
    """

    msg.attach(MIMEText(html_body, "html"))

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(settings.SENDER_EMAIL, settings.SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"📧 Email annulation envoyé à {email_client}")
    except Exception as e:
        print(f"❌ Erreur envoi email annulation : {e}")
