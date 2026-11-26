# Fritz!Box LAN DNS

Sammelt stündlich Mesh- und Host-Daten von einer FRITZ!Box, speichert sie in
SQLite, zeigt sie in einem Webinterface und erzeugt daraus eine DNS-Zone für
`*.<dns-domain>`.

Die DNS-Auslieferung übernimmt CoreDNS. Pi-hole bleibt der primäre LAN-DNS und
delegiert nur die lokale Domain an CoreDNS:

```text
Client -> Pi-hole (<pve-ip>:53)
            └── server=/<dns-domain>/<pve-ip>#53
                  └── CoreDNS (<pve-ip>:53)
                        ├── bekannte Hosts aus dns/hosts
                        └── bei MISS: foo.<dns-domain> -> foo.fritz.box. -> FRITZ!Box
```

CoreDNS beantwortet ausschließlich `<dns-domain>`. Anfragen für andere Namen
(z. B. `google.com`) werden mit `REFUSED` beantwortet; der Container ist damit
kein offener Resolver. Die Antworten der Stufe 2 werden auf den ursprünglichen
Owner-Namen `foo.<dns-domain>.` zurückgeschrieben.

## Funktionen

- Stündlicher Abruf per eingebautem Scheduler
- Mesh-Topologie inkl. LAN/WLAN/PLC-Verbindungen und PLC-Raten
- WLAN-Übersicht mit Radios, Kanälen, Kanalbreite, Auslastung, SSID,
  Sicherheit und verbundenen Clients inkl. Signal
- WLAN-Auffälligkeiten aus dem FRITZ!Box-Ereignisprotokoll
  (DFS/Radar, Kanalwechsel, Störquellen)
- Alle bekannten FRITZ!Box-Hosts (aktiv und offline)
- Optional: zweite Host-Quelle OpenWrt (DHCP-Leases + statische Hosts) via ubus
- Webinterface für Dashboard, Topologie, WLAN, Hosts, DNS und Historie
- DNS-Records aus Hostnamen und IPs, inkl. Konflikt-Erkennung und Quellenangabe
- IP-Zuweisung pro Host: statisch, DHCP oder DHCP mit dauerhafter Zuweisung
- `dns/hosts` für CoreDNS, `dns/<dns-domain>.zone` als Export
- MISS-Fallback: unbekannte `foo.<dns-domain>` werden als `foo.fritz.box.` an
  die FRITZ!Box gefragt (Default; `bare` ist konfigurierbar); Antworten werden
  auf `foo.<dns-domain>.` zurückgeschrieben
- Miss-Logging: jede Anfrage an Stufe 2 landet mit Name, Typ und RCODE auf stdout
- Manueller Refresh im Webinterface
- Keine externen JS-/CSS-Abhängigkeiten

## Voraussetzungen

- Python 3.12 (lokal) oder Docker
- FRITZ!Box-Benutzer mit TR-064-/Host-Rechten
- CoreDNS-Container für die DNS-Auslieferung
- Pi-hole als primärer LAN-DNS (Konfiguration erfolgt separat)
- Port 53 auf dem Docker-Host muss frei sein

## Konfiguration

```bash
cp .env.example .env
chmod 600 .env
```

`DNS_DOMAIN` und die DNS-Upstreams sind Pflichtwerte. Die App bricht mit einer
klaren Fehlermeldung ab, wenn sie fehlen.

Beispielwerte (die echten Werte stehen nur in der lokalen `.env`):

