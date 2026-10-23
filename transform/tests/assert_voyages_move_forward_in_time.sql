-- A voyage arrives after it departs, between two different ports.
select *
from {{ ref('fct_voyages') }}
where arrival_ts <= departure_ts
    or origin_port_id = destination_port_id
