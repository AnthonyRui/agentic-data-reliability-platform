with regional as (
    select order_date, sum(orders) as orders, sum(revenue_usd) as revenue_usd
    from {{ ref('regional_revenue') }} group by order_date
)
select coalesce(r.order_date, d.order_date) as order_date
from regional r full outer join {{ ref('daily_revenue') }} d using (order_date)
where r.orders is distinct from d.orders or r.revenue_usd is distinct from d.revenue_usd
