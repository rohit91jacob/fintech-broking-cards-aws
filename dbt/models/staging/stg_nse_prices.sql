-- End-of-day prices for equity (EQ series) stocks; SYNTHETIC by default (raw_nse_bhavcopy).
select
    trade_date,
    symbol,
    isin,
    instrument_name,
    "open" as open_price,
    high as high_price,
    low as low_price,
    "close" as close_price,
    prev_close,
    volume,
    turnover_inr,
    turnover_inr / nullif(volume, 0) as vwap,
    trades
from {{ source('lake', 'raw_nse_bhavcopy') }}
where series = 'EQ'
