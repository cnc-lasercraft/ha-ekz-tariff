# EKZ API Issues

## 1. `electricity_standard` liefert Business-Tarif statt Privatkunden-Tarif

- **Entdeckt:** 2026-03-25
- **API-Call:** `GET /v1/tariffs?tariff_name=electricity_standard`
- **Erwartet:** Privatkunden-Tarif "EKZ Energie Erneuerbar" (14.38 Rp. Winter / 9.73 Rp. Sommer)
- **Geliefert:** 13.3 Rp. (flat, ganzjährig) — entspricht dem Business-Tarif
- **Update 2026-03-28:** 13.3 Rp × 1.081 (MWST 8.1%) = 14.38 Rp — der Wert ist korrekt, die API liefert **netto** (ohne MWST). Die vermeintliche Differenz war die fehlende MWST.
- **Status:** GELÖST — Preise sind netto, MWST muss separat aufgeschlagen werden

## 2. `customerTariffs` publication_timestamp ist Request-Zeitpunkt, nicht Publikationszeit

- **Entdeckt:** 2026-03-25
- **API-Call:** `GET /v1/customerTariffs?tariffType=electricity_dynamic&ems_instance_id=...&start_timestamp=...&end_timestamp=...`
- **Erwartet:** Zeitpunkt wann EKZ die Tarife publiziert hat (z.B. 17:15 für Tomorrow-Preise)
- **Geliefert:** Zeitpunkt des API-Requests (z.B. 21:47 bei Fetch um 21:47)
- **Update 2026-03-28:** Am 27.3. zeigte publication_timestamp 17:15:00 UTC (18:15 lokal), Fetch war 17:15:01. Möglicherweise hat EKZ dies korrigiert oder es funktioniert seit Aktivierung der dynamischen Tarif-Vorbereitung.
- **EKZ bestätigt:** Publication timestamp wird täglich korrekt geliefert (Telefonat 27.3.)
- **Status:** VERMUTLICH GELÖST — nach 1.4. verifizieren

## 3. `customerTariffs` liefert ebenfalls Geschäftskunden-Tarif

- **Entdeckt:** 2026-03-26
- **API-Call:** `GET /v1/customerTariffs?tariffType=electricity_dynamic&ems_instance_id=...`
- **Erwartet:** Privatkunden All-in 25.93 Rp/kWh (Q1 2026)
- **Geliefert:** 23.83 Rp/kWh integrated (= electricity 13.30 + grid 10.53). regional_fees 0.16 Rp nicht im integrated enthalten. metering fehlt ganz.
- **Update 2026-03-28:** Alle Preise sind **netto**. (13.30 + 10.53 + 0.16) × 1.081 = 25.93 Rp — exakt der konfigurierte Q1-Wert. Die API liefert korrekte Privatkunden-Preise in netto.
- **Baseline-Berechnung:** electricity (Public, 0.1330) + grid (customerTariffs, 0.1053) + regional_fees (customerTariffs, 0.0016) = 0.2399 CHF/kWh netto = 25.93 Rp brutto
- **Status:** GELÖST — Preise sind netto, kein Business-Tarif-Problem

## 4. Dynamische Preise vor offiziellem Start erhalten

- **Entdeckt:** 2026-03-26
- **Beobachtung:** Über `customerTariffs` mit `tariffType=electricity_dynamic` wurden bereits mehrere Wochen lang bis zum 25.3.2026 Preisdaten geliefert, obwohl der dynamische Tarif offiziell erst ab 1.4.2026 gilt.
- **Verhalten:** Alle Slots haben identischen Preis (23.83 Rp netto) — de facto ein Flat-Tarif im Dynamic-Format
- **Update 2026-03-28:** API liefert auch Morgen-Daten jederzeit (nicht nur nach 18:15). Dies liegt am Pre-Dynamic-Modus. Ab 1.4. werden Morgen-Daten vermutlich erst nach Publikationszeit (~18:00) verfügbar sein.
- **Status:** ERWARTET — Pre-Dynamic Flat-Rate bis 31.3.

