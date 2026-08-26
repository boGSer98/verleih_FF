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
http://localhost:8000/admin/
```

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

Initiales Grundgerüst mit Datenmodell, Adminbereich, Docker Compose und Dokumentation.

Die vollständige Umsetzungsplanung liegt unter [`docs/IMPLEMENTATION_STEPS.md`](docs/IMPLEMENTATION_STEPS.md).
