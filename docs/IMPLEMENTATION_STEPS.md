# Umsetzungsschritte Verleih FF

Dieses Dokument beschreibt die notwendigen Schritte von der aktuellen Grundstruktur bis zur produktiv nutzbaren Vereins-Verleihplattform auf der Synology NAS.

## Phase 0 – Projektbasis

Status: begonnen mit PR #1.

- Django-Projekt anlegen.
- PostgreSQL-/Docker-Compose-Grundlage vorbereiten.
- Datenmodell für Verleihartikel, Entleiher, Vorgänge, Positionen, Protokolle und Dokumente erstellen.
- Adminbereich für Stammdaten und Vorgänge registrieren.
- README, Architektur und `.env.example` pflegen.
- Basistests und Django-Systemcheck ausführen.

Abnahmekriterien:

- `python manage.py check` läuft fehlerfrei.
- `python manage.py test` läuft fehlerfrei.
- Repository enthält eine nachvollziehbare Startanleitung.

## Phase 1 – Stammdaten und Vorgangsverwaltung

Ziel: Verwaltung kann Artikel, Entleiher und Vorgänge vollständig im Backend pflegen.

Aufgaben:

1. Artikelstammdaten erweitern:
   - Zubehör/Bestandteile
   - Ersatz-/Schadenswert optional
   - Aktiv-/Defekt-/Wartungsstatus
   - Lagerort und Bemerkungen
2. Entleiherdaten validieren:
   - Pflichtfelder für Name und E-Mail
   - optionale Telefonnummer und Adresse
3. Vorgangsmaske verbessern:
   - klare Statusanzeige
   - Positionen direkt am Vorgang pflegen
   - berechnete Summen für Spendenempfehlung, Kaution und Schäden
4. Statusübergänge absichern:
   - Reservieren
   - Abholung vorbereiten
   - Übergabe starten
   - Rücknahme starten
   - Vorgang abschließen
5. Audit-/Historienfelder anzeigen:
   - erstellt/geändert am
   - Bearbeiter, soweit relevant

Abnahmekriterien:

- Ein vollständiger Vorgang kann im Admin angelegt werden.
- Artikelpositionen können gepflegt werden.
- Statuswechsel folgen dem vorgesehenen Prozess.

## Phase 2 – Verfügbarkeitsprüfung und Kalenderlogik

Status: erste mengenbasierte Überschneidungsprüfung umgesetzt.

Ziel: Artikel können nicht versehentlich doppelt im selben Zeitraum zugesagt werden.

Aufgaben:

1. Blockierende Status definieren:
   - Reserviert
   - Abholung vorbereitet
   - Übergeben
   - Spende offen, wenn noch nicht zurückgegeben
2. Zeitraumüberschneidung je Artikel berechnen.
3. Verfügbarkeit bei Vorgangspositionen prüfen.
4. Warnungen im Admin anzeigen.
5. Optional: harte Sperre beim Speichern, wenn Bestand überschritten wird.
6. Kalender-/Listenansicht vorbereiten:
   - heutige Abholungen
   - heutige Rückgaben
   - offene Vorgänge

Abnahmekriterien:

- Das System erkennt Doppelbelegungen.
- Bestände werden mengenbasiert berücksichtigt.
- Tests decken Überschneidungen und freie Zeiträume ab.

## Phase 3 – Prozess-Dashboard

Status: erste mobile-optimierte Tagesübersicht umgesetzt.

Ziel: Helfer sehen sofort, was heute zu tun ist.

Aufgaben:

1. Dashboard-Seite außerhalb des Django-Admins erstellen.
2. Karten/Listen für:
   - Heute abzuholen
   - Heute zurückzugeben
   - Offene Spenden
   - Klärung nötig
   - Kürzlich abgeschlossene Vorgänge
3. Suchfunktion für Vorgangsnummer, Entleiher und Artikel.
4. Rollen-/Login-Schutz aktivieren.
5. Mobile Ansicht optimieren.

Abnahmekriterien:

- Helfer können nach Login die Tagesaufgaben sehen.
- Dashboard funktioniert auf Smartphone/Tablet.

