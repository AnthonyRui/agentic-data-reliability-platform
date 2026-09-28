select customer_id, trim(region) as region, signup_ts, lower(trim(segment)) as segment
from {{ source('raw', 'raw_customers') }}
