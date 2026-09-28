-- Keep failed lookups visible. Relationship tests reject missing dimension keys.
select o.*, c.region, c.segment, p.category, p.unit_cost
from {{ ref('stg_orders') }} o
left join {{ ref('stg_customers') }} c on o.customer_id = c.customer_id
left join {{ ref('stg_products') }} p on o.product_id = p.product_id
