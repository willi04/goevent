"""
GoEvent — seed.py
Peuple la base de données PostgreSQL avec des données de démonstration.
Utilisation : python seed.py
"""

import os
import uuid
import random
from datetime import datetime, timedelta
from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, Integer, String, Boolean, Float, DateTime, ForeignKey, Text
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from sqlalchemy.dialects.postgresql import JSONB
from passlib.context import CryptContext

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL manquante dans .env")

engine      = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
Base        = declarative_base()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_pin(pin): return pwd_context.hash(pin)

# ── Réimporter les modèles localement (évite d'importer FastAPI) ──

class User(Base):
    __tablename__ = "users"
    id              = Column(Integer, primary_key=True, index=True)
    phone_number    = Column(String, unique=True, index=True)
    full_name       = Column(String)
    email           = Column(String, nullable=True)
    pin_hash        = Column(String)
    profile_picture = Column(Text, nullable=True)
    role            = Column(String, default="fan")
    org_name        = Column(String, nullable=True)
    org_type        = Column(String, nullable=True)
    is_active       = Column(Boolean, default=True)
    created_at      = Column(DateTime, default=datetime.utcnow)
    orange_money    = Column(String(20), nullable=True)

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
    ticket_tiers    = Column(JSONB, default=list)
    is_active       = Column(Boolean, default=True)
    created_at      = Column(DateTime, default=datetime.utcnow)
    organizer_id    = Column(Integer, ForeignKey("users.id"))

class Ticket(Base):
    __tablename__ = "tickets"
    id             = Column(Integer, primary_key=True, index=True)
    qr_hash        = Column(String, unique=True, index=True)
    payment_status = Column(String, default="attente")
    payment_ref    = Column(String, default="")
    is_used        = Column(Boolean, default=False)
    used_at        = Column(DateTime, nullable=True)
    purchased_at   = Column(DateTime, default=datetime.utcnow)
    user_id        = Column(Integer, ForeignKey("users.id"))
    event_id       = Column(Integer, ForeignKey("events.id"))

class Payment(Base):
    __tablename__ = "payments"
    id               = Column(Integer, primary_key=True, index=True)
    user_id          = Column(Integer)
    ticket_id        = Column(Integer)
    amount           = Column(Float)
    base_price       = Column(Float, default=0)
    platform_fee     = Column(Float, default=0)
    organizer_amount = Column(Float, default=0)
    status           = Column(String, default="pending")
    transaction_id   = Column(String)

class Favorite(Base):
    __tablename__ = "favorites"
    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, ForeignKey("users.id"))
    event_id   = Column(Integer, ForeignKey("events.id"))
    created_at = Column(DateTime, default=datetime.utcnow)

class PartnerRequest(Base):
    __tablename__ = "partner_requests"
    id               = Column(Integer, primary_key=True, index=True)
    company_name     = Column(String)
    email            = Column(String)
    phone            = Column(String)
    partnership_type = Column(String)
    message          = Column(Text)
    status           = Column(String, default="nouveau")
    created_at       = Column(DateTime, default=datetime.utcnow)

class PasswordReset(Base):
    __tablename__ = "password_resets"
    id         = Column(Integer, primary_key=True, index=True)
    email      = Column(String)
    code       = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

class PayoutRequest(Base):
    __tablename__ = "payout_requests"
    id           = Column(Integer, primary_key=True, index=True)
    event_id     = Column(Integer, ForeignKey("events.id"))
    organizer_id = Column(Integer, ForeignKey("users.id"))
    amount       = Column(Float)
    status       = Column(String, default="en_attente")
    created_at   = Column(DateTime, default=datetime.utcnow)

# ── DONNÉES ───────────────────────────────────────────────────────

