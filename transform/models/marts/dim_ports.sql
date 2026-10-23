-- Ports with their activity totals over the whole data window.

with calls as (
    select
        port_id,
        count(*) as port_calls,
        count(distinct mmsi) as unique_vessels,
        avg(dwell_hours) as avg_dwell_hours
    from {{ ref('fct_port_calls') }}
    group by port_id
)

select
    p.port_id,
    p.wpi_number,
    p.name,
    p.country_code,
    p.unlocode,
    p.harbor_size,
    p.latitude,
    p.longitude,
    coalesce(c.port_calls, 0) as port_calls,
    coalesce(c.unique_vessels, 0) as unique_vessels,
    c.avg_dwell_hours,
    c.port_id is not null as has_activity
from {{ ref('stg_ports') }} as p
left join calls as c using (port_id)
