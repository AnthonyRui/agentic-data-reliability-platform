select product_id, lower(trim(category)) as category, unit_cost
from {{ source('raw', 'raw_products') }}