USERS_DATA = [
    # Organisateurs
    {
        "phone_number": "+23670000001",
        "full_name": "Musica Events RCA",
        "email": "musica@goevent.africa",
        "pin": "1234",
        "role": "organizer",
        "org_name": "Musica Events",
        "org_type": "Entreprise",
        "orange_money": "+23670000001",
    },
    {
        "phone_number": "+23670000002",
        "full_name": "Festival Kaga Bandoro",
        "email": "festival@goevent.africa",
        "pin": "1234",
        "role": "organizer",
        "org_name": "Festival Kaga",
        "org_type": "ONG",
        "orange_money": "+23670000002",
    },
    {
        "phone_number": "+23670000003",
        "full_name": "Bangui Live Production",
        "email": "banguilive@goevent.africa",
        "pin": "1234",
        "role": "organizer",
        "org_name": "Bangui Live",
        "org_type": "Institution",
        "orange_money": "+23670000003",
    },
    # Fans
    {
        "phone_number": "+23672000001",
        "full_name": "Jean-Pierre Mbaïki",
        "email": "jp@gmail.com",
        "pin": "0001",
        "role": "fan",
        "orange_money": "+23672000001",
    },
    {
        "phone_number": "+23672000002",
        "full_name": "Aïcha Oumar",
        "email": "aicha@gmail.com",
        "pin": "0002",
        "role": "fan",
        "orange_money": "+23672000002",
    },
    {
        "phone_number": "+23672000003",
        "full_name": "Rodrigue Nzinga",
        "email": "rodrigue@gmail.com",
        "pin": "0003",
        "role": "fan",
        "orange_money": "+23672000003",
    },
    {
        "phone_number": "+23672000004",
        "full_name": "Marie-Claire Samba",
        "email": "marie@gmail.com",
        "pin": "0004",
        "role": "fan",
        "orange_money": "+23672000004",
    },
    {
        "phone_number": "+23672000005",
        "full_name": "Christian Boganda",
        "email": "christian@gmail.com",
        "pin": "0005",
        "role": "fan",
        "orange_money": "+23672000005",
    },
    # Agent
    {
        "phone_number": "+23673000001",
        "full_name": "Agent Entrée VIP",
        "email": "agent@goevent.africa",
        "pin": "9999",
        "role": "agent",
        "org_name": "Musica Events",
        "org_type": None,
    },
]

EVENTS_DATA = [
    {
        "title": "Nuit de Bangui — Concert de Gala",
        "description": "La plus grande nuit musicale de Centrafrique. Artistes locaux et invités internationaux se retrouvent pour une soirée inoubliable au cœur de Bangui.",
        "location": "Palais des Sports de Bangui",
        "category": "Concert",
        "days_from_now": 15,
        "price": 5000,
        "total_seats": 500,
        "seats_sold": 312,
        "cover_image_url": "https://images.unsplash.com/photo-1470229722913-7c0e2dbbafd3?w=800",
        "organizer_index": 0,
        "ticket_tiers": [
            {"name": "Standard", "price": 5000, "seats": 300},
            {"name": "VIP", "price": 15000, "seats": 150},
            {"name": "VVIP", "price": 30000, "seats": 50},
        ],
    },
    {
        "title": "Festival de la Jeunesse RCA",
        "description": "Trois jours de célébration de la culture centrafricaine : musique, danse, art et gastronomie. Un événement pour toute la famille.",
        "location": "Stade Barthélémy Boganda, Bangui",
        "category": "Festival",
        "days_from_now": 30,
        "price": 2000,
        "total_seats": 2000,
        "seats_sold": 850,
        "cover_image_url": "https://images.unsplash.com/photo-1504680177321-2e6a879aac86?w=800",
        "organizer_index": 1,
        "ticket_tiers": [
            {"name": "Journée", "price": 2000, "seats": 1500},
            {"name": "Pass 3 jours", "price": 5000, "seats": 500},
        ],
    },
    {
        "title": "Business Summit Centrafrique 2025",
        "description": "Rencontrez les entrepreneurs, investisseurs et décideurs qui façonnent l'économie centrafricaine. Networking, panels et opportunités d'affaires.",
        "location": "Hôtel Ledger Plaza, Bangui",
        "category": "Conférence",
        "days_from_now": 45,
        "price": 25000,
        "total_seats": 200,
        "seats_sold": 78,
        "cover_image_url": "https://images.unsplash.com/photo-1540575467063-178a50c2df87?w=800",
        "organizer_index": 2,
        "ticket_tiers": [
            {"name": "Standard", "price": 25000, "seats": 150},
            {"name": "VIP (table ronde)", "price": 75000, "seats": 50},
        ],
    },
    {
        "title": "Soirée Gospel — Louange et Adoration",
        "description": "Une soirée de partage spirituel et musical avec les meilleurs chorales de Bangui. Entrée libre pour les moins de 12 ans.",
        "location": "Cathédrale Notre-Dame de Bangui",
        "category": "Religion",
        "days_from_now": 7,
        "price": 1000,
        "total_seats": 800,
        "seats_sold": 445,
        "cover_image_url": "https://images.unsplash.com/photo-1507036066871-b7e8032b3dea?w=800",
        "organizer_index": 0,
        "ticket_tiers": [
            {"name": "Général", "price": 1000, "seats": 600},
            {"name": "Carré Or", "price": 3000, "seats": 200},
        ],
    },
    {
        "title": "Match de Gala — Étoiles RCA vs Invités",
        "description": "Un match de football exceptionnel opposant les anciennes gloires du football centrafricain à des invités surprise. Ambiance garantie !",
        "location": "Stade Municipal de Bangui",
        "category": "Sport",
        "days_from_now": 20,
        "price": 1500,
        "total_seats": 3000,
        "seats_sold": 1200,
        "cover_image_url": "https://images.unsplash.com/photo-1431324155629-1a6deb1dec8d?w=800",
        "organizer_index": 2,
        "ticket_tiers": [
            {"name": "Tribune Populaire", "price": 1500, "seats": 2000},
            {"name": "Tribune Officielle", "price": 5000, "seats": 800},
            {"name": "Loge VIP", "price": 20000, "seats": 200},
        ],
    },
    {
        "title": "Atelier Formation — Entrepreneuriat Féminin",
        "description": "Deux jours de formation intensive pour les femmes entrepreneures de RCA. Comptabilité, marketing digital, accès au financement.",
        "location": "Maison de la Femme, Bangui",
        "category": "Formation",
        "days_from_now": 10,
        "price": 0,
        "total_seats": 100,
        "seats_sold": 67,
        "cover_image_url": "https://images.unsplash.com/photo-1552664730-d307ca884978?w=800",
        "organizer_index": 1,
        "ticket_tiers": [
            {"name": "Gratuit", "price": 0, "seats": 100},
        ],
    },
    {
        "title": "Soirée DJ — AfroVibes Bangui",
        "description": "La nuit électronique la plus attendue de l'année. DJs internationaux + talents locaux pour une soirée 100% afro.",
        "location": "Espace Culturel de Bangui",
        "category": "Concert",
        "days_from_now": 5,
        "price": 3000,
        "total_seats": 400,
        "seats_sold": 380,
        "cover_image_url": "https://images.unsplash.com/photo-1571266028243-d220c6a7a1c9?w=800",
        "organizer_index": 0,
        "ticket_tiers": [
            {"name": "Entrée", "price": 3000, "seats": 300},
            {"name": "Table VIP (4 pers.)", "price": 50000, "seats": 100},
        ],
    },
    {
        "title": "Exposition d'Art Contemporain RCA",
        "description": "Découvrez les œuvres de 20 artistes centrafricains émergents. Peintures, sculptures et installations autour du thème 'Renaissance'.",
        "location": "Institut Français de Centrafrique, Bangui",
        "category": "Culture",
        "days_from_now": 3,
        "price": 500,
        "total_seats": 300,
        "seats_sold": 120,
        "cover_image_url": "https://images.unsplash.com/photo-1594736797933-d0501ba2fe65?w=800",
        "organizer_index": 2,
        "ticket_tiers": [
            {"name": "Entrée", "price": 500, "seats": 300},
        ],
    },
]