```env
BoxIP='192.0.2.1'
BoxUSER='fritz-user'
BoxPW='fritz-password'

# Optional: FRITZ-Repeater außerhalb des Mesh direkt per TR-064 abfragen
FRITZ_REPEATER_IPS=
FRITZ_REPEATER_USER=
FRITZ_REPEATER_PW=
FRITZ_REPEATER_TIMEOUT=25

FETCH_INTERVAL_MINUTES=60
WEB_HOST=0.0.0.0
WEB_PORT=8346

# Port-80-Scan aktiver Hosts
HTTP_SCAN_ENABLED=1
HTTP_SCAN_TIMEOUT=1.0
HTTP_SCAN_WORKERS=16
HTTP_SCAN_FAIL_THRESHOLD=2

DNS_DOMAIN=example.test
DNS_ZONE_TTL=60
DNS_INCLUDE_IPV6=0
DNS_HOST_RETENTION_DAYS=90

# MISS-Fallback direkt zur FRITZ!Box (<BoxIP>:53)
DNS_FALLBACK_ENABLED=1
DNS_FALLBACK_MODE=fritz.box

# Optional: A-Record für ns1.<dns-domain>
DNS_NS_IP=192.0.2.10

SNAPSHOT_RETENTION_DAYS=90
RAW_RETENTION_DAYS=30

# WLAN-Auffälligkeiten aus dem FRITZ!Box-Ereignisprotokoll
FRITZ_EVENTLOG_ENABLED=1
EVENT_RETENTION_DAYS=90

# Optional: OpenWrt als zweite Host-Quelle
OPENWRT_ENABLED=0
OPENWRT_URL=http://192.0.2.3
OPENWRT_USER=ubusro
OPENWRT_PASSWORD=openwrt-password
OPENWRT_TIMEOUT=25
OPENWRT_VERIFY_TLS=0
OPENWRT_TOPOLOGY_PARENT_IP=192.0.2.1
OPENWRT_MWAN3_ENABLED=1
```

`DNS_FALLBACK_MODE=fritz.box` (Default) fragt bei einem MISS
`foo.fritz.box.` bei der FRITZ!Box an. `DNS_FALLBACK_MODE=bare` fragt
stattdessen den nackten Namen `foo.`; dabei leakt die FRITZ!Box unbekannte
Single-Label-Namen an ihren externen Upstream (siehe „Privacy notes“).

Nach jedem Abruf werden die aktiven Hosts zusätzlich auf einen offenen
TCP-Port 80 geprüft (`HTTP_SCAN_ENABLED`). Der Status wird pro IP gespeichert
und latcht: War Port 80 einmal offen, bleibt das so, bis zwei aufeinanderfolgende
Scans eines aktiven Hosts fehlschlagen (`HTTP_SCAN_FAIL_THRESHOLD`). Ein
Offline-Gehen des Hosts widerlegt den Status nicht. Offene Webports werden in
Hosts, Topologie, DNS, Dashboard und den API-Antworten als `target="_blank"`-Link
auf `http://<ip>/` dargestellt.

Optional können Gerätebeschreibungen gepflegt werden:

```bash
cp config/device-names.conf.example config/device-names.conf
```

Die echte `config/device-names.conf` bleibt lokal und wird nicht veröffentlicht.
Die Beschreibungen gelten auch für OpenWrt-Hosts: Lookup über den Hostnamen,
sonst über den Basisnamen vor dem ersten Punkt (`nas.lan` → `nas`).

## Lokal starten

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt

# Einmalig abrufen
./fetch.sh

