# Billbee Export App

Lokale Web-App und CLI zum Export von Billbee-Verkaufsdaten als CSV.

## Funktionen

- Export nach Datumsbereich
- Plattformfilter: Etsy, Kasuwa, Amazon, eBay, ohne Plattform
- CSV mit allen verfügbaren Feldern als einzelne Spalten
- Kompakte Exporte pro Bestellung oder Artikelposition
- Keine externen Python-Abhängigkeiten

## Zugangsdaten

Die Zugangsdaten werden nicht im Code gespeichert. Setze sie vor dem Start als Umgebungsvariablen.

PowerShell:

```powershell
$env:BILLBEE_API_KEY="dein-api-key"
$env:BILLBEE_USERNAME="dein-billbee-login"
$env:BILLBEE_API_PASSWORD="dein-api-passwort"
```

Optional:

```powershell
$env:BILLBEE_BASE_URL="https://api.billbee.io/api/v1"
```

## Web-App starten

```powershell
python .\billbee_export_app.py
```

Danach im Browser öffnen:

```text
http://127.0.0.1:8765/
```

Unter Windows kann alternativ eine Starterdatei genutzt werden:

```cmd
start_billbee_app_and_open.cmd
```

## CLI-Beispiel

```powershell
python .\billbee_sales_export.py `
  --rows all `
  --platform Etsy `
  --platform Kasuwa `
  --min-order-date "2026-06-01T00:00:00" `
  --max-order-date "2026-06-30T23:59:59" `
  --output ".\billbee_exports\etsy_kasuwa_juni.csv"
```

## Sicherheit

Dieses Repository sollte keine echten Zugangsdaten und keine exportierten Kunden- oder Verkaufsdaten enthalten. `.gitignore` schließt lokale `.env`, CSV- und JSON-Dateien aus.
