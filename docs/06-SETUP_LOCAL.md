# Setup local

## Prérequis

- Python 3.11 (version de prod ; 3.12/3.13 fonctionnent mais
  `psycopg2-binary==2.9.9` n'a pas de wheel pour 3.13 → installer
  `psycopg2-binary>=2.9.10` dans ce cas).
- PostgreSQL 14+ en local (ou une branche Neon de dev, **jamais** la base
  de prod).

## Backend

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # puis renseigner au minimum DATABASE_URL et SECRET_KEY
createdb goevent
uvicorn main:app --reload --port 8000
```

Au premier démarrage, `Base.metadata.create_all()` crée les tables puis
`core/migrations.py` joue les migrations (`[Migration] Terminé` dans la
console). Les tokens NGSER peuvent rester factices tant qu'on ne teste pas
le paiement ; `ENV=development` autorise le CORS `localhost:5500`.

Documentation interactive : http://localhost:8000/docs

## Frontend

```bash
cd web && python -m http.server 5500
```

⚠️ `web/bt-api.js` pointe en dur sur `https://api.goevent.africa`
(`BT_API`). Pour travailler contre l'API locale, remplacer
temporairement par `http://localhost:8000` — et ne pas le commiter.

## Données de démo

`seed.py` est **obsolète** (copie des modèles d'avant 2026, PIN à 4
chiffres refusés par le login actuel). Ne pas l'utiliser ; créer les
comptes via `POST /auth/register` (PIN 6 chiffres, téléphone
`+236XXXXXXXX`). Un admin se crée en passant `role='admin'` en base sur
un compte existant :

```sql
UPDATE users SET role = 'admin' WHERE phone_number = '+236XXXXXXXX';
```

## Tester le paiement

Uniquement avec les identifiants **sandbox** NGSER
(`NGSER_BASE_URL=https://securetest.crossroad-africa.net`). Le webhook
NGSER ne peut pas joindre `localhost` : simuler la notification avec

```bash
curl -X POST http://localhost:8000/payment/ngser-webhook \
     -H 'Content-Type: application/json' -d '{"order_id": "EVT1-XXXXXXXXXX"}'
```

Le backend ignore le contenu et réinterroge NGSER
(`check_payment_status`) : seul `order_id` compte.
