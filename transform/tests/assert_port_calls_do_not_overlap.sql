-- A vessel cannot be in two port calls at the same time.
select a.mmsi, a.port_call_id, b.port_call_id as overlapping_call_id
from {{ ref('fct_port_calls') }} as a
inner join {{ ref('fct_port_calls') }} as b
    on a.mmsi = b.mmsi
    and a.port_call_id < b.port_call_id
    and b.arrival_ts < coalesce(a.departure_ts, 'infinity')
    and a.arrival_ts < coalesce(b.departure_ts, 'infinity')
