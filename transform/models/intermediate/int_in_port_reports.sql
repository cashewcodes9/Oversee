-- AIS reports from vessels that are stationary near a port. A report counts as in port
-- when its speed is at most port_call_max_sog_knots within port_call_radius_m of a port;
-- when several ports are in range, the nearest one wins.

select
    p.mmsi,
    p.ts,
    nearest.port_id
from {{ ref('stg_positions') }} as p
cross join lateral (
    select pt.port_id
    from {{ ref('stg_ports') }} as pt
    where st_dwithin(pt.geom, p.geom, cast({{ var('port_call_radius_m') }} as double precision))
    order by st_distance(pt.geom, p.geom)
    limit 1
) as nearest
where p.sog <= cast({{ var('port_call_max_sog_knots') }} as real)
