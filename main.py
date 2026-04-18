"""
GoEvent — Backend API v2
Rôles: fan, artiste, agent, organisation
Nouvelles features: recherche, pagination, email, reset PIN, stats
"""

from fastapi import FastAPI, HTTPException, Depends, Query, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Boolean, Float, DateTime, ForeignKey, Text, or_,func
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship
from sqlalchemy.dialects.postgresql import JSONB
from passlib.context import CryptContext
from jose import jwt, JWTError
from datetime import datetime, timedelta
from typing import Optional, List
import requests
import random
import uuid
import os
import pathlib
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv


#CHARGEMENT DE BASE DE DONNEES
load_dotenv()

# ── CONFIG ─────────────────────────────────────────────────────
# Remplace les infos par tes vrais identifiants PostgreSQL (ou utilise une variable d'environnement)
DATABASE_URL = os.getenv("DATABASE_URL")
SECRET_KEY   = "go_event_secret_key_2025_centrafrique"
ALGORITHM    = "HS256"

if not DATABASE_URL:
    raise ValueError("⚠️ ERREUR CRITIQUE : Aucune URL de base de données (DATABASE_URL) n'a été trouvée !")
# 💰 CinetPay
CINETPAY_API_KEY = os.getenv("CINETPAY_API_KEY", "TON_API_KEY")
CINETPAY_SITE_ID = os.getenv("CINETPAY_SITE_ID", "TON_SITE_ID")
CINETPAY_URL = "https://api-checkout.cinetpay.com/v2/payment"
CINETPAY_CHECK_URL = "https://api-checkout.cinetpay.com/v2/payment/check"

# Configuration PostgreSQL (sans check_same_thread)
engine       = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base         = declarative_base()
pwd_context  = CryptContext(schemes=["bcrypt"], deprecated="auto")
security     = HTTPBearer(auto_error=False)

ROLES_VALIDES = ("fan", "organizer", "agent")
# ── MODÈLES ────────────────────────────────────────────────────
class User(Base):
    __tablename__ = "users"
    id           = Column(Integer, primary_key=True, index=True)
    phone_number = Column(String, unique=True, index=True)
    full_name    = Column(String)
    email        = Column(String, nullable=True)
    pin_hash     = Column(String)
    profile_picture = Column(Text, nullable=True)  # Text car une image en Base64 est un long texte
    role         = Column(String, default="fan")
    org_name     = Column(String, nullable=True)   # nom organisation/entreprise
    org_type     = Column(String, nullable=True)   # ONG, Entreprise, Institution...
    is_active    = Column(Boolean, default=True)
    agent_event_id = Column(Integer, ForeignKey("events.id"), nullable=True)
    created_at   = Column(DateTime, default=datetime.utcnow)
    events = relationship("Event", back_populates="organizer_user", foreign_keys="[Event.organizer_id]")
    tickets      = relationship("Ticket", back_populates="owner")
    favorites    = relationship("Favorite", back_populates="user")
    orange_money = Column(String(20), nullable=True)


class Event(Base):
    __tablename__ = "events"
    id              = Column(Integer, primary_key=True, index=True)
    title           = Column(String)
    description     = Column(Text, default="")
    location        = Column(String)
    category        = Column(String, default="Concert")
    event_date      = Column(DateTime)
    price           = Column(Float)
    total_seats     = Column(Integer)
    seats_sold      = Column(Integer, default=0)
    cover_image_url = Column(String, default="")
    ticket_tiers = Column(JSONB, default=list)
    is_active       = Column(Boolean, default=True)
    created_at      = Column(DateTime, default=datetime.utcnow)
    organizer_id    = Column(Integer, ForeignKey("users.id"))
    organizer_user = relationship("User", back_populates="events", foreign_keys=[organizer_id])
    tickets         = relationship("Ticket", back_populates="event")

class Ticket(Base):
    __tablename__ = "tickets"
    id             = Column(Integer, primary_key=True, index=True)
    qr_hash        = Column(String, unique=True, index=True, default=lambda: str(uuid.uuid4()))
    payment_status = Column(String, default="attente")
    payment_ref    = Column(String, default="")
    is_used        = Column(Boolean, default=False)
    used_at        = Column(DateTime, nullable=True)
    purchased_at   = Column(DateTime, default=datetime.utcnow)
    user_id        = Column(Integer, ForeignKey("users.id"))
    event_id       = Column(Integer, ForeignKey("events.id"))
    owner          = relationship("User", back_populates="tickets")
    event          = relationship("Event", back_populates="tickets")


class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer)
    ticket_id = Column(Integer)

    # --- LA COMPTABILITÉ ---
    amount = Column(Float)  # Total payé via CinetPay (Prix + 4%)
    base_price = Column(Float, default=0)  # Le prix initial affiché du billet
    platform_fee = Column(Float, default=0)  # TES BÉNÉFICES (4% acheteur + 7% orga)
    organizer_amount = Column(Float, default=0)  # Ce que tu dois virer à l'organisateur

    status = Column(String, default="pending")
    transaction_id = Column(String)

class PayoutRequest(Base):
    __tablename__ = "payout_requests"
    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id"))
    organizer_id = Column(Integer, ForeignKey("users.id"))
    amount = Column(Float)
    status = Column(String, default="en_attente") # en_attente, paye, rejete
    created_at = Column(DateTime, default=datetime.utcnow)

class Favorite(Base):
    __tablename__ = "favorites"
    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, ForeignKey("users.id"))
    event_id   = Column(Integer, ForeignKey("events.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    user       = relationship("User", back_populates="favorites")
    event      = relationship("Event")

class Follower(Base):
    __tablename__ = "followers"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))      # Le fan qui s'abonne
    organizer_id = Column(Integer, ForeignKey("users.id")) # L'organisateur suivi
    created_at = Column(DateTime, default=datetime.utcnow)


class CancellationRequest(Base):
    __tablename__ = "cancellation_requests"
    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id"))
    organizer_id = Column(Integer, ForeignKey("users.id"))
    reason = Column(Text)
    status = Column(String, default="en_attente")  # en_attente, approuve, rejete
    created_at = Column(DateTime, default=datetime.utcnow)

    event = relationship("Event")
    organizer = relationship("User")

# ── SÉCURITÉ ───────────────────────────────────────────────────
def hash_pin(pin): return pwd_context.hash(pin)
def verify_pin(plain, hashed): return pwd_context.verify(plain, hashed)

def create_token(user_id, role):
    return jwt.encode(
        {"sub": str(user_id), "role": role,
         "exp": datetime.utcnow() + timedelta(days=30)},
        SECRET_KEY, algorithm=ALGORITHM
    )

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
):
    if not credentials:
        raise HTTPException(status_code=401, detail="Token manquant")
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        user = db.query(User).filter(User.id == int(payload["sub"])).first()
        if not user:
            raise HTTPException(status_code=401, detail="Utilisateur introuvable")
        return user
    except JWTError:
        raise HTTPException(status_code=401, detail="Token invalide")

