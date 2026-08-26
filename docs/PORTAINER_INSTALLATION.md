# Portainer-Installationsanleitung Verleih FF

Diese Anleitung beschreibt die Installation der Verleih-FF-Web-App als Portainer-Stack mit Django und PostgreSQL.

> Stand der Anwendung: MVP in Entwicklung. Reservierung, Stammdaten, Verfügbarkeit und mobiles Prozess-Dashboard sind vorbereitet. PDF-Erzeugung, Mailversand und mobile Signatur folgen in späteren Phasen.

## 1. Voraussetzungen

- Portainer ist eingerichtet und kann Docker-Stacks/Compose starten.
- Der Portainer-Host hat Internetzugriff zu GitHub und Docker Hub.
- Ein freier HTTP-Port ist verfügbar, z. B. `8000`.
- Für den späteren Produktivbetrieb sollte ein Reverse Proxy mit HTTPS vorhanden sein.

## 2. Repository

GitHub-Repository:

```text
https://github.com/boGSer98/verleih_FF.git
```

Empfohlener Branch für stabile Installation:

```text
main
```

Wenn ein noch nicht gemergter Entwicklungsstand getestet werden soll, kann stattdessen ein Feature-Branch ausgewählt werden.

## 3. Stack in Portainer anlegen

1. Portainer öffnen.
2. Ziel-Environment auswählen.
3. **Stacks** öffnen.
4. **Add stack** anklicken.
5. Stack-Name eintragen, z. B.:

```text
verleih-ff
```

6. Als Build-Methode je nach Portainer-Version wählen:
   - bevorzugt: **Repository** / **Git repository**
   - alternativ: Repository auf den Host klonen und als lokales Compose-Projekt verwenden

7. Repository-Daten eintragen:

```text
Repository URL: https://github.com/boGSer98/verleih_FF.git
Repository reference: refs/heads/main
Compose path: docker-compose.yml
```

## 4. Environment-Variablen setzen

In Portainer unter **Environment variables** folgende Werte setzen.

Wichtig: Passwörter und `SECRET_KEY` individuell ersetzen, nicht die Beispielwerte produktiv verwenden.

```env
DEBUG=0
SECRET_KEY=<langen-zufaelligen-django-secret-key-eintragen>
ALLOWED_HOSTS=localhost,127.0.0.1,<hostname-oder-ip-des-portainer-hosts>
POSTGRES_DB=verleih_ff
POSTGRES_USER=verleih_ff
POSTGRES_PASSWORD=<datenbankpasswort>
TIME_ZONE=Europe/Berlin

EMAIL_HOST=smtp.example.org
EMAIL_PORT=587
EMAIL_USE_TLS=1
EMAIL_HOST_USER=verein@example.org
EMAIL_HOST_PASSWORD=<smtp-passwort>
DEFAULT_FROM_EMAIL=verein@example.org
```

Einen sicheren `SECRET_KEY` kann man z. B. auf einem Linux-System erzeugen mit:

```bash
openssl rand -base64 48
```

Der Stack baut daraus intern die passende `DATABASE_URL` für Django. Das PostgreSQL-Passwort muss nur als `POSTGRES_PASSWORD` gesetzt werden:

```env
POSTGRES_PASSWORD=<datenbankpasswort>
```

Falls Portainer die `.env`-Datei aus dem Repository nutzt, kann stattdessen eine eigene `.env` im Stack-Kontext gepflegt werden. Keine echten Passwörter ins GitHub-Repository committen.

## 5. Stack deployen

1. In Portainer **Deploy the stack** anklicken.
2. Warten, bis beide Container laufen:
   - `db`
   - `web`
3. Die Datenbank muss im Healthcheck als gesund erscheinen.

## 6. Ersteinrichtung nach dem Start

Beim Containerstart führt der Stack automatisch aus:

```bash
python manage.py migrate
python manage.py collectstatic --noinput
```

Nach dem ersten Start muss nur noch ein Admin-Benutzer erstellt werden.

In Portainer:

1. Container **web** öffnen.
2. **Console** öffnen.
3. Shell starten, z. B. `/bin/sh`.
4. Befehl ausführen:

```bash
python manage.py createsuperuser
```

Beim Superuser werden Benutzername, E-Mail und Passwort abgefragt.

## 7. Aufruf der Anwendung

Wenn der Stack-Port unverändert ist:

```text
http://<host-oder-ip>:8000/
```

Adminbereich:

```text
http://<host-oder-ip>:8000/admin/
```

Die Startseite `/` ist login-geschützt und leitet auf den Admin-Login weiter.

## 8. Reverse Proxy / HTTPS

Für mobile Nutzung mit Signaturen und späterem Dokumentenversand sollte die Anwendung über HTTPS erreichbar sein.

Empfehlung:

- interner Container-Port: `8000`
- externer Reverse Proxy: HTTPS auf Port `443`
- Ziel im Proxy: `http://<docker-host>:8000`

Dann `ALLOWED_HOSTS` auf die echte Domain setzen, z. B.:

```env
ALLOWED_HOSTS=verleih.example.org
```

## 9. Persistente Daten und Backups

Der Stack verwendet Docker-Volumes:

```text
postgres_data  Datenbank
media_data     PDFs, Signaturen, spätere Fotos
static_data    statische Dateien
```

Für Backups müssen mindestens gesichert werden:

- PostgreSQL-Datenbank/Volume `postgres_data`
- Medien-Volume `media_data`
- verwendete Stack-/Environment-Konfiguration ohne öffentliche Weitergabe von Passwörtern

Empfohlener Datenbankdump im `db`-Container:

```bash
pg_dump -U verleih_ff -d verleih_ff -Fc > /tmp/verleih_ff.dump
```

## 10. Aktualisierung

Bei Installation über Git-Repository:

1. In Portainer den Stack öffnen.
2. **Pull and redeploy** bzw. **Update the stack** ausführen.
3. Migrationen und `collectstatic` laufen beim Neustart automatisch.

## 11. Häufige Fehler

### Web-Container startet nicht

Prüfen:

- sind `POSTGRES_DB`, `POSTGRES_USER` und `POSTGRES_PASSWORD` gesetzt?
- ist der `db`-Container gesund?

### Login-Seite oder Startseite nicht erreichbar

Prüfen:

- ist Port `8000` am Host frei?
- wurde der Stack-Port korrekt veröffentlicht?
- steht Hostname/IP in `ALLOWED_HOSTS`?

### Mailversand funktioniert später nicht

Prüfen:

- SMTP-Host, Port, TLS-Einstellung
- Benutzername/Passwort
- Absenderadresse `DEFAULT_FROM_EMAIL`
- Firewall/Provider-Sperren für ausgehende SMTP-Verbindungen

## 12. Sicherheitsnotizen

- `DEBUG=0` für produktive Nutzung.
- Starke Passwörter verwenden.
- Anwendung hinter HTTPS betreiben.
- Keine echten Secrets in GitHub speichern.
- Portainer-Zugriff selbst absichern.
- Regelmäßige Backups testen, nicht nur einrichten.
