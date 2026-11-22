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
                        └── bei MISS: foo.<dns-domain> -> foo. -> FRITZ!Box
```

CoreDNS beantwortet ausschließlich `<dns-domain>`. Anfragen für andere Namen
(z. B. `google.com`) werden mit `REFUSED` beantwortet; der Container ist damit
kein offener Resolver. Die Antworten der Stufe 2 werden auf den ursprünglichen
Owner-Namen `foo.<dns-domain>.` zurückgeschrieben.

## Funktionen

- Stündlicher Abruf per eingebautem Scheduler
- Mesh-Topologie inkl. LAN/WLAN/PLC-Verbindungen und PLC-Raten
- Alle bekannten FRITZ!Box-Hosts (aktiv und offline)
- Optional: zweite Host-Quelle OpenWrt (DHCP-Leases + statische Hosts) via ubus
- Webinterface für Dashboard, Topologie, Hosts, DNS und Historie
- DNS-Records aus Hostnamen und IPs, inkl. Konflikt-Erkennung und Quellenangabe
- `dns/hosts` für CoreDNS, `dns/<dns-domain>.zone` als Export
- MISS-Fallback: unbekannte `foo.<dns-domain>` werden als `foo.` an die
  FRITZ!Box gefragt; Antworten werden auf `foo.<dns-domain>.` zurückgeschrieben
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

# MISS-Fallback direkt zur FRITZ!Box
DNS_FALLBACK_ENABLED=1
DNS_FALLBACK_MODE=bare
DNS_FALLBACK_UPSTREAM=192.0.2.1:53

# Optional: A-Record für ns1.<dns-domain>
DNS_NS_IP=192.0.2.10

SNAPSHOT_RETENTION_DAYS=90
RAW_RETENTION_DAYS=30

# Optional: OpenWrt als zweite Host-Quelle
OPENWRT_ENABLED=0
OPENWRT_URL=http://192.0.2.3
OPENWRT_USER=ubusro
OPENWRT_PASSWORD=openwrt-password
OPENWRT_TIMEOUT=25
OPENWRT_VERIFY_TLS=0
```

`DNS_FALLBACK_MODE=bare` fragt bei einem MISS den nackten Hostnamen `foo` bei
der FRITZ!Box an. `DNS_FALLBACK_MODE=fritz.box` würde stattdessen
`foo.fritz.box` fragen.

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
foo.<dns-domain> -> rewrite -> foo. -> FRITZ!Box <fritzbox-ip>:53
```

Die Antwort wird per `answer name` wieder auf den ursprünglichen Owner-Namen
`foo.<dns-domain>.` zurückgeschrieben. Ohne diese Rückumschreibung würde die
Antwort `foo.` als Owner tragen; viele Resolver (u. a. dnsmasq in Pi-hole)
verwerfen eine solche Abweichung zwischen Frage und Antwort.

Voraussetzung ist, dass die FRITZ!Box nackte Hostnamen per DNS beantwortet:

```bash
dig @<fritzbox-ip> foo
```

Wenn das nicht funktioniert, kann `DNS_FALLBACK_MODE=fritz.box` verwendet
werden. Dann fragt CoreDNS `foo.fritz.box.` und schreibt die Antwort ebenfalls
auf `foo.<dns-domain>.` zurück.

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

Unbekannte Namen werden in Stufe 2 als nackte Single-Label-Namen (`foo.`) an die
FRITZ!Box geschickt. Ob die FRITZ!Box solche Namen an ihren externen Upstream
weiterreicht, ist nicht pauschal garantiert und muss pro Installation geprüft
werden.

Test (auf dem Docker-Host ausführen, nicht aus dieser Dev-VM):

```bash
dig @<docker-host> zzz-leaktest-12345.<dns-domain>
sudo tcpdump -ni any port 53 and not host 127.0.0.1
```

Zusätzlich im Pi-hole-Log bzw. auf dem Upstream der FRITZ!Box prüfen, ob
`zzz-leaktest-12345` dort ankommt.

Status: **unverifiziert**. Dieser Test benötigt Zugriff auf das echte LAN und
die FRITZ!Box und wurde im Rahmen der Umstellung nicht ausgeführt. Falls sich
ein Leck zeigt, wäre ein Allowlist-Schutz möglich (Stufe 2 nur für bereits
bekannte Namen aus dem letzten Sync). Das würde allerdings die Live-Erkennung
neuer, noch nicht gecachter Hosts abschalten und ist daher eine bewusste
Abwägung.

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
Oberfläche zeigt die Spalte „Quelle“ den Ursprung (`FritzBox`, `OpenWrt`,
`FritzBox Mesh`); die Hosts-Seite hat zusätzlich einen Quellen-Filter.
Schlägt der OpenWrt-Abruf fehl, bleibt der FRITZ!Box-Abruf erfolgreich und der
Fehler wird als Warnung im Snapshot vermerkt.

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

## Webinterface

- `/` Dashboard: letzter Lauf, Zähler, Konflikte, Historie, Refresh
- `/topology`: Mesh-Baum und Offline-Geräte
- `/hosts`: alle bekannten Hosts, Suche, Status- und Quellen-Filter
- `/dns`: Records mit Quelle, Konflikte, Serial, Corefile, Downloads
- `/history`: Snapshots und Fehler
- `/api/status`, `/api/topology`, `/api/hosts`, `/api/dns`,
  `/api/snapshots`, `/api/snapshots/{id}`
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
