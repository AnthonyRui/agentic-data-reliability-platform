select
    order_id,
    customer_id,
    product_id,
    order_ts,
    (order_ts at time zone 'UTC')::date as order_date,
    amount::numeric(12, 2) as order_amount,
    upper(trim(currency)) as currency,
    lower(trim(status)) as status
from {{ source('raw', 'raw_orders') }}
