# Welfare aziendale

Applicazione web per la gestione del welfare aziendale: l'amministrazione assegna a
ogni dipendente un budget e una quantità di voucher delle convenzioni attive; il
dipendente consulta la propria situazione, richiede i voucher che gli sono stati
assegnati (allegando eventuale documentazione) e ne segue lo stato fino alla consegna.

Il **calcolo del budget è fuori dalla piattaforma**: l'applicazione riceve l'importo
deciso dall'amministratore welfare.

## Indice

- [Stack](#stack)
- [Requisiti](#requisiti)
- [Setup locale](#setup-locale)
- [Variabili d'ambiente](#variabili-dambiente)
- [PostgreSQL in locale](#postgresql-in-locale)
- [Migrazioni](#migrazioni)
- [Superuser](#superuser)
- [Dati demo](#dati-demo)
- [Esecuzione dei test](#esecuzione-dei-test)
- [Cloudflare R2](#cloudflare-r2-allegati-su-bucket-privato)
- [Deploy su Railway](#deploy-su-railway)
- [Architettura](#architettura)
- [Gestione utenti dal frontend](#gestione-utenti-dal-frontend)
- [Notifiche](#notifiche)
- [Modello dei dati e regole di dominio](#modello-dei-dati-e-regole-di-dominio)
- [Ruoli e permessi](#ruoli-e-permessi)

## Stack

Python 3.11 · Django 5.2 LTS · PostgreSQL · Django Templates · Bootstrap 5 ·
HTMX (solo dove serve) · Gunicorn · WhiteNoise · Cloudflare R2 (API S3-compatible)
· Railway.

Nessuna SPA, nessun frontend React/Vue, nessuna API REST separata: view Django
server-rendered, form Django e progressive enhancement con HTMX.

## Requisiti

- Python 3.11+
- PostgreSQL 14+ (testato su 16)
- `pip` / `venv`

## Setup locale

```bash
git clone <repo> welfare_app && cd welfare_app

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # poi valorizza SECRET_KEY, DATABASE_URL, ...
```

Per lo sviluppo, nel file `.env`:

```
DEBUG=True
SECRET_KEY=una-chiave-qualsiasi-per-lo-sviluppo
ALLOWED_HOSTS=localhost,127.0.0.1
DATABASE_URL=postgres://welfare:welfare@localhost:5432/welfare
```

Avvio:

```bash
python manage.py migrate
python manage.py seed_demo        # opzionale: dati dimostrativi
python manage.py runserver
```

L'applicazione risponde su <http://127.0.0.1:8000/>.

## Variabili d'ambiente

Tutta la configurazione sensibile passa da variabili d'ambiente; in locale vengono
lette anche dal file `.env` (non versionato). Riferimento completo in `.env.example`.

| Variabile | Obbligatoria | Descrizione |
|---|---|---|
| `SECRET_KEY` | sì (in produzione) | Chiave segreta Django. Con `DEBUG=True` viene usato un valore di sviluppo. |
| `DEBUG` | no (default `False`) | Modalità debug. |
| `ALLOWED_HOSTS` | sì (in produzione) | Host ammessi, separati da virgola. Su Railway viene aggiunto automaticamente `RAILWAY_PUBLIC_DOMAIN`. |
| `DATABASE_URL` | sì | Connessione PostgreSQL (`postgres://utente:password@host:5432/db`). |
| `R2_ACCESS_KEY_ID` | in produzione | Access key del token R2. |
| `R2_SECRET_ACCESS_KEY` | in produzione | Secret key del token R2. |
| `R2_BUCKET_NAME` | in produzione | Nome del bucket **privato**. |
| `R2_ENDPOINT_URL` | in produzione | `https://<account_id>.r2.cloudflarestorage.com`. |
| `CSRF_TRUSTED_ORIGINS` | no | Origini aggiuntive per il CSRF (dedotte da `ALLOWED_HOSTS` se assenti). |
| `DB_SSL_REQUIRE` | no | Forza SSL verso il database. |
| `R2_URL_EXPIRE_SECONDS` | no (300) | Scadenza delle URL firmate generate internamente. |
| `ATTACHMENT_MAX_SIZE_MB` | no (10) | Dimensione massima di un allegato. |
| `WELFARE_NOTIFICATION_EMAIL` | no | Unico destinatario delle notifiche (default `agevolazioni@studiobirardi.it`). |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | per le notifiche | Server SMTP. Senza `EMAIL_HOST` le notifiche restano inerti. |
| `EMAIL_USE_TLS` / `EMAIL_USE_SSL` | no (TLS attivo) | Cifratura della connessione SMTP. |
| `DEFAULT_FROM_EMAIL` | no | Mittente delle notifiche. |
| `SITE_BASE_URL` | no | URL pubblica per i link nelle email (dedotta da Railway se assente). |
| `ATTACHMENT_ALLOWED_EXTENSIONS` | no | Estensioni ammesse per gli allegati. |
| `SECURE_SSL_REDIRECT` | no (`True` in produzione) | Redirect HTTPS (l'endpoint `/health/` è esentato). |
| `WEB_CONCURRENCY` | no (3) | Worker Gunicorn. |
| `TIME_ZONE` | no (`Europe/Rome`) | Fuso orario. |

**Nessun segreto va committato nel repository.**

## PostgreSQL in locale

Con Docker:

```bash
docker run --name welfare-postgres \
  -e POSTGRES_USER=welfare -e POSTGRES_PASSWORD=welfare -e POSTGRES_DB=welfare \
  -p 5432:5432 -d postgres:16
```

Con PostgreSQL installato sulla macchina:

```bash
sudo -u postgres psql -c "CREATE USER welfare WITH PASSWORD 'welfare';"
sudo -u postgres psql -c "CREATE DATABASE welfare OWNER welfare;"
```

In entrambi i casi: `DATABASE_URL=postgres://welfare:welfare@localhost:5432/welfare`.

## Migrazioni

```bash
python manage.py makemigrations   # solo se modifichi i modelli
python manage.py migrate
```

La migrazione crea anche, tramite signal `post_migrate`, il gruppo
**Welfare Managers** con il permesso `welfare.manage_welfare`.

## Superuser

```bash
python manage.py createsuperuser
```

Il superuser accede al Django Admin (`/django-admin/`) e, avendo tutti i permessi,
anche all'area Amministrazione welfare. Per usarlo anche come dipendente creagli un
`EmployeeProfile` dal Django Admin.

Dove non c'è un terminale interattivo (per esempio su Railway) esiste l'equivalente
non interattivo, che legge le variabili d'ambiente e non fa nulla se l'utente esiste
già o se le variabili mancano:

```bash
DJANGO_SUPERUSER_USERNAME=admin \
DJANGO_SUPERUSER_EMAIL=admin@azienda.it \
DJANGO_SUPERUSER_PASSWORD='...' \
python manage.py ensure_superuser
```

## Dati demo

```bash
python manage.py seed_demo                 # password predefinita: welfare2026
python manage.py seed_demo --password ...  # password personalizzata
python manage.py seed_demo --if-requested  # esegue solo se SEED_DEMO è attiva
```

La password può arrivare anche dalla variabile `SEED_DEMO_PASSWORD`. L'opzione
`--if-requested` serve in produzione: il comando è nel comando di avvio e resta
inerte finché non imposti `SEED_DEMO=true` fra le variabili d'ambiente.

Crea (in modo idempotente):

- **Antonia** — dipendente **e** Welfare Manager (budget €2.000, 2 abbonamenti OroDance)
- **Giuseppe** — dipendente (budget €5.000)
- Programma **Piano Welfare**
- Convenzioni: Muraglia Srlrs (Buono spesa €100 e €50), OroDance (Abbonamento annuale
  €440), Associazione Yoga (Singola lezione €50)
- Allocazioni di Giuseppe: 10 × €100 + 3 × €440 + 27 × €50 = **€3.670 allocati**,
  **€1.330 non ancora allocati**

Il Buono spesa Muraglia da €50 esiste a catalogo senza essere assegnato a Giuseppe:
è visibile nel catalogo convenzioni ma non richiedibile.

## Esecuzione dei test

```bash
python manage.py test
```

I test usano un database PostgreSQL temporaneo (l'utente di `DATABASE_URL` deve poter
creare database). Coprono, tra l'altro: isolamento dei dati tra dipendenti, accesso
all'area amministrativa, doppio ruolo dipendente/manager, tetto di budget, riduzione
del budget, disponibilità dei voucher nei vari stati, consegne (da richiesta e
dirette), immutabilità del valore unitario, protezione degli allegati e correttezza
delle quantità/importi derivati.

## Cloudflare R2 (allegati su bucket privato)

1. Dashboard Cloudflare → **R2** → *Create bucket* (es. `welfare-allegati`).
   **Non** abilitare l'accesso pubblico né un dominio pubblico: il bucket resta privato.
2. **Manage R2 API Tokens** → crea un token con permesso *Object Read & Write*
   limitato a quel bucket. Annota Access Key ID e Secret Access Key.
3. Valorizza le variabili:

   ```
   R2_ACCESS_KEY_ID=...
   R2_SECRET_ACCESS_KEY=...
   R2_BUCKET_NAME=welfare-allegati
   R2_ENDPOINT_URL=https://<account_id>.r2.cloudflarestorage.com
   ```

Quando le quattro variabili sono presenti, gli allegati vengono salvati su R2 tramite
API S3-compatible (`django-storages` + `boto3`); altrimenti — solo per lo sviluppo —
finiscono in `media/`.

**Come vengono protetti gli allegati**

- Il bucket è privato e i file hanno un nome casuale (`attachments/<id>/<uuid>.<ext>`):
  non esistono URL pubbliche né indovinabili.
- Il download passa **sempre** dalla view `/allegati/<id>/download/`, che verifica il
  login e il permesso (proprietario della richiesta oppure Welfare Manager) e poi
  restituisce il contenuto in streaming. Il link diretto all'oggetto R2 non viene mai
  esposto al browser.

## Deploy su Railway

1. **Nuovo progetto** → *Deploy from GitHub repo* e collega questo repository.
2. Aggiungi un servizio **PostgreSQL** (`+ New` → *Database* → *PostgreSQL*).
3. Nel servizio applicativo imposta le variabili:

   ```
   DATABASE_URL=${{Postgres.DATABASE_URL}}
   SECRET_KEY=<chiave generata>
   DEBUG=False
   ALLOWED_HOSTS=<dominio-del-servizio>.up.railway.app
   R2_ACCESS_KEY_ID=...
   R2_SECRET_ACCESS_KEY=...
   R2_BUCKET_NAME=...
   R2_ENDPOINT_URL=https://<account_id>.r2.cloudflarestorage.com
   ```

   Genera la chiave con:
   `python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"`

4. Il file `railway.json` configura già tutto:
   - build: `pip install -r requirements.txt && python manage.py collectstatic --noinput`
   - start: `migrate` → `ensure_superuser` → `seed_demo --if-requested` → Gunicorn
   - health check su `/health/`

   (È presente anche un `Procfile` equivalente per altre piattaforme.)

   **Le migrazioni girano all'avvio, non durante la build.** Su Railway il database
   è raggiungibile solo attraverso la rete privata, che *non esiste in fase di build*:
   un `migrate` eseguito lì fallisce con
   `failed to resolve host 'postgres.railway.internal'`. La build si limita quindi a
   installare le dipendenze e a fare `collectstatic`, operazioni che non toccano il
   database.

   Non impostare comandi personalizzati nella UI di Railway: i campi *Build Command*,
   *Pre-Deploy Command* e *Custom Start Command* vanno lasciati vuoti, altrimenti
   hanno la precedenza su `railway.json`.

5. **Crea le credenziali iniziali.** Railway non offre un terminale interattivo sul
   container, quindi il primo utente si crea da variabili d'ambiente: all'avvio il
   servizio esegue `ensure_superuser` e, se richiesto, `seed_demo`.

   Aggiungi temporaneamente queste variabili e fai ripartire il servizio:

   ```
   DJANGO_SUPERUSER_USERNAME=admin
   DJANGO_SUPERUSER_EMAIL=admin@azienda.it
   DJANGO_SUPERUSER_PASSWORD=<password robusta>
   ```

   Se vuoi anche utenti e dati applicativi con cui provare subito l'applicazione:

   ```
   SEED_DEMO=true
   SEED_DEMO_PASSWORD=<password per gli utenti demo>
   ```

   Ottieni così tre utenti:

   | Utente | Ruolo | Cosa vede |
   |---|---|---|
   | `admin` | superuser, senza profilo dipendente | Django Admin e area Amministrazione welfare |
   | `antonia` | Welfare Manager **e** dipendente | Area dipendente **e** Amministrazione welfare |
   | `giuseppe` | dipendente | Solo la propria area dipendente |

   Entrambi i comandi sono **idempotenti**: a ogni riavvio non duplicano nulla e non
   sovrascrivono la password di un utente che esiste già.

   **Dopo il primo accesso rimuovi `DJANGO_SUPERUSER_PASSWORD` e `SEED_DEMO_PASSWORD`
   dalle variabili**, e imposta `SEED_DEMO=false` per non ricreare i dati demo.
   Cambia la password dell'admin dall'applicazione (voce *Password* nella barra in alto).

   In alternativa, se usi la [CLI di Railway](https://docs.railway.com/guides/cli):

   ```bash
   railway link
   railway ssh python manage.py createsuperuser
   ```

L'endpoint `/health/` risponde `200 {"status": "ok"}` se l'app e il database sono
raggiungibili, `503` altrimenti; è escluso dal redirect HTTPS perché Railway lo
interroga in HTTP interno.

### Se il deploy fallisce

| Sintomo | Causa | Rimedio |
|---|---|---|
| Build fallita con `failed to resolve host 'postgres.railway.internal'` | Un comando che usa il database (tipicamente `migrate`) sta girando in fase di build, dove la rete privata non esiste | Svuota il campo *Build Command* nella UI (Settings → Build): deve valere quello di `railway.json`, che fa solo `pip install` e `collectstatic` |
| `DisallowedHost` / errore 400 | Il dominio non è in `ALLOWED_HOSTS` | Genera il dominio (Settings → Networking) e inseriscilo in `ALLOWED_HOSTS` |
| `ImproperlyConfigured: SECRET_KEY` | Variabile mancante | Imposta `SECRET_KEY` fra le variabili del servizio |
| Health check in timeout | L'app non parte: guarda i *Deploy Logs* | Spesso è `DATABASE_URL` assente o errata: deve essere `${{Postgres.DATABASE_URL}}` |
| Allegati che spariscono dopo un redeploy | Variabili `R2_*` non impostate: i file finiscono sul filesystem effimero del container | Configura il bucket Cloudflare R2 |

## Architettura

```
config/            impostazioni, URL, WSGI
welfare/
  models.py        modello di dominio + contatori quantità
  services.py      business logic (transazioni, lock, regole di budget)
  forms.py         form e validazione server-side
  views.py         view server-rendered (area dipendente + amministrazione)
  permissions.py   controlli di autorizzazione e decoratori
  admin.py         Django Admin per manutenzione
  storage.py       storage dei file statici in produzione
  templatetags/    filtri di presentazione (importi in formato italiano)
  management/commands/seed_demo.py
  tests/           test suite
templates/         Django Templates (Bootstrap 5)
static/            Bootstrap, HTMX e CSS applicativo (serviti da WhiteNoise)
```

La business logic vive in `welfare/services.py` e nei metodi dei modelli: le view
non la duplicano. Ogni operazione che cambia disponibilità, allocazioni, richieste o
consegne gira in `transaction.atomic` con `select_for_update` sulla riga
dell'allocazione (e del budget), così da impedire doppi consumi in concorrenza.

HTMX è usato in tre punti in cui porta un vantaggio concreto — calcolo lato server,
con `Decimal`, di valori che l'utente deve vedere prima di inviare il form:

- form di richiesta voucher → valore complessivo della richiesta;
- form di assegnazione voucher → valore dell'assegnazione e budget ancora allocabile;
- form di consegna diretta → disponibilità e valore della consegna.

In tutti i casi la validazione definitiva resta server-side, nel form e nel service.

## Modello dei dati e regole di dominio

| Modello | Ruolo |
|---|---|
| `EmployeeProfile` | Profilo welfare del dipendente (1-a-1 con `User`). Nessun dato usato per il calcolo del budget. |
| `WelfareProgram` | Iniziativa welfare una tantum (nessun rinnovo automatico, nessun riporto di credito). |
| `EmployeeBudget` | Budget del dipendente per un programma (uno solo per coppia dipendente+programma). |
| `Convention` | Soggetto convenzionato; disattivabile senza perdere lo storico. |
| `VoucherType` | Tipo di voucher di una convenzione con il suo valore unitario. |
| `VoucherAllocation` | Quantità di un tipo voucher assegnata a un dipendente (una per dipendente+programma+tipo). |
| `VoucherRequest` | Richiesta del dipendente: `PENDING` / `APPROVED` / `REJECTED`. |
| `RequestAttachment` | Documento allegato a una richiesta (opzionale, su R2). |
| `VoucherDelivery` | Consegna registrata: collegata a una richiesta approvata **oppure** diretta (`request = NULL`). |

Regole applicate lato server:

- il valore complessivo delle allocazioni di un dipendente non può superare il suo
  budget (il budget si impegna **al momento dell'allocazione**, non della richiesta);
- il budget non può essere negativo né sceso sotto il valore già allocato;
- una quantità assegnata non può scendere sotto `in attesa + da consegnare + consegnati`;
- il valore unitario di un `VoucherType` già allocato non è modificabile (descrizione
  e stato `active` sì); per un taglio diverso si crea un nuovo tipo voucher;
- `Convention` e `VoucherType` non vengono eliminati fisicamente: si disattivano;
- una richiesta `PENDING` riserva subito le quantità, una `REJECTED` le libera, una
  `APPROVED` continua a riservarle fino alla consegna;
- una richiesta approvata genera **al massimo una** consegna, sempre totale;
- una consegna diretta può usare solo voucher realmente disponibili;
- il dipendente non può modificare o eliminare una richiesta già inviata;
- tutti gli importi sono `Decimal` (mai `float`).

Contatori per allocazione:

```
available = assigned - pending - approved_waiting_delivery - delivered
```

## Gestione utenti dal frontend

Gli utenti con **privilegi di staff** (`is_staff`) trovano in Amministrazione welfare la
sezione **Utenti**, che evita di dover passare dal Django Admin:

- elenco con ricerca per nome, cognome, email o nome utente;
- creazione di un account con password, ruoli e — in un solo passaggio — il profilo
  dipendente con la relativa matricola;
- modifica dei dati e dei ruoli di un account esistente;
- impostazione di una nuova password;
- disattivazione e riattivazione dell'account.

Le tre spunte che definiscono cosa vede l'utente sono indipendenti e cumulabili:

| Opzione | Effetto |
|---|---|
| Profilo dipendente attivo | Accesso alla propria area welfare; necessario per ricevere voucher |
| Welfare Manager | Aggiunge l'area Amministrazione welfare (gruppo `Welfare Managers`) |
| Privilegi di staff | Permette di gestire gli utenti da questa sezione |
| Superuser | Accesso completo, incluso il Django Admin. Assegnabile solo da un altro superuser |

Regole di sicurezza applicate lato server:

- la sezione è raggiungibile **solo** dagli utenti staff: un Welfare Manager che non sia
  staff riceve 403 anche digitando la URL a mano;
- un utente staff che non è superuser **non può modificare l'account di un superuser**,
  nemmeno la password: potrebbe altrimenti prenderne il posto;
- solo un superuser può assegnare o revocare i privilegi di superuser;
- nessuno può disattivare il proprio account o togliersi da solo i privilegi di staff:
  eviterebbe di restare chiuso fuori;
- gli account **non si eliminano**: si disattivano, così budget, richieste e consegne
  restano nello storico. Disattivare un account disattiva anche il suo profilo dipendente.

## Notifiche

Le notifiche amministrative hanno **un solo destinatario**, configurato in
`WELFARE_NOTIFICATION_EMAIL` (default: `agevolazioni@studiobirardi.it`).
**Ai dipendenti non viene inviata alcuna email.**

Tre livelli, indipendenti fra loro:

1. **Badge in applicazione** — accanto alla voce «Amministrazione welfare» compare il
   numero di richieste in attesa, visibile da qualunque pagina ai Welfare Manager.
   Non richiede alcuna configurazione.
2. **Email alla nuova richiesta** — appena un dipendente invia una richiesta parte un
   messaggio con dipendente, convenzione, voucher, quantità, valore, presenza di
   allegati e link diretto alla richiesta. È l'unico evento notificato: approvazioni,
   rifiuti e consegne li compie l'amministrazione stessa, avvisarla sarebbe rumore.
3. **Riepilogo giornaliero** — elenco delle richieste da approvare e di quelle approvate
   da consegnare. Se non c'è nulla in sospeso non viene inviato niente.

### Configurazione SMTP

Finché `EMAIL_HOST` è vuoto le notifiche restano **inerti**: l'applicazione funziona
normalmente e nei log compare un avviso. In sviluppo (`DEBUG=True`) le email vengono
stampate a console.

```
EMAIL_HOST=smtp.provider.it
EMAIL_PORT=587
EMAIL_HOST_USER=...
EMAIL_HOST_PASSWORD=...
EMAIL_USE_TLS=True
DEFAULT_FROM_EMAIL=welfare@studiobirardi.it
WELFARE_NOTIFICATION_EMAIL=agevolazioni@studiobirardi.it
```

Un invio che fallisce non fa mai fallire l'operazione dell'utente: la richiesta viene
comunque registrata e l'errore finisce nei log. Le email partono a transazione conclusa
(`transaction.on_commit`), quindi non viene mai notificata una richiesta non salvata.

### Riepilogo giornaliero: come schedularlo

```bash
python manage.py send_pending_digest           # non invia nulla se non c'è niente in sospeso
python manage.py send_pending_digest --force   # invia comunque
```

Su Railway: `+ New` → *GitHub Repo* (lo stesso repository) → nel nuovo servizio
**Settings → Cron Schedule** inserisci `0 7 * * 1-5` (feriali alle 7:00 UTC) e come
**Custom Start Command** `python manage.py send_pending_digest`. Assegna al servizio
le stesse variabili d'ambiente dell'applicazione (`DATABASE_URL`, `SECRET_KEY`,
`EMAIL_*`, `WELFARE_NOTIFICATION_EMAIL`, `SITE_BASE_URL`).

## Ruoli e permessi

Non esiste alcun campo `user_type`: si usano gruppi e permessi Django, perché un
Welfare Manager può essere contemporaneamente un dipendente (è il caso di Antonia).

- Gruppo **Welfare Managers** → permesso `welfare.manage_welfare` → accesso all'area
  Amministrazione welfare **in aggiunta** alla propria area dipendente.
- Flag `is_staff` → in più, gestione degli utenti dal frontend (sezione *Utenti*).
  È indipendente dal ruolo welfare: un Welfare Manager non è automaticamente staff.
- Un Welfare Manager può amministrare anche la propria posizione: ogni operazione
  registra comunque attore e timestamp (budget, allocazioni, approvazioni, rifiuti,
  consegne, consegne dirette).
- Tutte le autorizzazioni sono verificate server-side: modificando l'URL un dipendente
  non raggiunge budget, allocazioni, richieste, allegati o consegne altrui (403/404).
- Tutte le azioni di modifica sono POST protette da CSRF.

## Fuori scope

Non sono implementati (per scelta, come da specifiche): calcolo automatico del budget,
composizione familiare, rimborsi spese, pagamenti, integrazione con esercenti,
generazione di codici voucher o QR code, marketplace libero, notifiche/email/SMS,
rinnovo annuale e riporto del credito.