# Webinterface auf 0.0.0.0:8346
./web.sh
```

Weitere CLI-Befehle:

```bash
python -m fritz_lan_dns init-db
python -m fritz_lan_dns rebuild-dns
python -m fritz_lan_dns fetch
```

## Container-Image

GitHub Actions baut das Image bei jedem Push auf `main` und manuell über
`workflow_dispatch`:

```bash
docker pull ghcr.io/skoelle/fritz-lan-dns:latest
```

Tags:

- `latest`
- `sha-<kurz>`

Falls das GHCR-Package nach dem ersten Build noch privat ist, in den
GitHub-Package-Einstellungen auf **public** stellen.

Nach dem Push ruft der Workflow optional den Deploy-Webhook auf. Dafür müssen
die Repository-Secrets `WEBHOOK_URL` und `WEBHOOK_TOKEN` gesetzt sein. Ein
Webhook-Fehler oder `summary.updated <= 0` lässt den Workflow fehlschlagen.

Für lokale Entwicklung:

```bash
docker build -t fritz-lan-dns:local .
```

## Docker

```bash
cp docker-compose.example.yml docker-compose.yml
mkdir -p db data dns
sudo chown 1000:1000 db data dns
docker compose pull
docker compose up -d
```

Die Einbindung ist bewusst getrennt:

- **`.env`** wird über `env_file: .env` als Umgebungsvariablen in den Container
  gereicht. Sie wird nicht ins Image kopiert.
- **`config/`** wird read-only nach `/app/config` gemountet. Die private
  `config/device-names.conf` landet dadurch nicht im Image.
- **`data/`, `db/`, `dns/`** sind beschreibbare Volumes.

Das Compose-Beispiel startet zwei Services:

- `fritz-lan-dns` auf Port `8346`
- `coredns` auf Port `53/udp` und `53/tcp`

CoreDNS liest `/zones/Corefile` und `/zones/hosts`. Die App schreibt beide
Dateien atomar; CoreDNS lädt Änderungen automatisch nach.

## Pi-hole-Delegation

Pi-hole bleibt primärer DNS. Dort nur die lokale Zone an CoreDNS delegieren:

```text
server=/<dns-domain>/<pve-ip>#53
```

In Pi-hole v6 kann das z. B. als zusätzliche dnsmasq-Zeile hinterlegt werden.
Danach DNS neu laden:

```bash
pihole reloaddns
```

Die Clients und der DHCP-DNS-Eintrag bleiben unverändert auf Pi-hole.

## DNS-Fallback

CoreDNS beantwortet zuerst alle bekannten Records aus `dns/hosts`. Bei einem
MISS innerhalb der lokalen Domain greift der zweite Server-Block:

```text
foo.<dns-domain> -> rewrite -> foo.fritz.box. -> FRITZ!Box <fritzbox-ip>:53
```

Die Antwort wird per `answer name` wieder auf den ursprünglichen Owner-Namen
`foo.<dns-domain>.` zurückgeschrieben. Ohne diese Rückumschreibung würde die
Antwort `foo.fritz.box.` als Owner tragen; viele Resolver (u. a. dnsmasq in
Pi-hole) verwerfen eine solche Abweichung zwischen Frage und Antwort.

Der Default `DNS_FALLBACK_MODE=fritz.box` nutzt die lokale `fritz.box`-Zone der
FRITZ!Box. Unbekannte Namen werden dort lokal mit NXDOMAIN beantwortet und
nicht an den externen Upstream weitergeleitet; live bekannte LAN-Hosts werden
weiterhin aufgelöst.

`DNS_FALLBACK_MODE=bare` fragt stattdessen den nackten Namen `foo.`. Das
funktioniert nur, wenn die FRITZ!Box nackte Hostnamen per DNS beantwortet, und
leakt unbekannte Single-Label-Namen an den externen Upstream (siehe
„Privacy notes“).

Bekannte Hosts aus `dns/hosts` beantworten nur die Typen, die dort stehen
(A und optional AAAA). Bei einem bekannten Host mit nur einem A-Record liefert
eine AAAA-Anfrage `NOERROR` mit leerer Antwort (NODATA) und wird **nicht** an
die FRITZ!Box weitergereicht. Das übernimmt das `hosts`-Plugin in CoreDNS
1.12.1 (Fallthrough nur bei NXDOMAIN).

## Miss-Logging

Der zweite Block loggt jede Anfrage, die dort ankommt, auf stdout:

```text
log . "{name} {type} {rcode}"
```

`{name}` ist der ursprüngliche Name (`foo.<dns-domain>.`), `{rcode}` der
RCODE der FRITZ!Box-Antwort. `NXDOMAIN` bedeutet, die FRITZ!Box kennt den
Namen nicht; `NOERROR` bedeutet, sie hat geantwortet (Treffer oder NODATA),
der Cache aber nicht.

Beispielausgabe (gekürzt, generische Testwerte):

```text
[INFO] gibtsnicht.example.test. A NXDOMAIN
[INFO] printer.example.test. A NOERROR
```

Im echten Log steht statt `example.test` die konfigurierte `<dns-domain>`.

Die Zeilen landen im Docker-Log des `coredns`-Containers. Empfohlen ist der
Standard-Logging-Driver `json-file` mit Rotation (im Compose-Beispiel gesetzt);
lesen lässt sich das mit:

```bash
docker compose logs -f coredns
```

Bewusst wird **kein** Docker-Socket in den Web-Container gemountet: Das wäre
eine zusätzliche Privilegien-/Angriffsfläche. Wer die Misses zentral sammeln
will, kann stattdessen einen Logging-Driver (z. B. Loki, Fluentd) konfigurieren.

## Privacy notes

Der nackte Fallback (`DNS_FALLBACK_MODE=bare`) schickt unbekannte Namen als
Single-Label-Namen (`foo.`) an die FRITZ!Box. Das wurde gegen die echte
FRITZ!Box geprüft: Ein Fantasiename wurde bis zu den Root-Servern
weitergeleitet (Antwort mit Root-SOA `a.root-servers.net`). In dieser
Konfiguration leckt der Name also nach außen.

Der Default `DNS_FALLBACK_MODE=fritz.box` vermeidet das: Die FRITZ!Box
beantwortet `foo.fritz.box.` aus ihrer lokalen Zone. Ein unbekannter Name
liefert NXDOMAIN mit der lokalen SOA `fritz.box.`; es geht nichts upstream.
Gleichzeitig löst dieser Modus live bekannte LAN-Hosts weiterhin auf und
vermeidet ein DNSSEC-SERVFAIL, das Pi-hole im nackten Modus für
`<dns-domain>`-NXDOMAINs lieferte.

Nachprüfen lässt sich das auf dem Docker-Host mit:

```bash
dig @<docker-host> zzz-leaktest-12345.<dns-domain>
dig @<fritzbox-ip> zzz-leaktest-12345.fritz.box
```

Im ersten Fall muss die AUTHORITY-Section die lokale SOA `fritz.box.` zeigen
(kein Root-SOA `a.root-servers.net`). Zusätzlich im Pi-hole-Log bzw. auf dem
Upstream der FRITZ!Box prüfen, ob `zzz-leaktest-12345` dort ankommt.

Status: **fritz.box-Modus verifiziert (kein Leak)**. Der frühere `bare`-Modus
hat nachweislich geleakt und ist daher nicht zu empfehlen.

## OpenWrt (optional)

Ein OpenWrt-Router kann als zweite Host-Quelle angebunden werden. Der Abruf
läuft über die LuCI/ubus-JSON-RPC-Schnittstelle `http://<router>/ubus`
(`uhttpd-mod-ubus` + `rpcd` + `rpcd-mod-luci`, bei LuCI Standard).

