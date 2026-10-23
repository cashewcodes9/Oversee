-- Voyages per origin, destination and UTC departure day, with the average trip time.

select
    origin_port_id,
    destination_port_id,
    cast(departure_ts at time zone 'UTC' as date) as summary_date,
    cast(count(*) as integer) as voyages,
    avg(duration_hours) as avg_duration_hours
from {{ ref('fct_voyages') }}
group by 1, 2, 3