## 5. Public API Grid-Preis weicht ab

- **Entdeckt:** 2026-03-28
- **Public API (ohne tariff_name):** grid = 0.1098 CHF/kWh (netto)
- **customerTariffs:** grid = 0.1053 CHF/kWh (netto)
- **Differenz:** 0.45 Rp — Public API hat höheren Grid-Preis
- **Auswirkung:** Baseline wird korrekt aus Public electricity + customerTariffs grid/regional_fees berechnet (nicht aus Public grid)
- **Status:** BEKANNT — Public API Grid wird nicht für Baseline verwendet

## 6. DST-Handling: 93 statt 92 Slots

- **Entdeckt:** 2026-03-28
- **Public API** für 29.3.2026 (DST-Tag): liefert 93 Slots statt erwarteter 92
- **Letzter Slot:** `start_timestamp: 2026-03-30T00:00:00+02:00` — gehört zum nächsten Tag
- **customerTariffs** für 29.3.2026: liefert 97 Slots (92 für 29.3. + 5 Überlauf für 30.3.)
- **Auswirkung:** Validator filtert korrekt nach Zieldatum, Überlauf-Slots werden ignoriert
- **Status:** KEIN PROBLEM — Validator handhabt dies korrekt

## 7. No-Data-Tage: API liefert gar keine Slots für den Folgetag

- **Entdeckt:** 2026-08-01 (1. Vorfall), bestätigt 2026-08-12/13 (2. Vorfall)
- **Beobachtung:** Daily Fetch 18:15 + 4 Retries → `no_data` für den Folgetag. Am 12.08. für den 13.08.; auch manueller `ekz_tariff.fetch_date` am Folgemorgen (13.08. ~09:30) lieferte noch "Keine Slots".
- **Kein Feiertags-Muster:** 01.08. war Feiertag, 13.08. (Mi) nicht — These widerlegt.
- **Auswirkung:** Betroffener Tag läuft komplett ohne Preisdaten. Tariff Saver invalidiert Pläne (`error_no_data`, seit 02.05.). Ladeempfehlung (EV-Ladeermahnung) kann an solchen Tagen strukturell nie ON werden (Tarif-Fenster UND `pv_kw`-Mapping hängen an den Slots).
- **Update 2026-08-13 abends:** 2. Tag in Folge — auch Fetch für 14.08. leer (4 Retries + `test_fetch` 23:13). Debug-Dump zeigt: Request identisch zum letzten Erfolg (11.08.), Antwort ist **HTTP-ok mit `"prices": []`** + publication_timestamp — kein Auth-/Request-Fehler. Public API (`/v1/tariffs`, ohne Auth) liefert ebenfalls `{"prices":[]}` für 13./14.08. → **EKZ publiziert seit 12.08. serverseitig keine dynamischen Preise.** Letzter publizierter Tag: 12.08. (geliefert 11.08. 18:15).
- **Update 2026-08-15:** Publikation kam am 14.08. 18:15 **von selbst zurück** (96 Slots für 15.08. validiert, kein Eingriff nötig). Dauer der Störung: 2 Publikationstage (13.+14.08. ohne Daten).
- **Status:** BEOBACHTEN — EKZ-seitige Publikations-Aussetzer, bisher 3 No-Data-Tage in 2 Wochen (01.08., 13.08., 14.08.), heilen sich bislang selbst. Konsequenz in Tariff Saver: Teil-3-Fallback (letzte bekannte Slots als Schätzung) bauen, damit Consumer-Pläne + Ladeermahnung solche Tage überstehen.

## 8. customerTariffs-Endpoint komplett kaputt: 401/500 über mehrere Tage

