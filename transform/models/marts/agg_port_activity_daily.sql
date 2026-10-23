{{ config(indexes=[{'columns': ['port_id', 'activity_date'], 'unique': True}]) }}

-- Per port and UTC day: port calls starting (arrivals) and ending (departures), every
-- vessel present at some point that day, and the average stay of calls arriving that
-- day. An open call runs to that vessel's own last report, never to the newest report
-- in the table: live data can be months newer than a batch window.

with last_seen as (
    select mmsi, max(ts) as ts
    from {{ ref('stg_positions') }}
    group by mmsi
),

presence as (
    select
        c.port_id,
        cast(d as date) as activity_date,
        count(distinct c.mmsi) as unique_vessels
    from {{ ref('fct_port_calls') }} as c
    inner join last_seen as s using (mmsi)
    cross join lateral generate_series(
        date_trunc('day', c.arrival_ts at time zone 'UTC'),
        date_trunc('day', coalesce(c.departure_ts, s.ts) at time zone 'UTC'),
        interval '1 day'
    ) as d
    group by 1, 2
),

arrivals as (
    select
        port_id,
        cast(arrival_ts at time zone 'UTC' as date) as activity_date,
        count(*) as n,
        avg(dwell_hours) as avg_dwell_hours
    from {{ ref('fct_port_calls') }}
    group by 1, 2
),

departures as (
    select
        port_id,
        cast(departure_ts at time zone 'UTC' as date) as activity_date,
        count(*) as n
    from {{ ref('fct_port_calls') }}
    where departure_ts is not null
    group by 1, 2
)

select
    p.port_id,
    p.activity_date,
    cast(coalesce(a.n, 0) as integer) as arrivals,
    cast(coalesce(d.n, 0) as integer) as departures,
    cast(p.unique_vessels as integer) as unique_vessels,
    a.avg_dwell_hours
from presence as p
left join arrivals as a using (port_id, activity_date)
left join departures as d using (port_id, activity_date)
