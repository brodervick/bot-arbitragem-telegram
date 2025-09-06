# ── Config por ENV ──
TOKEN = os.getenv("TELEGRAM_TOKEN")
NETWORK = os.getenv("NETWORK", "ethereum")
THRESHOLD = float(os.getenv("THRESHOLD", "0.80"))
INTERVAL_SEC = int(os.getenv("INTERVAL_SEC", "90"))

# Lista padrão (fallback). Será sobrescrita se TOKENS for definido.
DEFAULT_TOKENS = [
    "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",  # USDC
    "0xdAC17F958D2ee523a2206206994597C13D831ec7",  # USDT
    "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",  # WETH
]

# Se existir a variável de ambiente TOKENS, ela substitui a lista padrão.
_ENV_TOKENS = os.getenv("TOKENS")  # CSV com endereços
if _ENV_TOKENS:
    DEFAULT_TOKENS = [t.strip() for t in _ENV_TOKENS.split(",") if t.strip()]
