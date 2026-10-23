select
    port_id,
    wpi_number,
    name,
    country_code,
    unlocode,
    harbor_size,
    latitude,
    longitude,
    geom
from {{ source('oversee', 'ports') }}
