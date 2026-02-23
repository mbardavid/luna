from nautilus_trader.core.datetime import unix_nanos_to_dt
from nautilus_trader.model.identifiers import InstrumentId, Venue
from nautilus_trader.model.data import QuoteTick

def test_nautilus():
    venue = Venue("BINANCE")
    instrument_id = InstrumentId(venue, "BTCUSDT")
    print(f"Instrument ID created: {instrument_id}")
    
    quote = QuoteTick(
        instrument_id=instrument_id,
        ts_event=1700000000000000000,
        ts_init=1700000000000000000,
        bid=50000.0,
        ask=50001.0,
        bid_size=1.0,
        ask_size=1.0
    )
    print(f"Quote Tick created: {quote}")
    print(f"Timestamp: {unix_nanos_to_dt(quote.ts_event)}")
    print("NautilusTrader Core is working correctly.")

if __name__ == "__main__":
    test_nautilus()
