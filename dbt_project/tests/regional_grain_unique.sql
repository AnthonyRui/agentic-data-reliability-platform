select order_date, region
from {{ ref('regional_revenue') }}
group by order_date, region
having count(*) <> 1