PARTNERS_DATA = [
    {
        "company_name": "Orange Centrafrique",
        "email": "partenariat@orange.cf",
        "phone": "+23675000001",
        "partnership_type": "Sponsor Principal",
        "message": "Orange Centrafrique souhaite devenir partenaire principal de la plateforme GoEvent pour promouvoir Orange Money.",
        "status": "traite",
    },
    {
        "company_name": "Brasserie Mocaf",
        "email": "contact@mocaf.cf",
        "phone": "+23675000002",
        "partnership_type": "Sponsor Événement",
        "message": "Nous souhaitons sponsoriser les grands concerts sur la plateforme.",
        "status": "nouveau",
    },
    {
        "company_name": "Ministère de la Culture RCA",
        "email": "culture@gouvernement.cf",
        "phone": "+23675000003",
        "partnership_type": "Partenaire Institutionnel",
        "message": "Le Ministère de la Culture souhaite utiliser GoEvent pour les événements officiels.",
        "status": "nouveau",
    },
]


def seed():
    db = SessionLocal()
    try:
        print("Démarrage du seed GoEvent...")

        # ── 1. Utilisateurs ───────────────────────────────────────
        users = []
        for u in USERS_DATA:
            existing = db.query(User).filter(User.phone_number == u["phone_number"]).first()
            if existing:
                print(f"  [SKIP] Utilisateur {u['phone_number']} déjà présent")
                users.append(existing)
                continue
            user = User(
                phone_number    = u["phone_number"],
                full_name       = u["full_name"],
                email           = u.get("email"),
                pin_hash        = hash_pin(u["pin"]),
                role            = u["role"],
                org_name        = u.get("org_name"),
                org_type        = u.get("org_type"),
                orange_money    = u.get("orange_money"),
                is_active       = True,
            )
            db.add(user)
            db.flush()
            users.append(user)
            print(f"  [OK] Utilisateur créé : {u['full_name']} ({u['role']}) — PIN: {u['pin']}")

        db.commit()

        # ── 2. Événements ─────────────────────────────────────────
        events = []
        for e in EVENTS_DATA:
            existing = db.query(Event).filter(Event.title == e["title"]).first()
            if existing:
                print(f"  [SKIP] Événement '{e['title']}' déjà présent")
                events.append(existing)
                continue
            organizer = users[e["organizer_index"]]
            event = Event(
                title           = e["title"],
                description     = e["description"],
                location        = e["location"],
                category        = e["category"],
                event_date      = datetime.utcnow() + timedelta(days=e["days_from_now"]),
                price           = e["price"],
                total_seats     = e["total_seats"],
                seats_sold      = e["seats_sold"],
                cover_image_url = e["cover_image_url"],
                ticket_tiers    = e["ticket_tiers"],
                organizer_id    = organizer.id,
                is_active       = True,
            )
            db.add(event)
            db.flush()
            events.append(event)
            print(f"  [OK] Événement créé : {e['title']}")

        db.commit()

        # ── 3. Billets + Paiements ────────────────────────────────
        fans = [u for u in users if u.role == "fan"]
        tickets_created = 0
        for event in events:
            if event.seats_sold == 0:
                continue
            nb = min(event.seats_sold, len(fans) * 2)
            for i in range(nb):
                fan = fans[i % len(fans)]
                existing = db.query(Ticket).filter(
                    Ticket.user_id == fan.id,
                    Ticket.event_id == event.id
                ).first()
                if existing:
                    continue
                ticket = Ticket(
                    qr_hash        = str(uuid.uuid4()),
                    payment_status = "paye",
                    payment_ref    = f"REF-{random.randint(100000, 999999)}",
                    is_used        = random.choice([True, False]),
                    user_id        = fan.id,
                    event_id       = event.id,
                )
                db.add(ticket)
                db.flush()

                prix_base         = event.price if event.price > 0 else 0
                frais_acheteur    = prix_base * 0.04
                total_paye        = prix_base + frais_acheteur
                frais_organisateur = prix_base * 0.07
                solde_orga        = prix_base - frais_organisateur
                benefices         = frais_acheteur + frais_organisateur

                payment = Payment(
                    user_id          = fan.id,
                    ticket_id        = ticket.id,
                    amount           = total_paye,
                    base_price       = prix_base,
                    platform_fee     = benefices,
                    organizer_amount = solde_orga,
                    status           = "completed",
                    transaction_id   = f"TXN-{uuid.uuid4().hex[:12].upper()}",
                )
                db.add(payment)
                tickets_created += 1

        db.commit()
        print(f"  [OK] {tickets_created} billets + paiements créés")

        # ── 4. Favoris ────────────────────────────────────────────
        favs_created = 0
        for fan in fans:
            sample = random.sample(events, min(3, len(events)))
            for event in sample:
                existing = db.query(Favorite).filter(
                    Favorite.user_id == fan.id,
                    Favorite.event_id == event.id
                ).first()
                if existing:
                    continue
                db.add(Favorite(user_id=fan.id, event_id=event.id))
                favs_created += 1
        db.commit()
        print(f"  [OK] {favs_created} favoris créés")

        # ── 5. Partenaires ────────────────────────────────────────
        for p in PARTNERS_DATA:
            existing = db.query(PartnerRequest).filter(
                PartnerRequest.email == p["email"]
            ).first()
            if existing:
                continue
            db.add(PartnerRequest(**p))
        db.commit()
        print(f"  [OK] {len(PARTNERS_DATA)} demandes partenaires créées")

        # ── Résumé final ──────────────────────────────────────────
        print("\n" + "="*50)
        print("SEED TERMINÉ AVEC SUCCÈS")
        print("="*50)
        print(f"  Utilisateurs : {len(users)}")
        print(f"  Événements   : {len(events)}")
        print(f"  Billets      : {tickets_created}")
        print(f"  Favoris      : {favs_created}")
        print()
        print("COMPTES DE TEST :")
        print("  Organisateur  : +23670000001  PIN: 1234")
        print("  Fan           : +23672000001  PIN: 0001")
        print("  Agent         : +23673000001  PIN: 9999")
        print("="*50)

    except Exception as e:
        db.rollback()
        print(f"ERREUR : {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