## Phase 4 – Mobile Übergabe

Status: erste mobile Übergabemaske mit Touch-Signaturen umgesetzt.

Ziel: Übergabe kann vor Ort auf Tablet oder Smartphone durchgeführt werden.

Aufgaben:

1. Mobile Übergabeseite pro Vorgang bauen.
2. Artikelpositionen mit Menge und Zustand anzeigen.
3. Bemerkungen und Hinweise erfassen.
4. Entleiherdaten bestätigen.
5. Unterschrift Entleiher per Touch erfassen.
6. Unterschrift Verein/Helfer per Touch erfassen.
7. Übergabeprotokoll speichern.
8. Status auf `Übergeben` setzen.

Abnahmekriterien:

- Übergabe kann ohne Django-Admin durchgeführt werden.
- Signaturen werden gespeichert.
- Protokoll ist dem Vorgang zugeordnet.

## Phase 5 – Mobile Rücknahme

Status: erste geführte mobile Rücknahmemaske mit Abschnittsbestätigungen umgesetzt.

Ziel: Rücknahme kann vor Ort vollständig dokumentiert werden.

Aufgaben:

1. Mobile Rücknahmeseite pro Vorgang bauen.
2. Artikelpositionen prüfen:
   - zurückgegeben
   - fehlt
   - beschädigt
   - Reinigungsbedarf
3. Bemerkungen und Schadenbeträge erfassen.
4. Unterschriften erfassen.
5. Rücknahmeprotokoll speichern.
6. Status auf `Zurückgenommen` oder `Klärung nötig` setzen.

Abnahmekriterien:

- Rücknahme kann mobil abgeschlossen werden.
- Schäden/Fehlteile bleiben offen sichtbar.
- Vorgang kann erst abgeschlossen werden, wenn offene Punkte geklärt sind.

## Phase 6 – PDF-Dokumente

Status: PDF-Basislayout, Reservierungsbestätigung, Übergabeprotokoll, Rücknahmeprotokoll und Abschlussübersicht mit Dateiablage umgesetzt.

Ziel: Alle relevanten Dokumente werden automatisch als PDF erzeugt und am Vorgang gespeichert.

Aufgaben:

1. PDF-Basislayout erstellen:
   - Vereinsname
   - Vorgangsnummer
   - Entleiherdaten
   - Datum/Zeit
   - Artikelpositionen
   - Hinweise
2. Reservierungsbestätigung erzeugen.
3. Übergabeprotokoll mit Signaturen erzeugen.
4. Rücknahmeprotokoll mit Signaturen erzeugen.
5. Abschlussübersicht erzeugen.
6. Dokumentendatensätze mit Dateiablage speichern.

Abnahmekriterien:

- PDFs werden serverseitig erzeugt.
- Signaturen sind im PDF sichtbar.
- Dokumente sind am Vorgang abrufbar.

## Phase 7 – Mailversand

Status: erster manueller Dokumentenversand mit PDF-Anhang, Versandstatus, Fehlerablage und Dashboard-Mailbuttons umgesetzt.

Ziel: Dokumente können direkt per E-Mail versendet werden.

Aufgaben:

1. SMTP-Konfiguration aus `.env` nutzen.
2. Mailvorlagen erstellen:
   - Reservierungsbestätigung
   - Übergabeprotokoll
   - Rücknahmeprotokoll
   - Abschlussmail
3. PDF-Anhänge versenden.
4. Versandstatus und Fehler am Dokument speichern.
5. Manuelles erneutes Senden ermöglichen.
6. Testmodus/Entwicklungs-Mailbackend dokumentieren.

Abnahmekriterien:

- E-Mails werden mit PDF-Anhang versendet.
- Versandstatus ist nachvollziehbar.
- Fehler werden nicht verschluckt, sondern sichtbar gespeichert.

## Phase 8 – Spenden-/Zahlungsprozess

Status: Dashboard-Formular zum Dokumentieren von Spendenentscheidung, Zahlungsart, Betrag, Zahlungsnotiz, Eingangszeitpunkt und Statuswechsel umgesetzt.

