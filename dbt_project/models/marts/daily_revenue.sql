select
    order_date,
    count(*) as orders,
    count(*) filter (where status = 'completed') as completed_orders,
    count(distinct customer_id) filter (where status = 'completed') as customers,
    coalesce(sum(order_amount) filter (where status = 'completed'), 0)::numeric(18, 2) as revenue_usd,
    (sum(order_amount) filter (where status = 'completed')
        / nullif(count(*) filter (where status = 'completed'), 0))::numeric(18, 2) as aov_usd
from {{ ref('fact_sales') }}
group by order_date