Verwendet werden:

- `luci-rpc getDHCPLeases` – aktive IPv4-/IPv6-Leases
- `luci-rpc getHostHints` – bekannte und statisch konfigurierte Hosts (auch
  offline)

Die Hosts werden über die MAC mit den FRITZ!Box-Daten zusammengeführt und
erscheinen unter derselben `DNS_DOMAIN` in `/hosts` und im DNS. In der
Oberfläche zeigt die Spalte „Quelle“ den Ursprung (`FritzBox`, `OpenWrt`);
Hosts, die nur über die FRITZ!Box-Mesh-Topologie bekannt sind, zählen dort zur
Quelle `FritzBox`. Die Hosts-Seite hat zusätzlich einen Quellen-Filter.
Schlägt der OpenWrt-Abruf fehl, bleibt der FRITZ!Box-Abruf erfolgreich und der
Fehler wird als Warnung im Snapshot vermerkt.

Mit `OPENWRT_TOPOLOGY_PARENT_IP` werden aktive OpenWrt-Clients als Sub-Nodes
unter dem Mesh-Gerät mit dieser IP in der Topologie angezeigt. Inaktive
OpenWrt-Hosts erscheinen zusätzlich in der Offline-Sektion. Die Injection
passiert beim Rendern aus der aktuellen Host-Tabelle und ist daher nicht in
historischen Snapshots enthalten.

Wird dasselbe Gerät (gleiche MAC) von beiden Routern mit unterschiedlichen IPs
gesehen, bleiben beide als getrennte Host-Zeilen erhalten (Quelle `FritzBox`
bzw. `OpenWrt`). Im DNS werden dann für denselben Hostnamen mehrere A-Records
geschrieben. Echte Namenskollisionen verschiedener Geräte (unterschiedliche
MACs) bleiben weiterhin Konflikte.

