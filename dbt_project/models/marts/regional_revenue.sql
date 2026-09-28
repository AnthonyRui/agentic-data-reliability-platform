select
    order_date,
    region,
    count(*) as orders,
    count(*) filter (where status = 'completed') as completed_orders,
    coalesce(sum(order_amount) filter (where status = 'completed'), 0)::numeric(18, 2) as revenue_usd
from {{ ref('fact_sales') }}
group by order_date, region