def get_optional_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
):
    if not credentials:
        return None
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        return db.query(User).filter(User.id == int(payload["sub"])).first()
    except:
        return None


def envoyer_billet_email(email_client, nom_client, ticket, event, amount):
    # Tes identifiants (À configurer avec un mot de passe d'application Gmail)
    SENDER_EMAIL = "emmanuel.madoukou30@gmail.com"
    SENDER_PASSWORD = "zfsbvemkoyryrvhw"

    msg = MIMEMultipart()
    msg['From'] = f"GoEvent <{SENDER_EMAIL}>"
    msg['To'] = email_client
    msg['Subject'] = f"🎟️ Confirmation & Billet : {event.title}"

    # L'URL du QR Code générée automatiquement
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=250x250&data={ticket.qr_hash}"

    html_body = f"""
    <html>
    <body style="font-family: 'Arial', sans-serif; background-color: #f4f4f4; padding: 20px; text-align: center;">
        <div style="background-color: #ffffff; max-width: 450px; margin: 0 auto; padding: 30px; border-radius: 20px; border: 1px solid #e2e2e2; box-shadow: 0 10px 25px rgba(0,0,0,0.05);">
            <h2 style="color: #00502c; font-size: 24px; margin-bottom: 5px;">GoEvent</h2>
            <p style="color: #888; font-size: 12px; text-transform: uppercase; letter-spacing: 2px;">Reçu de paiement</p>

            <h3 style="color: #1a1c1c; font-size: 20px; margin-top: 20px;">{event.title}</h3>
            <p style="color: #666; margin: 5px 0;">📍 {event.location}</p>
            <p style="color: #666; margin: 5px 0;">💰 Payé : {amount} FCFA</p>

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

    msg.attach(MIMEText(html_body, 'html'))

    try:
        # Connexion au serveur Gmail
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"📧 Email envoyé avec succès à {email_client}")
    except Exception as e:
        print(f"❌ Erreur lors de l'envoi de l'email : {e}")


def envoyer_mail_code(email_client, code_secret):
    # Tes identifiants Gmail (les mêmes que pour les billets)
    SENDER_EMAIL = "ton_email_go_event@gmail.com"
    SENDER_PASSWORD = "ton_mot_de_passe_application_secret"

    msg = MIMEMultipart()
    msg['From'] = f"GoEvent <{SENDER_EMAIL}>"
    msg['To'] = email_client
    msg['Subject'] = "🔒 Votre code de sécurité GoEvent"

    # Le code HTML du design (les couleurs s'affichent correctement dans Gmail)
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

    msg.attach(MIMEText(html_body, 'html'))

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"🔒 Code de sécurité envoyé à {email_client}")
    except Exception as e:
        print(f"❌ Erreur envoi code de sécurité : {e}")
# ── SCHÉMAS ────────────────────────────────────────────────────

class UserLogin(BaseModel):
    phone_number: str
    pin_code: str

class UserRegister(BaseModel):
    phone_number: str
    full_name: str
    pin_code: str
    role: str = "fan"
    email: Optional[str] = None   # ✅ AJOUT
    org_name: Optional[str] = None   # ✅ AJOUT
    org_type: Optional[str] = None   # ✅ AJOUT

class AgentCreate(BaseModel):
    full_name: str
    phone_number: str
    pin_code:str
    event_id: int

class PinChange(BaseModel):
    old_pin: str
    new_pin: str

class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    org_name: Optional[str] = None
    org_type: Optional[str] = None
    profile_picture: Optional[str] = None  # <-- AJOUT
    orange_money: Optional[str] = None

class TicketTier(BaseModel):
    name: str
    price: float
    seats: int
class EventCreate(BaseModel):
    title: str
    description: str = ""
    location: str
    category: str = "Concert"
    event_date: datetime
    price: float
    total_seats: int
    cover_image_url: str = ""
    ticket_tiers: Optional[List[TicketTier]] = []

class EventUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    location: Optional[str] = None
    category: Optional[str] = None
    event_date: Optional[datetime] = None
    price: Optional[float] = None
    total_seats: Optional[int] = None
    cover_image_url: Optional[str] = None
    is_active: Optional[bool] = None

class BuyTicket(BaseModel):
    event_id: int

class ScanTicket(BaseModel):
    qr_data: str

class PartnerRequestCreate(BaseModel):
    company_name: str
    email: str
    phone: str
    partnership_type: str
    message: str

# 2. Le modèle de la base de données SQLAlchemy (à ajouter avec tes autres tables)
class PartnerRequest(Base):
    __tablename__ = "partner_requests"
    id = Column(Integer, primary_key=True, index=True)
    company_name = Column(String)
    email = Column(String)
    phone = Column(String)
    partnership_type = Column(String)
    message = Column(Text)
    status = Column(String, default="nouveau") # nouveau, traite
    created_at = Column(DateTime, default=datetime.utcnow)

class PasswordReset(Base):
    __tablename__ = "password_resets"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String)
    code = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

# Assure-toi que Base.metadata.create_all(bind=engine) est bien en dessous pour créer la table !
Base.metadata.create_all(bind=engine)

def _event_dict(e, include_organizer=True):
    org = e.organizer_user
    return {
        "id": e.id, "title": e.title, "description": e.description,
        "location": e.location, "category": e.category,
        "event_date": e.event_date, "price": e.price,
        "total_seats": e.total_seats,
        "seats_available": e.total_seats - e.seats_sold,
        "seats_sold": e.seats_sold,
        "is_sold_out": e.seats_sold >= e.total_seats,
        "cover_image_url": e.cover_image_url,
        "is_active": e.is_active,
        "organizer": org.org_name or org.full_name if org else "Organisateur",
        "organizer_role": org.role if org else "artiste",
        "organizer_id": e.organizer_id,
        "ticket_tiers": e.ticket_tiers,
    }
class CancelRequestCreate(BaseModel):
    reason: str
# ── APP ─────────────────────────────────────────────────────────
app = FastAPI(title="GoEvent API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://goevent-core.vercel.app",
        "https://goevent.africa",
        "http://localhost:5500",
        "http://localhost:8000",
        "http://127.0.0.1",
        "null",],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Servir les fichiers web statiques si le dossier existe
web_dir = pathlib.Path("web")
if web_dir.exists():
    app.mount("/web", StaticFiles(directory="web"), name="web")

# ── ROOT ────────────────────────────────────────────────────────
@app.get("/")
def root():
    return {
        "app": "GoEvent API",
        "version": "2.0.0",
        "status": "En ligne",
        "roles": list(ROLES_VALIDES),
        "docs": "/docs"
    }


@app.post("/payment/init/{event_id}")
def init_payment(event_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    event = db.query(Event).get(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event introuvable")
    if event.seats_sold >= event.total_seats:
        raise HTTPException(status_code=400, detail="Événement complet")

    transaction_id = str(uuid.uuid4())

    # 🎟️ Créer ticket en attente
    ticket = Ticket(user_id=user.id, event_id=event.id, payment_status="attente")
    db.add(ticket)
    db.flush()

    # 🧮 MATHÉMATIQUES HYBRIDES (Le Modèle "GoEvent")
    prix_base = event.price
    frais_acheteur = prix_base * 0.04
    total_a_payer = prix_base + frais_acheteur

    frais_organisateur = prix_base * 0.07
    solde_orga = prix_base - frais_organisateur

    tes_benefices = frais_acheteur + frais_organisateur

    # 💰 Créer paiement avec la trace comptable exacte
    payment = Payment(
        user_id=user.id,
        ticket_id=ticket.id,
        amount=total_a_payer,
        base_price=prix_base,
        platform_fee=tes_benefices,
        organizer_amount=solde_orga,
        status="pending",
        transaction_id=transaction_id
    )
    db.add(payment)
    db.commit()

    # 🚀 Envoi de la requête à CinetPay
    payload = {
        "apikey": CINETPAY_API_KEY,
        "site_id": CINETPAY_SITE_ID,
        "transaction_id": transaction_id,
        "amount": int(total_a_payer),  # CinetPay encaisse le TOTAL (Prix + 4%)
        "currency": "XAF",
        "description": f"Billet pour {event.title}",
        "customer_name": user.full_name,
        "customer_phone_number": user.phone_number,
        "notify_url": "https://ton-domaine.com/payment/webhook",
        "return_url": "https://ton-domaine.com/user_dashboard.html",
        "channels": "MOBILE_MONEY"
    }
    url_cinetpay = "https://api-checkout.cinetpay.com/v2/payment"
    response = requests.post(url_cinetpay, json=payload)

    if response.status_code == 200:
        result = response.json()
        if result.get("code") == "201":
            # CinetPay a accepté, on renvoie le lien de paiement à ton checkout.html
            return {"payment_url": result["data"]["payment_url"]}
        else:
            raise HTTPException(status_code=400, detail=result.get("description", "Erreur CinetPay"))
    else:
        raise HTTPException(status_code=500, detail="Impossible de joindre CinetPay")


@app.post("/organizer/create-agent")
def create_agent(agent: AgentCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if len(agent.pin_code) != 4 or not agent.pin_code.isdigit():
        raise HTTPException(400, "Le PIN doit être 4 chiffres")
    if db.query(User).filter(User.phone_number == agent.phone_number).first():
        raise HTTPException(400, "Ce numéro est déjà utilisé.")

    # Vérifier que l'événement lui appartient bien
    event = db.query(Event).filter(Event.id == agent.event_id, Event.organizer_id == current_user.id).first()
    if not event:
        raise HTTPException(404, "Événement introuvable")

    new_agent = User(
        phone_number=agent.phone_number,
        full_name=agent.full_name,
        pin_hash=hash_pin(agent.pin_code),
        role="agent",
        org_name=current_user.org_name or current_user.full_name,
        agent_event_id=agent.event_id  # L'agent est maintenant lié à l'événement !
    )
    db.add(new_agent)
    db.commit()
    return {"message": "Agent créé avec succès"}


@app.get("/organizer/agents")
def get_agents(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Cherche les agents de cette organisation
    agents = db.query(User).filter(
        User.role == "agent",
        User.org_name == (current_user.org_name or current_user.full_name)
    ).order_by(User.created_at.desc()).all()

    res = []
    for a in agents:
        evt = db.query(Event).filter(Event.id == a.agent_event_id).first()
        res.append({
            "id": a.id,
            "full_name": a.full_name,
            "phone_number": a.phone_number,
            "event_name": evt.title if evt else "Événement supprimé"
        })
    return res


@app.delete("/organizer/agents/{agent_id}")
def delete_agent(agent_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    agent = db.query(User).filter(
        User.id == agent_id, User.role == "agent",
        User.org_name == (current_user.org_name or current_user.full_name)
    ).first()
    if not agent:
        raise HTTPException(404, "Agent introuvable")

    db.delete(agent)
    db.commit()
    return {"message": "Agent supprimé avec succès"}

@app.post("/partner-request")
async def receive_partner_request(req: PartnerRequestCreate, db: Session = Depends(get_db)):
    # On sauvegarde la demande dans la base de données
    nouvelle_demande = PartnerRequest(
        company_name=req.company_name,
        email=req.email,
        phone=req.phone,
        partnership_type=req.partnership_type,
        message=req.message
    )
    db.add(nouvelle_demande)
    db.commit()

    # Optionnel, mais fortement recommandé :
    # Si tu as gardé la fonction envoyer_billet_email d'hier, tu pourrais
    # très facilement créer un email automatique qui t'envoie une alerte !
    print(f"🤝 NOUVELLE DEMANDE PARTENAIRE : {req.company_name} - {req.email}")

    return {"status": "success", "message": "Demande enregistrée"}


@app.post("/auth/forgot-password")
async def forgot_password(data: dict, db: Session = Depends(get_db)):
    email = data.get("email")
    user = db.query(User).filter(User.email == email).first()

    if not user:
        raise HTTPException(status_code=404, detail="Cet email n'existe pas")

    # Génération du code à 6 lettres
    code = ''.join(random.choices(string.ascii_uppercase, k=6))

    # On enregistre en base (on supprime d'abord les anciens codes pour cet email)
    db.query(PasswordReset).filter(PasswordReset.email == email).delete()
    new_reset = PasswordReset(email=email, code=code)
    db.add(new_reset)
    db.commit()

    # Envoi du mail avec le code
    envoyer_mail_code(email, code)
    return {"status": "success", "message": "Code envoyé par email"}


# 3. La route pour changer le mot de passe avec le code
@app.post("/auth/reset-password")
async def reset_password(data: dict, db: Session = Depends(get_db)):
    email = data.get("email")
    code = data.get("code").upper()
    new_pwd = data.get("new_password")

    # On vérifie si le code est correct en base de données
    reset_entry = db.query(PasswordReset).filter(
        PasswordReset.email == email,
        PasswordReset.code == code
    ).first()

    if not reset_entry:
        raise HTTPException(status_code=400, detail="Code incorrect ou expiré")

    # On vérifie si le code n'est pas trop vieux (ex: plus de 15 min)
    temps_ecoule = datetime.utcnow() - reset_entry.created_at
    if temps_ecoule.total_seconds() > 900:  # 900 secondes = 15 min
        db.delete(reset_entry)
        db.commit()
        raise HTTPException(status_code=400, detail="Code expiré (validité 15min)")

    # Tout est bon ! On change le mot de passe de l'utilisateur
    user = db.query(User).filter(User.email == email).first()
    user.hashed_password = pwd_context.hash(new_pwd)  # Utilise ton système de hash

    # On nettoie la table des codes
    db.delete(reset_entry)
    db.commit()

    return {"status": "success", "message": "Mot de passe modifié avec succès"}


@app.post("/organizer/events/{event_id}/cancel-request")
def request_cancellation(event_id: int, data: CancelRequestCreate, db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_user)):
    if current_user.role != "organizer":
        raise HTTPException(403, "Accès refusé")

    if len(data.reason) < 100:
        raise HTTPException(400, "La raison doit faire au moins 100 caractères.")

    event = db.query(Event).filter(Event.id == event_id, Event.organizer_id == current_user.id).first()
    if not event:
        raise HTTPException(404, "Événement introuvable")

    # On crée la demande d'annulation
    cancel_req = CancellationRequest(
        event_id=event.id,
        organizer_id=current_user.id,
        reason=data.reason
    )
    db.add(cancel_req)
    db.commit()

    # Optionnel : Tu pourrais ajouter un print() ici pour simuler une alerte admin
    print(f"🚨 ALERTE ANNULATION : L'événement {event.title} demande à être annulé !")

    return {"message": "Demande d'annulation soumise."}


@app.get("/admin/cancellations")
def get_admin_cancellations(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(403, "Accès refusé")

    requests = db.query(CancellationRequest).order_by(CancellationRequest.created_at.desc()).all()
    return [{
        "id": r.id,
        "event_title": r.event.title if r.event else "Événement supprimé",
        "organizer_name": r.organizer.org_name or r.organizer.full_name,
        "reason": r.reason,
        "status": r.status,
        "created_at": r.created_at
    } for r in requests]


@app.post("/admin/cancellations/{request_id}/approve")
def approve_cancellation(request_id: int, db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(403, "Accès refusé")

    # 1. On cherche la demande
    cancel_req = db.query(CancellationRequest).filter(CancellationRequest.id == request_id).first()
    if not cancel_req:
        raise HTTPException(404, "Demande introuvable")

    if cancel_req.status == "traite":
        raise HTTPException(400, "Cette demande a déjà été traitée")

    # 2. On cherche l'événement associé
    event = db.query(Event).filter(Event.id == cancel_req.event_id).first()

    if event:
        # 🔴 LA CORRECTION EST ICI :
        # On coupe le lien entre la demande et l'événement pour ne pas bloquer PostgreSQL
        cancel_req.event_id = None

        # Nettoyage de sécurité avant suppression
        db.query(Ticket).filter(Ticket.event_id == event.id).delete()
        db.query(User).filter(User.agent_event_id == event.id).delete()

        # Et enfin, on supprime l'événement en toute sécurité
        db.delete(event)

    # 3. On marque la demande comme traitée
    cancel_req.status = "traite"
    db.commit()

    return {"message": "Événement définitivement supprimé."}
# ── AUTH ────────────────────────────────────────────────────────
@app.post("/auth/register")
def register(data: UserRegister, db: Session = Depends(get_db)):
    if data.role not in ROLES_VALIDES:
        raise HTTPException(400, f"Rôle invalide. Valeurs: {ROLES_VALIDES}")
    if len(data.pin_code) != 4 or not data.pin_code.isdigit():
        raise HTTPException(400, "Le PIN doit être 4 chiffres")
    if db.query(User).filter(User.phone_number == data.phone_number).first():
        raise HTTPException(400, "Ce numéro est déjà utilisé")
    user = User(
        phone_number=data.phone_number,
        full_name=data.full_name,
        email=data.email,
        pin_hash=hash_pin(data.pin_code),
        role=data.role,
        org_name=data.org_name,
        org_type=data.org_type,
    )
    db.add(user); db.commit(); db.refresh(user)
    return {
        "message": "Compte créé",
        "user_id": user.id,
        "role": user.role,
        "access_token": create_token(user.id, user.role),
        "user": {
            "id": user.id, "name": user.full_name,
            "role": user.role, "phone": user.phone_number,
            "org_name": user.org_name, "org_type": user.org_type,
        }
    }

@app.post("/auth/login")
def login(data: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.phone_number == data.phone_number).first()
    if not user or not verify_pin(data.pin_code, user.pin_hash):
        raise HTTPException(401, "Numéro ou PIN incorrect")
    return {
        "access_token": create_token(user.id, user.role),
        "token_type": "bearer",
        "user": {
            "id": user.id, "name": user.full_name,
            "role": user.role, "phone": user.phone_number,
            "email": user.email,
            "org_name": user.org_name, "org_type": user.org_type,
        }
    }

@app.get("/auth/me")
def me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "name": current_user.full_name,
        "phone": current_user.phone_number,
        "email": current_user.email,
        "role": current_user.role,
        "org_name": current_user.org_name,
        "org_type": current_user.org_type,
        "created_at": current_user.created_at,
    }

@app.put("/auth/me")
def update_profile(data: UserUpdate, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    for k, v in data.model_dump(exclude_none=True).items():
        setattr(current_user, k, v)
    db.commit()
    return {"message": "Profil mis à jour"}

@app.put("/auth/pin")
def change_pin(data: PinChange, db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)):
    if not verify_pin(data.old_pin, current_user.pin_hash):
        raise HTTPException(400, "Ancien PIN incorrect")
    if len(data.new_pin) != 4 or not data.new_pin.isdigit():
        raise HTTPException(400, "Le nouveau PIN doit être 4 chiffres")
    current_user.pin_hash = hash_pin(data.new_pin)
    db.commit()
    return {"message": "PIN modifié avec succès"}

# ── ÉVÉNEMENTS ─────────────────────────────────────────────────
@app.get("/events")
def list_events(
    page: int = 1,
    limit: int = 6,
    sort_by: Optional[str] = "date_asc",
    q: Optional[str] = None,
    categories: Optional[str] = None,
    location: Optional[str] = None,
    max_price: Optional[float] = None,
    upcoming_only: bool = False,
    db: Session = Depends(get_db)
):
    # 1. On prend tous les événements actifs
    query = db.query(Event).filter(Event.is_active == True)

    if upcoming_only:
        query = query.filter(Event.event_date >= datetime.utcnow())

    # 2. Filtres (Recherche, Catégorie, Lieu, Prix)
    if q:
        query = query.filter(or_(
            Event.title.ilike(f"%{q}%"),
            Event.location.ilike(f"%{q}%"),
            Event.description.ilike(f"%{q}%"),
        ))
    if categories:
        cat_list = categories.split(",")
        query = query.filter(Event.category.in_(cat_list))
    if location and location != "Tous les lieux":
        query = query.filter(Event.location == location)
    if max_price is not None:
        query = query.filter(Event.price <= max_price)

    # 3. Tri (Plus récents, Prix, etc.)
    if sort_by == "price_asc":
        query = query.order_by(Event.price.asc())
    elif sort_by == "price_desc":
        query = query.order_by(Event.price.desc())
    elif sort_by == "date_desc":
        query = query.order_by(Event.event_date.desc())
    else: # date_asc par défaut
        query = query.order_by(Event.event_date.asc())

    # 4. Pagination
    total_items = query.count() # On compte le vrai total
    skip = (page - 1) * limit
    events = query.offset(skip).limit(limit).all()

    # 5. On renvoie le bon format (Objet avec total + events)
    return {
        "total": total_items,
        "page": page,
        "limit": limit,
        "events": [_event_dict(e) for e in events]
    }

@app.get("/events/{event_id}")
def get_event(event_id: int, db: Session = Depends(get_db)):
    e = db.query(Event).filter(Event.id == event_id).first()
    if not e:
        raise HTTPException(404, "Événement introuvable")
    return _event_dict(e)

@app.post("/events")
def create_event(data: EventCreate, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    if current_user.role not in ("organizer", "agent"):
        raise HTTPException(403, "Réservé aux organisations")
    event = Event(**data.model_dump(), organizer_id=current_user.id)
    db.add(event); db.commit(); db.refresh(event)
    return {"message": "Événement créé", "event_id": event.id, "event": _event_dict(event)}

@app.put("/events/{event_id}")
def update_event(event_id: int, data: EventUpdate,
                 db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    event = db.query(Event).filter(
        Event.id == event_id, Event.organizer_id == current_user.id).first()
    if not event:
        raise HTTPException(404, "Événement introuvable ou non autorisé")
    for k, v in data.model_dump(exclude_none=True).items():
        setattr(event, k, v)
    db.commit()
    return {"message": "Événement mis à jour"}

@app.delete("/events/{event_id}")
def delete_event(event_id: int, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    event = db.query(Event).filter(
        Event.id == event_id, Event.organizer_id == current_user.id).first()
    if not event:
        raise HTTPException(404, "Événement introuvable ou non autorisé")
    event.is_active = False
    db.commit()
    return {"message": "Événement supprimé"}

@app.get("/my-events")
def my_events(db: Session = Depends(get_db),
              current_user: User = Depends(get_current_user)):
    if current_user.role not in ("artiste", "organizer", "agent"):
        raise HTTPException(403, "Réservé aux organisateurs")
    events = db.query(Event).filter(
        Event.organizer_id == current_user.id
    ).order_by(Event.event_date.desc()).all()
    return [{
        **_event_dict(e),
        "revenue": e.seats_sold * e.price,
        "fill_rate": round(e.seats_sold / e.total_seats * 100, 1) if e.total_seats else 0,
    } for e in events]

@app.post("/organizer/payout-request")
async def create_payout_request(data: dict, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # 1. On vérifie que c'est bien un organisateur
    if user.role != "organizer":
        raise HTTPException(status_code=403, detail="Accès refusé")

    event_id = data.get("event_id")
    amount = data.get("amount")

    # 2. On crée la demande dans la table des retraits
    new_request = PayoutRequest(
        event_id=event_id,
        organizer_id=user.id,
        amount=amount,
        status="en_attente"
    )
    db.add(new_request)
    db.commit()

    return {"status": "success", "message": "Demande transmise ! "}

# ── CATÉGORIES ─────────────────────────────────────────────────
@app.get("/categories")
def get_categories(db: Session = Depends(get_db)):
    from sqlalchemy import func
    cats = db.query(Event.category, func.count(Event.id).label("count"))\
             .filter(Event.is_active == True)\
             .group_by(Event.category).all()
    return [{"category": c, "count": n} for c, n in cats]

# ── BILLETS ─────────────────────────────────────────────────────
@app.post("/tickets/buy")
def buy_ticket(data: BuyTicket, db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)):
    event = db.query(Event).filter(Event.id == data.event_id, Event.is_active == True).first()
    if not event:
        raise HTTPException(404, "Événement introuvable")
    if event.seats_sold >= event.total_seats:
        raise HTTPException(400, "Plus de places disponibles")
    if event.event_date < datetime.utcnow():
        raise HTTPException(400, "Événement déjà passé")
    ticket = Ticket(user_id=current_user.id, event_id=event.id)
    db.add(ticket); db.commit(); db.refresh(ticket)
    return {
        "message": "Billet réservé",
        "ticket_id": ticket.id,
        "qr_hash": ticket.qr_hash,
        "amount": event.price,
        "currency": "FCFA",
        "instruction": f"Composez #150*50# sur Orange Money pour payer {int(event.price)} FCFA",
        "ussd_code": "#150*50#",
    }


@app.post("/payment/webhook")
async def cinetpay_webhook(request: Request, db: Session = Depends(get_db)):
    # CinetPay envoie les infos en form-data
    form_data = await request.form()
    cpm_trans_id = form_data.get("cpm_trans_id")

    if not cpm_trans_id:
        raise HTTPException(status_code=400, detail="Transaction ID manquant")

    # 1. Interrogation de CinetPay pour s'assurer que c'est un vrai paiement
    payload = {
        "apikey": CINETPAY_API_KEY,
        "site_id": CINETPAY_SITE_ID,
        "transaction_id": cpm_trans_id
    }
    response = requests.post(CINETPAY_CHECK_URL, json=payload)
    data = response.json()

    # 2. Si le code est "00", le paiement est validé
    if data.get("code") == "00":
        payment = db.query(Payment).filter(Payment.transaction_id == cpm_trans_id).first()
        if payment and payment.status != "paye":
            payment.status = "paye"

            ticket = db.query(Ticket).filter(Ticket.id == payment.ticket_id).first()
            if ticket:
                ticket.payment_status = "paye"
                ticket.payment_ref = cpm_trans_id

                ticket.qr_hash = f"BT-{''.join(random.choices(string.ascii_uppercase + string.digits, k=5))}"

            event = db.query(Event).filter(Event.id == ticket.event_id).first()
            if event:
                event.seats_sold += 1

            db.commit()

    if ticket:
        ticket.payment_status = "paye"
        ticket.payment_ref = cpm_trans_id
        ticket.qr_hash = f"BT-{''.join(random.choices(string.ascii_uppercase + string.digits, k=5))}"

        # 🔴 NOUVEAU : On récupère l'utilisateur pour avoir son email
        user = db.query(User).filter(User.id == payment.user_id).first()

        # On sauvegarde d'abord la base de données
        db.commit()

        # 🔴 NOUVEAU : On envoie l'email en arrière-plan si le client a un email !
        if user and user.email:
            envoyer_billet_email(user.email, user.full_name, ticket, event, payment.amount)

        return {"status": "success"}

    # Il faut toujours retourner un statut 200 à CinetPay pour qu'ils arrêtent d'appeler le webhook
    return {"status": "ok"}

# Simuler paiement en dev
@app.post("/payment/simulate/{ticket_id}")
def simulate_payment(ticket_id: int, db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)):
    # 1. On cherche le billet en attente
    ticket = db.query(Ticket).filter(
        Ticket.id == ticket_id, Ticket.user_id == current_user.id).first()

    if not ticket:
        raise HTTPException(404, "Billet introuvable")

    # 2. On valide le paiement
    ticket.payment_status = "paye"
    ticket.payment_ref = f"SIM_{uuid.uuid4().hex[:8].upper()}"

    # 🔴 CORRECTION : On génère le vrai QR Code au moment de la simulation !
    ticket.qr_hash = f"BT-SIM-{''.join(random.choices(string.ascii_uppercase + string.digits, k=5))}"

    # 3. On ajoute la place vendue dans les stats de l'événement
    event = db.query(Event).filter(Event.id == ticket.event_id).first()
    if event:
        event.seats_sold += 1

    db.commit()

    # 🔴 CORRECTION : On déclenche l'envoi de l'email SI l'utilisateur a renseigné un email
    if current_user.email:
        # Assure-toi d'avoir corrigé l'espace dans SENDER_EMAIL plus haut dans ton code !
        envoyer_billet_email(current_user.email, current_user.full_name, ticket, event, event.price)
    else:
        print("⚠️ Email non envoyé : L'utilisateur n'a pas d'adresse email dans son profil.")

    return {"message": "Paiement simulé avec succès", "ref": ticket.payment_ref}
@app.get("/tickets/my")
def my_tickets(db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)):
    tickets = db.query(Ticket).filter(
        Ticket.user_id == current_user.id
    ).order_by(Ticket.purchased_at.desc()).all()
    return [{
        "ticket_id": t.id, "qr_hash": t.qr_hash,
        "payment_status": t.payment_status,
        "event_title": t.event.title if t.event else "",
        "event_date": t.event.event_date if t.event else None,
        "event_location": t.event.location if t.event else "",
        "price": t.event.price if t.event else 0,
        "is_used": t.is_used,
        "purchased_at": t.purchased_at,
    } for t in tickets]

@app.get("/tickets/paid")
def my_paid_tickets(db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    tickets = db.query(Ticket).filter(
        Ticket.user_id == current_user.id,
        Ticket.payment_status == "paye"
    ).order_by(Ticket.purchased_at.desc()).all()
    return [{
        "ticket_id": t.id, "qr_hash": t.qr_hash,
        "event_title": t.event.title if t.event else "",
        "event_date": t.event.event_date if t.event else None,
        "event_location": t.event.location if t.event else "",
        "price": t.event.price if t.event else 0,
        "is_used": t.is_used,
        "purchased_at": t.purchased_at,
    } for t in tickets]

@app.post("/scan")
def valider_billet(
    data: ScanTicket,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Sécurité : Seuls les agents (et les boss) ont le droit de scanner
    if current_user.role not in ("agent", "organizer", "organisation"):
        raise HTTPException(status_code=403, detail="Accès refusé. Réservé aux contrôleurs.")

    # VERROU 1 : Est-ce que ce billet existe dans la base ?
    ticket = db.query(Ticket).filter(Ticket.qr_hash == data.qr_data).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Billet introuvable (Faux billet ou QR mal lu).")

    # VERROU 2 : Est-ce que le billet a été payé ?
    if ticket.payment_status != "paye":
        raise HTTPException(status_code=400, detail="Billet non valide (Paiement en attente).")

    # VERROU 3 : Le billet a-t-il déjà été utilisé ?
    if ticket.is_used:
        date_used = ticket.used_at.strftime('%H:%M:%S') if ticket.used_at else "?"
        raise HTTPException(status_code=400, detail=f"Alerte : Billet DÉJÀ UTILISÉ à {date_used} !")

    # 🚨 VERROU 4 : L'agent a-t-il le droit de scanner CET événement précis ?
    if current_user.role == "agent":
        if current_user.agent_event_id and current_user.agent_event_id != ticket.event_id:
            raise HTTPException(status_code=403, detail="Alerte : Vous n'êtes pas assigné à cet événement !")

    # 🟢 TOUT EST BON : On valide l'entrée !
    try:
        ticket.is_used = True
        ticket.used_at = datetime.utcnow()
        db.commit()
        return {
            "valid": True,
            "message": f"Validé ! {ticket.owner.full_name} peut entrer.",
            "event": ticket.event.title
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="Erreur interne lors de la sauvegarde.")
@app.get("/tickets/scan/stats/{event_id}")
def scan_stats(event_id: int, db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)):
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(404, "Événement introuvable")
    total_paid = db.query(Ticket).filter(
        Ticket.event_id == event_id, Ticket.payment_status == "paye").count()
    total_used = db.query(Ticket).filter(
        Ticket.event_id == event_id, Ticket.is_used == True).count()
    return {
        "event_title": event.title,
        "billets_vendus": total_paid,
        "personnes_entrees": total_used,
        "personnes_restantes": total_paid - total_used,
        "taux_entree": f"{(total_used/total_paid*100) if total_paid else 0:.1f}%",
    }


# ── ROUTES ADMIN ────────────────────────────────────────────────

@app.get("/admin/global-stats")
def admin_global_stats(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(403, "Accès refusé")
    total_tickets   = db.query(Ticket).filter(Ticket.payment_status == "paye").count()
    total_users     = db.query(User).filter(User.role.in_(["fan", "organizer"])).count()
    total_events    = db.query(Event).filter(Event.is_active == True).count()
    total_orgas     = db.query(User).filter(User.role == "organizer").count()
    pending_payouts = db.query(PayoutRequest).filter(PayoutRequest.status == "en_attente").count()
    revenue_net     = db.query(func.sum(Payment.platform_fee)).filter(Payment.status == "completed").scalar() or 0
    ca_total        = db.query(func.sum(Payment.amount)).filter(Payment.status == "completed").scalar() or 0
    return {
        "total_tickets":   total_tickets,
        "total_users":     total_users,
        "total_events":    total_events,
        "total_orgas":     total_orgas,
        "pending_payouts": pending_payouts,
        "revenue_net":     round(revenue_net, 2),
        "ca_total":        round(ca_total, 2),
    }

@app.get("/admin/users")
def admin_list_users(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(403, "Accès refusé")
    users = db.query(User).order_by(User.created_at.desc()).all()
    return [{
        "id": u.id, "name": u.full_name, "phone": u.phone_number,
        "email": u.email, "role": u.role, "is_active": u.is_active,
        "org_name": u.org_name, "org_type": u.org_type,
        "created_at": u.created_at,
    } for u in users]

@app.put("/admin/users/{user_id}")
def admin_update_user(user_id: int, data: dict, db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(403, "Accès refusé")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "Utilisateur introuvable")
    if "is_active" in data:
        user.is_active = data["is_active"]
    db.commit()
    return {"message": "Utilisateur mis à jour"}

@app.get("/admin/events")
def admin_list_events(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(403, "Accès refusé")
    events = db.query(Event).order_by(Event.created_at.desc()).all()
    return [_event_dict(e) for e in events]

@app.put("/admin/events/{event_id}")
def admin_update_event(event_id: int, data: dict, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(403, "Accès refusé")
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(404, "Événement introuvable")
    if "is_active" in data:
        event.is_active = data["is_active"]
    db.commit()
    return {"message": "Événement mis à jour"}

@app.get("/admin/partners")
def admin_list_partners(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(403, "Accès refusé")
    partners = db.query(PartnerRequest).order_by(PartnerRequest.created_at.desc()).all()
    return [{
        "id": p.id, "company_name": p.company_name, "email": p.email,
        "phone": p.phone, "partnership_type": p.partnership_type,
        "message": p.message, "status": p.status, "created_at": p.created_at,
    } for p in partners]

@app.put("/admin/partners/{partner_id}")
def admin_update_partner(partner_id: int, data: dict, db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(403, "Accès refusé")
    partner = db.query(PartnerRequest).filter(PartnerRequest.id == partner_id).first()
    if not partner:
        raise HTTPException(404, "Demande introuvable")
    if "status" in data:
        partner.status = data["status"]
    db.commit()
    return {"message": "Demande mise à jour"}

@app.get("/admin/payouts/pending")
async def get_pending_payouts(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(status_code=403)

    # On récupère les demandes 'en_attente' avec les détails de l'événement et de l'orga
    return db.query(PayoutRequest).filter(PayoutRequest.status == "en_attente").all()


@app.post("/admin/payouts/{payout_id}/validate")
async def validate_payout(payout_id: int, db: Session = Depends(get_db),
                          current_user: User = Depends(get_current_user)):
    # 1. Vérification de sécurité : Seul l'admin (Emmanuel) peut cliquer
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Accès non autorisé")

    # 2. On cherche la demande de retrait dans la base
    payout = db.query(PayoutRequest).filter(PayoutRequest.id == payout_id).first()

    if not payout:
        raise HTTPException(status_code=404, detail="Demande de retrait introuvable")

    if payout.status == "paye":
        return {"message": "Ce versement a déjà été effectué"}

    # 3. On met à jour le statut
    payout.status = "paye"
    payout.paid_at = datetime.utcnow()  # Optionnel : garder l'heure du virement

    # 4. On enregistre le changement
    db.commit()

    print(f"💰 VERSEMENT VALIDÉ : {payout.amount} FCFA envoyés pour l'événement {payout.event_id}")
    return {"status": "success", "message": "Le retrait est marqué comme payé"}

# ── FAVORIS ─────────────────────────────────────────────────────
@app.get("/favorites")
def get_favorites(db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    favs = db.query(Favorite).filter(Favorite.user_id == current_user.id).all()
    return [_event_dict(f.event) for f in favs if f.event]

@app.post("/favorites/{event_id}")
def add_favorite(event_id: int, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    existing = db.query(Favorite).filter(
        Favorite.user_id == current_user.id,
        Favorite.event_id == event_id).first()
    if existing:
        db.delete(existing); db.commit()
        return {"message": "Retiré des favoris", "action": "removed"}
    fav = Favorite(user_id=current_user.id, event_id=event_id)
    db.add(fav); db.commit()
    return {"message": "Ajouté aux favoris", "action": "added"}

# ── STATS DASHBOARD ─────────────────────────────────────────────
@app.get("/dashboard/stats")
def dashboard_stats(db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    tickets = db.query(Ticket).filter(Ticket.user_id == current_user.id).all()
    paid    = [t for t in tickets if t.payment_status == "paye"]
    total_spend = sum(t.event.price for t in paid if t.event)
    upcoming = [t for t in paid if t.event and not t.is_used and t.event.event_date >= datetime.utcnow()]
    return {
        "total_tickets": len(paid),
        "upcoming_events": len(upcoming),
        "total_spend": total_spend,
        "tickets": [{
            "ticket_id": t.id,
            "event_title": t.event.title if t.event else "",
            "event_date": t.event.event_date if t.event else None,
            "event_location": t.event.location if t.event else "",
            "price": t.event.price if t.event else 0,
            "is_used": t.is_used,
            "qr_hash": t.qr_hash,
        } for t in paid[:5]],
    }


@app.get("/organizer/stats")
def organizer_stats(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if current_user.role not in ("artiste", "organizer", "agent"):
        raise HTTPException(403, "Réservé aux organisateurs")

    events = db.query(Event).filter(Event.organizer_id == current_user.id).all()

    total_sold = sum(e.seats_sold for e in events)
    active_events = [e for e in events if e.is_active and e.event_date >= datetime.utcnow()]

    total_cagnotte_globale = 0.0

    # 1. On calcule la cagnotte globale avec le JOIN explicite
    for e in events:
        cagnotte_event = db.query(func.sum(Payment.organizer_amount)).join(
            Ticket, Payment.ticket_id == Ticket.id  # <-- LA CORRECTION EST ICI
        ).filter(
            Ticket.event_id == e.id,
            Payment.status == "completed"
        ).scalar() or 0.0

        total_cagnotte_globale += cagnotte_event

    events_data = []
    # 2. On prépare la liste des 10 premiers événements avec le JOIN explicite
    for e in events[:10]:
        cagnotte_event = db.query(func.sum(Payment.organizer_amount)).join(
            Ticket, Payment.ticket_id == Ticket.id  # <-- LA CORRECTION EST ICI AUSSI
        ).filter(
            Ticket.event_id == e.id,
            Payment.status == "completed"
        ).scalar() or 0.0

        events_data.append({
            **_event_dict(e),
            "revenue": e.seats_sold * e.price,  # Chiffre d'affaires Brut (théorique)
            "cagnotte": cagnotte_event,  # L'argent NET qu'il peut retirer !
            "fill_rate": round(e.seats_sold / e.total_seats * 100, 1) if e.total_seats else 0,
        })

    return {
        "total_events": len(events),
        "active_events": len(active_events),
        "total_sold": total_sold,
        "total_revenue": total_cagnotte_globale,
        "org_name": current_user.org_name or current_user.full_name,
        "org_type": current_user.org_type or current_user.role,
        "events": events_data
    }


# --- ROUTE DE NOTIFICATION CINETPAY (WEBHOOK) ---
# C'est cette URL que CinetPay appelle en secret pour valider le billet
@app.post("/api/payment/notify")
async def payment_notify(request: Request, db: Session = Depends(get_db)):
    try:
        # 1. On récupère les données du formulaire envoyé par CinetPay
        data = await request.form()
        transaction_id = data.get("cpm_trans_id")
        site_id = data.get("cpm_site_id")

        # 2. Vérification de sécurité élémentaire
        if site_id != CINETPAY_SITE_ID:
            return {"status": "error", "message": "Invalid Site ID"}

        # 3. On cherche le billet correspondant
        ticket = db.query(Ticket).filter(Ticket.transaction_id == transaction_id).first()

        if ticket:
            if ticket.payment_status != "paye":
                # ✅ VALIDATION DU BILLET
                ticket.payment_status = "paye"

                # Mise à jour du compteur de l'événement
                if ticket.event:
                    ticket.event.seats_sold += 1

                db.commit()
                print(f"--- SUCCÈS : Billet {ticket.qr_hash} activé ! ---")
                return {"status": "success"}
            else:
                return {"status": "already_paid"}

    except Exception as e:
        print(f"--- ERREUR NOTIFICATION : {str(e)} ---")
        return {"status": "error", "message": str(e)}

    return {"status": "not_found"}

#DESTRUCTION
# --- 1. ROUTE POUR RÉCUPÉRER LE NOM DE L'ÉVÉNEMENT ---
@app.get("/agent/current-event")
def get_agent_event(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # On cherche le premier événement créé par le patron de cet agent
    event = db.query(Event).join(User, Event.organizer_id == User.id).filter(
        User.org_name == current_user.org_name).first()

    if event:
        return {"event_name": event.title}
    return {"event_name": "Événement en cours"}


# --- 2. ROUTE POUR AUTODÉTRUIRE L'AGENT ---
@app.delete("/agent/delete-account")
def delete_agent_account(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Sécurité : On s'assure que seul un compte "agent" peut s'autodétruire
    if current_user.role != "agent":
        raise HTTPException(status_code=403, detail="Seuls les agents peuvent être supprimés ainsi.")

    db.delete(current_user)
    db.commit()
    return {"message": "Compte agent supprimé définitivement."}

# =================================================================
# 🌉 PONT DE COMPATIBILITÉ POUR LE FRONT-END (HTML/JS)
# =================================================================
class OrderCreateLegacy(BaseModel):
    event_id: int
    buyer_name: str
    buyer_phone: str
    tier_name: str
    price: float


import random
import string


@app.post("/api/orders")
def create_order_legacy(order: OrderCreateLegacy, db: Session = Depends(get_db)):
    event = db.query(Event).filter(Event.id == order.event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Événement introuvable")

    if event.seats_sold >= event.total_seats:
        raise HTTPException(status_code=400, detail="Complet !")

    # On cherche le compte du fan grâce à son numéro de téléphone
    user = db.query(User).filter(User.phone_number == order.buyer_phone).first()

    #@app.post("/api/tickets/purchase") # Ou le nom de ta route actuelle

@app.get("/api/my-tickets")
def get_my_tickets_legacy(phone: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.phone_number == phone).first()
    if not user:
        return []

    # On récupère les billets payés de l'utilisateur
    tickets = db.query(Ticket).filter(
        Ticket.user_id == user.id,
        Ticket.payment_status == "paye"
    ).all()

    # On formate les données pour que mes_billets.html les lise sans planter
    return [{
        "ticket_id": t.qr_hash,
        "event_title": t.event.title if t.event else "",
        "event_date": t.event.event_date.isoformat() if t.event.event_date else None,
        "event_location": t.event.location if t.event else "",
        "price_paid": t.event.price if t.event else 0,
        "tier_name": "Standard"  # Claude a retiré cette info, on met Standard par défaut
    } for t in tickets if t.event]


# ── GESTION DES ABONNEMENTS (FOLLOWERS) ────────────────────────
@app.get("/organizers")
def get_organizers(db: Session = Depends(get_db), current_user: User = Depends(get_optional_user)):
    # On récupère uniquement les utilisateurs avec le rôle "organizer"
    organizers = db.query(User).filter(User.role == "organizer").all()
    result = []

    for org in organizers:
        # Calcul des statistiques en temps réel
        events_count = db.query(Event).filter(Event.organizer_id == org.id).count()
        followers_count = db.query(Follower).filter(Follower.organizer_id == org.id).count()

        # Vérifier si l'utilisateur actuel est déjà abonné
        is_followed = False
        if current_user:
            is_followed = db.query(Follower).filter(
                Follower.user_id == current_user.id,
                Follower.organizer_id == org.id
            ).first() is not None

        # Préparation du nom et des initiales
        name = org.org_name or org.full_name or "Organisateur Anonyme"
        initials = "".join([w[0] for w in name.split()[:2]]).upper()

        result.append({
            "id": org.id,
            "name": name,
            "type": org.org_type or "Organisation",
            "eventsCount": events_count,
            "followersCount": followers_count,
            "initials": initials,
            "is_followed": is_followed
        })
    return result


@app.post("/organizers/{org_id}/follow")
def follow_organizer(org_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    org = db.query(User).filter(User.id == org_id, User.role == "organizer").first()
    if not org:
        raise HTTPException(404, "Organisateur introuvable")

    existing = db.query(Follower).filter(Follower.user_id == current_user.id, Follower.organizer_id == org_id).first()

    if existing:
        db.delete(existing)
        db.commit()
        return {"message": f"Vous ne suivez plus {org.org_name or org.full_name}", "is_followed": False}
    else:
        new_follow = Follower(user_id=current_user.id, organizer_id=org_id)
        db.add(new_follow)
        db.commit()
        return {"message": f"Vous suivez maintenant {org.org_name or org.full_name}", "is_followed": True}

# TEST SUR BILLET ACHETE

@app.post("/payment/test-confirm")
async def test_confirm_payment(data: dict, db: Session = Depends(get_db)):
    # 1. On récupère les infos envoyées par le frontend
    event_id = data.get("event_id")
    email = data.get("email")

    # 2. LA CORRECTION : On cherche le véritable utilisateur dans la base de données
    user = db.query(User).filter(User.email == email).first()

    if not user:
        # Sécurité : Si l'email tapé ne correspond à aucun compte
        raise HTTPException(status_code=404, detail="Aucun compte trouvé avec cet email")

    # 3. On génère le faux code QR
    qr_code = f"BT-TEST-{random.randint(1000, 9999)}"

    # 4. On crée le ticket en utilisant `user_id` (comme l'exige ton modèle)
    new_ticket = Ticket(
        event_id=event_id,
        user_id=user.id,  # <-- C'EST LA CLÉ ! On relie le billet au compte.
        payment_status="paye",
        qr_hash=qr_code
    )
    db.add(new_ticket)
    db.commit()

    # 5. On déclenche l'envoi de l'email
    event = db.query(Event).filter(Event.id == event_id).first()

    # On récupère le nom depuis le compte utilisateur pour l'email
    nom_client = user.full_name if hasattr(user, 'full_name') else "Client GoEvent"
    envoyer_billet_email(email, nom_client, new_ticket, event, event.price)

    return {"status": "success", "ticket_id": new_ticket.id}
