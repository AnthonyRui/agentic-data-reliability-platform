select 1
where (select count(*) from {{ ref('fact_sales') }})
    <> (select count(*) from {{ ref('stg_orders') }})
