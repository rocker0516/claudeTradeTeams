"""Shared Bybit V5 adapter infrastructure.

Sits parallel to venues/ and market_data/ and depends only on domain, so
both the private Venue adapter (venues/bybit) and the public
LiveMarketDataSource (market_data) can decode Bybit's wire format through
`trading.bybit.wire` without depending on each other.
"""