- **Zeitraum:** 19.08. abends – mind. 24.08.2026 (bei Deploy des Fallbacks noch aktiv)
- **Symptom:** Täglicher 18:15-Fetch scheiterte 5 Abende in Folge (19.–23.08.) mit `401 Unauthorized` (leerer Body). Tagsüber am 23./24.08. lieferte derselbe Endpoint `500 Internal Server Error` (`/v1/customerTariffs`). Auch `emsLinkStatus` gab 401.
- **Kein Client-Problem:** Public API (`/v1/tariffs`, ohne Auth) lieferte im selben Zeitraum vollständige 96-Slot-Tage für `electricity_dynamic` UND `grid_400d`. OAuth-Token-Refresh lief fehlerfrei durch (kein Keycloak-Fehler) — der 401 kam vom API-Backend, nicht vom Auth-Server.
- **Verstärker im alten Code:** `EkzTariffAuthError` führte zu `raise ConfigEntryAuthFailed` OHNE Retry (retry_count blieb 0) — bereits der Link-Status-Check (Schritt 1) beendete den Abend. Seit 2026-08-24 gefixt: Link-Status-401 nur noch Log, Fetch-Auth-Fehler bekommen normale Retries + Public-Fallback.
- **Auswirkung:** 4 Tage ohne Preisdaten (20.–23.08.), 4 Tage kein automatisches Warmwasser (Boiler auf 30.9 °C). August-Bilanz gesamt: 7 No-Data-Tage (01., 13., 14., 20.–23.).
- **Preis-Rekonstruktion verifiziert (Basis des Public-Fallbacks):** `customer.electricity == public electricity_dynamic` (exakt, 12/12 Stichproben); `customer.grid == public grid_400d + 0.0276 CHF/kWh` (konstant über alle Stichproben 15./17./18./19.08.); `regional_fees` konstant 0.0016; `integrated = electricity + grid` (ohne regional_fees).
- **Status:** OFFEN bei EKZ (melden!). Systemseitig seit 2026-08-24 durch Public-API-Fallback entschärft.

## 9. Teillieferung: customerTariffs gibt 8 statt 96 Slots — Fallback greift nicht

- **Entdeckt:** 2026-09-01 (Fetch vom 31.08. 18:15 für den 01.09.); dasselbe Muster schon am 01.05.2026 (8/96 Slots für den 01.05.)
- **Symptom:** `customerTariffs` antwortet HTTP-ok mit einem **unvollständigen** Tag — 8 Slots (2 h) statt 96. Alle 4 Retries (18:15/18:30/18:45/19:00) liefern exakt dieselben 8 Slots. Validator verwirft korrekt (`insufficient_slots`), Ergebnis ist ein kompletter No-Data-Tag.
- **Public API war vollständig:** `/v1/tariffs` hatte für den 01.09. **96 Slots** `electricity_dynamic` + 96 `grid_400d`, `publication_timestamp` 31.08. **17:49** — also 26 Minuten VOR dem 18:15-Fetch. Die Daten waren da, nur die Kunden-API gab sie nicht heraus.
- **Lücke im Fallback (bis 2026-09-01):** Der Public-Fallback hing an `if not parsed_slots` — er sprang nur bei einer *leeren* Antwort an. 8 Slots sind „truthy", also wurde der Fallback übersprungen und die Teillieferung lief ungebremst in die Validierung. Betraf `_do_fetch_and_process` **und** das Recovery-Werkzeug `fetch_date`, d.h. der Tag liess sich auch von Hand nicht mehr retten.
- **Fix 2026-09-01:** Neuer Helper `_apply_public_fallback()` — der Fallback greift, sobald die Kunden-API **weniger als die DST-erwartete Slotzahl** liefert (`validator.expected_slots_for_date`, 96/92/100), und wird nur übernommen, wenn er mehr Slots bringt als die Kunden-API. Eine Teillieferung ist genauso wertlos wie gar keine — beide scheitern an der Validierung.
- **Status:** OFFEN bei EKZ (zusammen mit Issue 8 melden). Systemseitig seit 2026-09-01 abgedeckt.
