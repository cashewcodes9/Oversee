select
    mmsi,
    imo,
    name,
    call_sign,
    vessel_type,
    vessel_group,
    length_m,
    width_m
from {{ source('oversee', 'vessels') }}
