select
    mmsi,
    ts,
    latitude,
    longitude,
    sog,
    cog,
    heading,
    nav_status,
    geom,
    run_id
from {{ source('oversee', 'vessel_positions') }}
