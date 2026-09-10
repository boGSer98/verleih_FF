# Verleih FF

Web-App für den Verleihprozess von Festausstattung eines Fördervereins.

## Ziel

Die Anwendung bildet den vollständigen Verleihvorgang ab:

1. Reservierung
2. Abholung vorbereiten
3. Übergabeprotokoll mit digitaler Unterschrift
4. Empfang Spende/Zahlung
5. Rücknahmeprotokoll mit digitaler Unterschrift
6. Vorgangsabschluss

Die Plattform soll auf einer Synology NAS per Docker betrieben werden und eine PostgreSQL-Datenbank für Produkte, Entleiher, Vorgänge, Protokolle und Dokumente nutzen.

## Technischer Stack

- Python / Django
- PostgreSQL
- Docker Compose
- serverseitige PDF-Erzeugung vorbereitet
- SMTP-Mailversand vorbereitet
- Dateiablage für PDFs, Signaturen und spätere Fotos über `media/`

## Lokaler Start

```bash
cp .env.example .env
docker compose up --build
```

Danach:

```bash
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
```

Aufruf lokal:

```text
http://localhost:8100/
http://localhost:8100/admin/
```

Docker veröffentlicht den Host-Port `8100` auf den internen Container-Port `8000`.

Die Startseite ist ein login-geschütztes, mobile-optimiertes Verleih-Dashboard für Tagesaufgaben.

## Zugriff über Nginx Proxy Manager

Für den lokalen AHD-Betrieb ist die Compose-Konfiguration auf den Host-Port `8100` und die Domain vorbereitet:

```text
http://192.168.178.120:8100/
https://verleih.it-service-ahd.de/
```

In Portainer müssen dafür mindestens diese Environment-Variablen gesetzt sein:

```env
ALLOWED_HOSTS=localhost,127.0.0.1,192.168.178.120,verleih.it-service-ahd.de
CSRF_TRUSTED_ORIGINS=https://verleih.it-service-ahd.de
USE_X_FORWARDED_PROTO=1
SESSION_COOKIE_SECURE=1
CSRF_COOKIE_SECURE=1
```

Im Nginx Proxy Manager zeigt der Proxy-Host per `http` auf `192.168.178.120` Port `8100`. Ein `400 Bad Request` beim Domainaufruf bedeutet in der Regel, dass `verleih.it-service-ahd.de` noch nicht in `ALLOWED_HOSTS` der laufenden Django-Umgebung angekommen ist.

## Synology-Zielbetrieb

Empfohlenes Zielverzeichnis auf der NAS:

```text
/volume1/docker/verleih-ff/
```

Persistente Daten:

```text
/volume1/docker/verleih-ff/postgres/
/volume1/docker/verleih-ff/media/
/volume1/docker/verleih-ff/staticfiles/
```

## Status

MVP-Grundprozess mit Datenmodell, Adminbereich, mobilem Dashboard, PDF-Dokumenten, Mailversand, Signaturerfassung und dokumentierter Spendenentscheidung.

Die vollständige Umsetzungsplanung liegt unter [`docs/IMPLEMENTATION_STEPS.md`](docs/IMPLEMENTATION_STEPS.md).

Die Portainer-Installationsanleitung liegt unter [`docs/PORTAINER_INSTALLATION.md`](docs/PORTAINER_INSTALLATION.md).
