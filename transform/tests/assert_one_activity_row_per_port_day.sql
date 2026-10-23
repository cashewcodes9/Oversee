select port_id, activity_date, count(*) as rows_for_day
from {{ ref('agg_port_activity_daily') }}
group by port_id, activity_date
having count(*) > 1
