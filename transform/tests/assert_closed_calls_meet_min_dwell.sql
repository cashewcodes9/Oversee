-- A closed port call must last at least the minimum dwell time.
select *
from {{ ref('fct_port_calls') }}
where departure_ts is not null
    and departure_ts - arrival_ts
    < cast({{ var('port_call_min_dwell_minutes') }} as double precision) * interval '1 minute'