Ist `mwan3` auf dem OpenWrt-Router installiert (LuCI-App), wird mit
`OPENWRT_MWAN3_ENABLED=1` zusätzlich der MultiWAN-Status pro Snapshot
gespeichert und im Dashboard als Karte angezeigt: Interface-Status, Online-/
Uptime-Zeiten, Tracking-Score und die Policies mit Traffic-Anteilen. Fehlt das
`mwan3`-ubus-Objekt, wird der Abruf still übersprungen; bei anderen Fehlern
bleibt der letzte bekannte Stand sichtbar. Drahtlose MWAN3-Interfaces (z.B.
WWAN über WLAN) zeigen zusätzlich SSID, Signalstärke (dBm), Qualität, SNR,
Rauschen, Link-Rate, Band und Kanal – jeweils zum Zeitpunkt des Snapshots.

Für den Dauerbetrieb empfiehlt sich ein Read-only-User statt `root`. Auf dem
Router per SSH:

```sh
adduser -D -H -s /bin/false ubusro
passwd ubusro

cat > /usr/share/rpcd/acl.d/ubusro.json <<'EOF'
{
  "ubusro": {
    "description": "fritz-lan-dns read-only",
    "read": {
      "ubus": {
        "session": ["access", "login"],
        "luci-rpc": ["getDHCPLeases", "getHostHints"],
        "luci": ["getDHCPLeases", "getHostHints"]
      }
    },
    "write": {}
  }
}
EOF
chmod 644 /usr/share/rpcd/acl.d/ubusro.json

uci add rpcd login
uci set rpcd.@login[-1].username='ubusro'
uci set rpcd.@login[-1].password='$p$ubusro'
uci add_list rpcd.@login[-1].read='ubusro'
uci commit rpcd
/etc/init.d/rpcd restart

echo '/usr/share/rpcd/acl.d/ubusro.json' >> /etc/sysupgrade.conf
```

Der Docker-Container muss den OpenWrt-Router im anderen Subnetz erreichen
können (Routing/Firewall auf dem Docker-Host).

## WLAN

WLAN-Radios, SSID, Band, Kanal, Kanalbreite, Auslastung, Sicherheit und
Streams werden aus der FRITZ!Box-Mesh-Liste gelesen. Das gilt für die
FRITZ!Box selbst und für alle Repeater, die im Mesh sichtbar sind. WLAN-Clients
werden aus den Mesh-Links inklusive Signalstärke (`rx_rcpi`/`tx_rcpi`) und
Datenrate übernommen. Repeater-Uplinks zählen nicht als Clients.

Ein Repeater, der nicht im FRITZ!Box-Mesh hängt (z. B. hinter OpenWrt), kann
direkt per TR-064 abgefragt werden:

```env
FRITZ_REPEATER_IPS=192.0.2.10
FRITZ_REPEATER_USER=
FRITZ_REPEATER_PW=
```

`FRITZ_REPEATER_IPS` ist eine Komma- oder Leerzeichenliste. Ohne eigene
`FRITZ_REPEATER_USER`/`FRITZ_REPEATER_PW` werden `BoxUSER` und `BoxPW`
verwendet. Der Repeater muss vom Container aus erreichbar sein; bei einem
eigenen Subnetz hinter OpenWrt ist dafür eine Route auf dem Docker-Host
nötig. Der Zugriff braucht auf dem Repeater einen TR-064-Benutzer mit
Leserechten (App bzw. „FRITZ!Box Einstellungen“). Schlägt der Abruf fehl,
bleibt der Snapshot erfolgreich und zeigt eine Warnung.

Die direkte TR-064-Abfrage liefert bei manchen Repeatern keine Kanalbreite.
SSID, Kanal, Band, Sicherheit, Clients und Signal werden trotzdem angezeigt.
Die WLAN-Daten landen pro Snapshot in `wlan_json`; die neueste nicht-leere
Aufzeichnung wird im Dashboard und unter `/wlan` dargestellt.

Zusätzlich liest die App das vollständige FRITZ!Box-Ereignisprotokoll über
`X_AVM-DE_GetDeviceLogPath` (TR-064 über den Security-Port) und speichert nur
WLAN-Ereignisse. Im Dashboard und unter `/wlan` werden ausschließlich
ungewöhnliche Ereignisse gezeigt: Radar/DFS-Prüfungen, Kanalwechsel und
Störquellen. Normale Serienmeldungen (2,4-GHz-Bandbreitenreduktion,
Repeater-Anmeldefehler) sind standardmäßig ausgeblendet und über
`/wlan?all=1` sichtbar. Login- und Nicht-WLAN-Ereignisse werden ignoriert.
Das Roh-Log wird nicht abgelegt; die Events laufen nach
`EVENT_RETENTION_DAYS` (Default 90) aus.

