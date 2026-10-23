{{ config(indexes=[{'columns': ['port_id']}, {'columns': ['mmsi', 'arrival_ts']}]) }}

-- One row per stay of a vessel at a port (gaps and islands over in-port reports).
-- Consecutive in-port reports at the same port form one call unless separated by more
-- than port_call_max_gap_hours. Calls shorter than port_call_min_dwell_minutes are
-- dropped. A call whose last in-port report is also the vessel's last report overall is
-- still open, so departure_ts is null.

with flagged as (
    select
        mmsi,
        ts,
        port_id,
        case
            when lag(port_id) over w is distinct from port_id
                or ts - lag(ts) over w
                > cast({{ var('port_call_max_gap_hours') }} as double precision) * interval '1 hour'
                then 1
            else 0
        end as new_call
    from {{ ref('int_in_port_reports') }}
    window w as (partition by mmsi order by ts)
),

numbered as (
    select
        mmsi,
        ts,
        port_id,
        sum(new_call) over (partition by mmsi order by ts) as call_no
    from flagged
),

calls as (
    select
        mmsi,
        port_id,
        min(ts) as arrival_ts,
        max(ts) as last_in_port_ts
    from numbered
    group by mmsi, port_id, call_no
),

last_seen as (
    select mmsi, max(ts) as last_ts
    from {{ ref('stg_positions') }}
    group by mmsi
),

closed as (
    select
        c.mmsi,
        c.port_id,
        c.arrival_ts,
        case when c.last_in_port_ts < s.last_ts then c.last_in_port_ts end as departure_ts
    from calls as c
    inner join last_seen as s using (mmsi)
    where c.last_in_port_ts - c.arrival_ts
        >= cast({{ var('port_call_min_dwell_minutes') }} as double precision) * interval '1 minute'
)

select
    row_number() over (order by mmsi, arrival_ts, port_id) as port_call_id,
    mmsi,
    port_id,
    arrival_ts,
    departure_ts,
    extract(epoch from departure_ts - arrival_ts) / 3600 as dwell_hours
from closed
