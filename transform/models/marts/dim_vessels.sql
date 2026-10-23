-- Vessels with their reporting window and port-call count.

with reports as (
    select mmsi, min(ts) as first_seen_ts, max(ts) as last_seen_ts, count(*) as positions
    from {{ ref('stg_positions') }}
    group by mmsi
),

calls as (
    select mmsi, count(*) as port_calls
    from {{ ref('fct_port_calls') }}
    group by mmsi
)

select
    v.mmsi,
    v.imo,
    v.name,
    v.call_sign,
    v.vessel_type,
    v.vessel_group,
    v.length_m,
    v.width_m,
    r.first_seen_ts,
    r.last_seen_ts,
    coalesce(r.positions, 0) as positions,
    coalesce(c.port_calls, 0) as port_calls
from {{ ref('stg_vessels') }} as v
left join reports as r using (mmsi)
left join calls as c using (mmsi)