## IP-Zuweisung

Hosts, DNS und Dashboard zeigen pro Eintrag ein kombiniertes Badge:

- `🔒 statisch`: Das Gerät hat die IP selbst fest konfiguriert
  (`NewAddressSource=Static`).
- `🔄 DHCP`: DHCP ohne dauerhafte Zuweisung.
- `📌 DHCP`: DHCP, aber die FRITZ!Box hat „IPv4-Adresse dauerhaft zuweisen“
  aktiviert.
- `–`: keine Zuweisungsdaten bekannt.

Die Zuweisungsart kommt aus TR-064 (`NewAddressSource`), das
Reservierungs-Flag `static_dhcp` aus der FRITZ!Box-internen `query.lua`.
Diese Route ist undokumentiert und kann sich mit FRITZ!OS ändern; schlägt der
Abruf fehl, bleibt die Reservierung unbekannt und der Rest des Abrufs läuft
weiter. Die dabei verwendete SID wird nicht gespeichert und nicht geloggt.
`/hosts` hat einen Filter für die Zuweisungsart; `/api/hosts` und `/api/dns`
enthalten `address_source` und `static_dhcp`.

## Webinterface

- `/` Dashboard: letzter Lauf, Zähler, WLAN, MultiWAN, Konflikte, Historie, Refresh
- `/topology`: Mesh-Baum und Offline-Geräte
- `/wlan`: WLAN-Radios pro Gerät, WLAN-Clients inkl. Signal und
  Auffälligkeiten aus dem Ereignisprotokoll (`?all=1` zeigt alle WLAN-Events)
- `/hosts`: alle bekannten Hosts, Suche, Status- und Quellen-Filter
- `/dns`: Records mit Quelle, Konflikte, Serial, Corefile, Downloads
- `/history`: Snapshots und Fehler
- `/api/status`, `/api/topology`, `/api/wlan`, `/api/hosts`, `/api/dns`,
  `/api/snapshots`, `/api/snapshots/{id}`
- `/api/wlan` enthält zusätzlich `events`, `event_summary` und `event_days`
- `POST /api/refresh`: manueller Abruf
- `/health`: Healthcheck für Docker

Das Webinterface hat keine eigene Authentifizierung. Es ist für den Betrieb
hinter einem Reverse Proxy mit Authentifizierung gedacht. `WEB_HOST=0.0.0.0`
ist der Default für den Container.

## Daten und Verzeichnisse

```text
db/fritz_lan_dns.db          SQLite mit Snapshots, Hosts, DNS-Records, HTTP-Status
data/raw/                    Rohdaten der letzten Abrufe
dns/hosts                    CoreDNS-hosts-Datei
dns/<dns-domain>.zone        Zone-Datei für Export/Diagnose
dns/Corefile                 generierte CoreDNS-Konfiguration
config/device-names.conf     optionale private Gerätebeschreibungen
```

Konflikte (mehrere Hostnamen auf derselben IP oder mehrere IPs für denselben
Hostnamen) werden in der Datenbank und im Web markiert. Nur der Gewinner wird
in `dns/hosts` geschrieben; aktive Hosts haben Vorrang vor inaktiven.

## Keine privaten Daten im Repository

Nicht versioniert werden:

- `.env` mit Zugangsdaten, echter Domain und echten IPs
- `config/device-names.conf` mit privaten Gerätenamen
- `data/`, `db/`, `dns/` mit Laufzeitdaten
- `.venv/` und Cache-Verzeichnisse

Öffentlich sind nur generische Beispiele mit `example.test` und
`192.0.2.x` (reservierte Testdomain und Dokumentations-IPs).

Vor dem ersten Push prüfen:

```bash
git ls-files | grep -E '(^|/)\.env$|device-names\.conf$'
git grep -nE '192\.168\.'
```

Der erste Befehl muss leer bleiben. Der zweite Befehl darf keine Treffer für
das eigene Netz liefern; passe das Suchmuster bei Bedarf an.

## Tests

```bash
.venv/bin/pytest
.venv/bin/ruff check .
```
