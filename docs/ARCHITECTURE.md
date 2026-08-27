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
- **Protokolle**: Übergabe und Rücknahme mit Signaturen, optionalen Rücknahmefotos und späterer PDF-Datei.
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
2. Verfügbarkeitsprüfung für Artikel und Zeitraum ergänzen. Status: mengenbasierte Überschneidungslogik für blockierende Vorgänge umgesetzt.
3. Prozess-Dashboard bereitstellen. Status: mobile Tagesübersicht für Abholungen, Rücknahmen, offene Spenden und Klärfälle mit Suche, Monatskalender, dynamischer Web-Vorgangsanlage inklusive Entleiherprüfung/-aktualisierung, Vorgangsdetailseite und Abschlussaktion umgesetzt.
4. Mobile Übergabe bauen. Status: Touch-optimierte Übergabemaske mit Artikelzuständen, Protokollnotiz, zwei Signaturen und Statuswechsel auf „Übergeben“ umgesetzt.
5. Mobile Rücknahme bauen. Status: Geführte Abschnittsmaske für Vorgangsprüfung, Artikelzustand, Zubehörprüfung, Schäden/Reinigung, optionale Fotos, Signaturen und Statuswechsel auf „Zurückgenommen“ oder „Klärung nötig“ umgesetzt.
6. PDF-Templates für Reservierung, Übergabe, Rücknahme und Abschluss ergänzen. Status: PDF-Basislayout, Reservierungsbestätigung, Übergabeprotokoll, Rücknahmeprotokoll und Abschlussübersicht mit Dokumentenablage/Download umgesetzt.
7. SMTP-Mailversand mit PDF-Anhang implementieren. Status: erster manueller Versand bestehender bzw. direkt erzeugter Dokumente an Entleiher mit Dashboard-Mailbuttons und Status-/Fehlerablage umgesetzt.
8. Spenden-/Zahlungsprozess konkretisieren. Status: offene Spenden sind im Dashboard sichtbar; Spendenentscheidung, Zahlungsart, Betrag, Notiz und Eingangszeitpunkt werden dokumentiert.
9. Benutzer/Rollen/Sicherheit. Status: Mustergruppen für Admin, Verwaltung/Vorstand, Helfer Ausgabe/Rücknahme und Lesen/Auswertung werden automatisch angelegt; Dashboard und Prozess-/Dokumentenviews prüfen passende Verleih-Rechte.
10. Synology-Deployment mit Reverse Proxy/HTTPS dokumentieren.