Ziel: Erwartete und erhaltene Spenden/Zahlungen sind nachvollziehbar.

Aufgaben:

1. Spendenstatus am Vorgang konkretisieren. Status: umgesetzt mit offen, erhalten, teilweise erhalten und verzichtet.
2. Zahlungsart ergänzen. Status: umgesetzt mit Bar, Überweisung, PayPal und Sonstig.
3. Zahlungsnotiz und Eingangsdatum speichern. Status: umgesetzt.
4. Offene Spenden im Dashboard anzeigen. Status: umgesetzt mit Formular „Spendenentscheidung speichern“.
5. Abschluss blockieren oder warnen, wenn Spende offen ist.

Abnahmekriterien:

- Offene Spenden sind sichtbar.
- Vorgang kann mit dokumentierter Spendenentscheidung abgeschlossen werden.

## Phase 9 – Benutzer, Rollen und Sicherheit

Ziel: Zugriff ist passend für Vereinsbetrieb abgesichert.

Aufgaben:

1. Rollen definieren:
   - Admin
   - Verwaltung/Vorstand
   - Helfer Ausgabe/Rücknahme
   - Lesen/Auswertung optional
2. Rechte für Views und Adminbereich konfigurieren.
3. Login erzwingen.
4. CSRF- und Session-Sicherheit prüfen.
5. Produktionssettings ergänzen:
   - `DEBUG=0`
   - `ALLOWED_HOSTS`
   - sichere Cookies bei HTTPS
6. Datenschutz-Hinweise in Dokumentation ergänzen.

Abnahmekriterien:

- Nicht angemeldete Nutzer sehen keine Vorgangsdaten.
- Helfer können nur benötigte Prozessmasken nutzen.
- Produktion läuft ohne Debug-Modus.

## Phase 10 – Synology-Deployment

Ziel: Anwendung kann reproduzierbar auf der Synology NAS betrieben werden.

Aufgaben:

1. Synology-Ordnerstruktur dokumentieren.
2. Docker Compose für produktive Nutzung ergänzen.
3. `.env` für Produktion beschreiben.
4. Datenbank-Backup-Skript erstellen.
5. Medien-/PDF-Backup beschreiben.
6. Reverse-Proxy-/HTTPS-Betrieb dokumentieren.
7. Update-Prozess dokumentieren:
   - Git Pull
   - Container neu bauen
   - Migrationen ausführen
   - Smoke-Test

Abnahmekriterien:

- Frische Synology-Installation ist anhand der Doku möglich.
- Backups sind nachvollziehbar.
- Update-Schritte sind klar beschrieben.

## Phase 11 – Tests und Qualitätssicherung

Ziel: Kernprozesse sind gegen Regressionen abgesichert.

Aufgaben:

1. Tests für Datenmodelle erweitern.
2. Tests für Verfügbarkeit und Statusübergänge ergänzen.
3. Tests für Übergabe-/Rücknahmeprozess ergänzen.
4. Tests für PDF-Erzeugung ergänzen.
5. Tests für Mailversand mit Test-Mailbackend ergänzen.
6. Optional: GitHub Actions für automatische Checks einrichten.

Abnahmekriterien:

- Kritische Geschäftsregeln sind getestet.
- PRs können vor Merge geprüft werden.

## Phase 12 – MVP-Abnahme

Ziel: Erste nutzbare Version für den Förderverein.

Abnahmeszenario:

1. Admin legt Artikel an.
2. Admin legt Entleiher an.
3. Verwaltung erstellt Reservierung mit Zeitraum und Artikeln.
4. System prüft Verfügbarkeit.
5. Helfer öffnet mobile Übergabe.
6. Entleiher und Helfer unterschreiben.
7. PDF wird erstellt und per E-Mail versendet.
8. Spende wird erfasst.
9. Helfer öffnet mobile Rücknahme.
10. Rückgabe wird dokumentiert und unterschrieben.
11. PDF wird erstellt und per E-Mail versendet.
12. Verwaltung schließt Vorgang ab.

MVP ist fertig, wenn dieses Szenario auf der Synology oder einer vergleichbaren Docker-Umgebung durchläuft.
