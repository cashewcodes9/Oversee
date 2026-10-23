{{ config(indexes=[{'columns': ['mmsi']}]) }}

-- A voyage is a vessel's move from one completed port call to its next call at a
-- different port.

with consecutive as (
    select
        mmsi,
        port_id,
        arrival_ts,
        lag(port_id) over w as prev_port_id,
        lag(departure_ts) over w as prev_departure_ts
    from {{ ref('fct_port_calls') }}
    window w as (partition by mmsi order by arrival_ts)
)

select
    row_number() over (order by mmsi, prev_departure_ts) as voyage_id,
    mmsi,
    prev_port_id as origin_port_id,
    port_id as destination_port_id,
    prev_departure_ts as departure_ts,
    arrival_ts,
    extract(epoch from arrival_ts - prev_departure_ts) / 3600 as duration_hours
from consecutive
where prev_port_id is not null
    and prev_port_id <> port_id
    and prev_departure_ts is not null
