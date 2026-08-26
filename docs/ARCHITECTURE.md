# Architektur Verleih FF

## Zielbild

Die Anwendung läuft auf einer Synology NAS als Docker-Compose-Stack:

- `web`: Django-Anwendung
- `db`: PostgreSQL-Datenbank
- persistente Volumes für Datenbank, Medien, PDFs und Signaturen

## Fachliche Kernobjekte

- **Verleihartikel**: Festausstattung mit Bestand, Lagerort, Artikelstatus, Zustand, Spendenempfehlung, Kaution und Ersatzwert.
- **Zubehör/Bestandteile**: Pflicht- oder optionale Bestandteile eines Artikels, z. B. Stromkabel, Heringe oder Transportboxen.
- **Entleiher**: Person oder Organisation, Kontaktdaten und interne Notizen.
- **Verleihvorgang**: Zeitraum, Status, Spende, Bemerkungen und Abschluss.
- **Vorgangspositionen**: ausgeliehene Artikel mit Menge, Übergabe-/Rücknahmezustand, Fehlmengen und Schäden.
- **Protokolle**: Übergabe und Rücknahme mit Signaturen und späterer PDF-Datei.
- **Dokumente**: erzeugte und versendete PDFs mit Versandstatus.

## Prozessstatus

```text
Anfrage
→ Reserviert
→ Abholung vorbereitet
→ Übergeben
→ Spende offen / Spende erhalten
→ Zurückgenommen
→ Klärung nötig
→ Abgeschlossen
```

Sonderstatus:

```text
Storniert
```

## Nächste Entwicklungsschritte

1. Admin-/Backend-Grundfunktionen stabilisieren. Status: erste Phase umgesetzt mit Artikelstatus, Zubehör/Bestandteilen, Ersatzwert, Admin-Feldsets und abgesicherten Statusübergängen.
2. Verfügbarkeitsprüfung für Artikel und Zeitraum ergänzen. Status: mengenbasierte Überschneidungslogik für blockierende Vorgänge umgesetzt; Kalender-/Listenansicht folgt im Dashboard-Schritt.
3. Mobile Prozessseiten für Übergabe und Rücknahme bauen.
4. Signatur-Erfassung im Browser integrieren.
5. PDF-Templates für Reservierung, Übergabe, Rücknahme und Abschluss ergänzen.
6. SMTP-Mailversand mit PDF-Anhang implementieren.
7. Synology-Deployment mit Reverse Proxy/HTTPS dokumentieren.
