# Data

Raw files live in `data/raw/` and are never committed (`.gitignore`, plus a pre-commit
check that rejects files over 1 MB). Everything here can be fetched again with the
commands below.

## Sources

| Source | What | Format | How it is fetched | Terms |
|---|---|---|---|---|
| [MarineCadastre AIS](https://hub.marinecadastre.gov/) (NOAA Office for Coastal Management / BOEM) | Vessel positions in US waters, filtered to one report per vessel per minute | One zstd-compressed CSV per day, about 155–290 MB, `https://noaaocm.blob.core.windows.net/ais/csv2/csv2025/ais-YYYY-MM-DD.csv.zst` | `oversee-etl download-ais` or `oversee-etl run` | US government data, public domain; credit NOAA/BOEM |
| [World Port Index](https://msi.nga.mil/Publications/WPI) (NGA, Pub. 150) | About 3,800 ports with location, size and facilities | CSV from `https://msi.nga.mil/api/publications/world-port-index?output=csv`; coordinates as `30°20'00"N` | `oversee-etl ports` or `oversee-etl run` | US government data, public domain; credit NGA |
| [AISStream.io](https://aisstream.io) | Live AIS messages from a volunteer receiver network | WebSocket JSON | `oversee-etl stream` | Free with a GitHub sign-in; no uptime guarantee, no replay; no data licence is stated, so treat as attribution required and check before publishing |

## Columns used

**MarineCadastre (csv2, 2024 onwards):** `mmsi, base_date_time, latitude, longitude, sog,
cog, heading, vessel_name, imo, call_sign, vessel_type, status, length, width` are mapped
to the internal schema in `src/oversee/etl/transform.py` (`AIS_COLUMNS`). `draft`, `cargo`
and `transceiver` are not used yet.

**World Port Index:** `portNumber, portName, countryCode, unloCode, latitude, longitude,
harborSize` (`WPI_COLUMNS`). Harbor size codes V/S/M/L become very small/small/medium/large.

**AISStream:** `PositionReport`, `StandardClassBPositionReport`,
`ExtendedClassBPositionReport` and `ShipStaticData` messages (`src/oversee/etl/stream.py`).

## Cleaning rules

Rows dropped per rule are stored per run in `data_quality_log`:

| Rule | Meaning |
|---|---|
| `missing_required` | No MMSI, timestamp, latitude or longitude |
| `invalid_mmsi` | MMSI is not 9 digits |
| `invalid_position` | Latitude/longitude out of range (AIS uses 91/181 for "not available") |
| `outside_region` | Outside the `--region` bounding box |
| `sog_over_limit` | Speed over ground above 50 kn |
| `duplicate` | Same MMSI and timestamp seen before |
| `implied_speed` | A single GPS spike: over 60 kn from the previous report and onward to the next |

Heading 511 and SOG ≥ 102.3 are AIS "not available" values and become NULL (not dropped).

## Live vs batch

The batch files have about one report per vessel per minute and are complete for US
waters. The live feed depends on which volunteer receivers are online; a 3-minute test
over Los Angeles / Long Beach gave about one report per vessel. Use the batch window for
port-call statistics and the live feed for "where are ships now".
