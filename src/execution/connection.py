"""IBKR connection management."""

from ib_async import IB


def initiate_ib(args=None, client_id: int = 14, real: bool = True, port: int = None, timeout: int = 15) -> IB:
    """Connect to Interactive Brokers TWS or IB Gateway."""
    ib = IB()
    if args is not None:
        real = getattr(args, "real", real)
        client_id = getattr(args, "clientid", client_id)
    
    if port is None:
        port = 4001 if real else 4002
        
    ib.connect("127.0.0.1", port, clientId=client_id, timeout=timeout)
    return ib


def safe_disconnect(ib: IB, timeout: float = 0.3):
    """Cleanly disconnects an IB connection."""
    if ib is not None:
        try:
            while ib.isConnected():
                ib.disconnect()
                ib.waitOnUpdate(timeout=timeout)
        except Exception as e:
            print(f"Error during disconnect: {e}")
